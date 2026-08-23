"""Real Devin API client.

Supports both API flavors:
  * ``v3`` — organization-scoped enterprise endpoints (``/v3/organizations/{org_id}/...``), used by
    ``cog_`` service-account keys. This is the default.
  * ``v1`` — personal-key endpoints (``/v1/sessions``).

Everything DYB Pro needs is here: create sessions with playbooks/tags/ACU limits/structured
output schemas, poll status + structured output + ACU consumption, send follow-up messages, list
child sessions, cancel (archive) sessions, and reconcile playbooks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# A key that is not an organization service account is rejected by /v3/organizations/... even
# though it works on /v1/sessions, so the resolved flavor is cached per base URL + key.
_RESOLVED_FLAVOR: dict[tuple[str, str], str] = {}

TERMINAL_STATUSES = {"exit", "error", "expired", "finished", "blocked"}
FAILED_STATUSES = {"error", "expired"}


class DevinAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: str, path: str) -> None:
        super().__init__(f"Devin API {status_code} on {path}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.path = path


class DevinNotConfigured(RuntimeError):
    pass


@dataclass
class SessionState:
    """Normalized view of a Devin session across API flavors."""

    session_id: str
    url: str
    status: str
    status_detail: str | None = None
    acus_consumed: float = 0.0
    structured_output: dict | None = None
    tags: list[str] = field(default_factory=list)
    child_session_ids: list[str] = field(default_factory=list)
    title: str | None = None
    messages: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        if self.status in TERMINAL_STATUSES:
            return True
        # A running session that reports 'finished' has completed its turn.
        return self.status_detail == "finished"

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES


def _normalize(payload: dict, fallback_id: str | None = None) -> SessionState:
    session_id = payload.get("session_id") or fallback_id or ""
    status = payload.get("status") or payload.get("status_enum") or "unknown"
    return SessionState(
        session_id=session_id,
        url=payload.get("url") or f"https://app.devin.ai/sessions/{session_id.removeprefix('devin-')}",
        status=str(status),
        status_detail=payload.get("status_detail") or payload.get("status_enum"),
        acus_consumed=float(payload.get("acus_consumed") or 0.0),
        structured_output=payload.get("structured_output"),
        tags=list(payload.get("tags") or []),
        child_session_ids=list(payload.get("child_session_ids") or []),
        title=payload.get("title"),
        messages=list(payload.get("messages") or []),
        raw=payload,
    )


class DevinClient:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.devin_enabled:
            raise DevinNotConfigured(
                "DEVIN_API_KEY (and DEVIN_ORG_ID for the v3 flavor) must be configured"
            )
        self.org_id = self.settings.devin_org_id
        self._flavor_cache_key = (
            self.settings.devin_api_base,
            (self.settings.devin_api_key or "")[-8:],
        )
        self.flavor = _RESOLVED_FLAVOR.get(
            self._flavor_cache_key, self.settings.devin_api_flavor
        )
        self._client = client or httpx.Client(
            base_url=self.settings.devin_api_base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self.settings.devin_api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.settings.devin_request_timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DevinClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- transport

    def _v1_equivalent(self, path: str) -> str | None:
        """The v1 path serving the same purpose, or None where v1 has no equivalent."""
        org_prefix = f"/v3/organizations/{self.org_id}"
        if path == f"{org_prefix}/sessions":
            return "/v1/sessions"
        if path.startswith(f"{org_prefix}/sessions/"):
            rest = path[len(f"{org_prefix}/sessions/") :]
            return f"/v1/sessions/{rest}" if "/" not in rest else None
        if path.startswith("/v3/enterprise/sessions/") and path.endswith("/messages"):
            session_id = path[len("/v3/enterprise/sessions/") : -len("/messages")]
            return f"/v1/sessions/{session_id}/message"
        return None  # playbooks, schedules and org knowledge are v3-only

    def _flavor(self) -> str:
        return _RESOLVED_FLAVOR.get(self._flavor_cache_key, self.flavor)

    def api_flavor(self) -> str:
        """The flavor actually in use, which is v1 once a non-org key has been demoted."""
        return self._flavor()

    def _demote_to_v1(self, path: str, status_code: int) -> None:
        logger.warning(
            "Devin %s on %s: this key is not organization-scoped, using the v1 API instead",
            status_code,
            path,
        )
        self.flavor = "v1"
        _RESOLVED_FLAVOR[self._flavor_cache_key] = "v1"

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        self.flavor = self._flavor()
        if self.flavor != "v3" and path.startswith("/v3/"):
            fallback = self._v1_equivalent(path)
            if fallback is None:
                raise DevinNotConfigured(f"{path} requires an organization-scoped Devin key")
            path, kwargs = fallback, {k: v for k, v in kwargs.items() if k != "params"}
        response = self._client.request(method, path, **kwargs)
        if response.status_code in (401, 403) and self.flavor == "v3" and path.startswith("/v3/"):
            # A personal key is refused by the organization endpoints but works on /v1.
            fallback = self._v1_equivalent(path)
            self._demote_to_v1(path, response.status_code)
            if fallback is None:
                raise DevinNotConfigured(f"{path} requires an organization-scoped Devin key")
            kwargs.pop("params", None)
            response = self._client.request(method, fallback, **kwargs)
            path = fallback
        if response.status_code >= 400:
            detail = response.text[:600]
            raise DevinAPIError(response.status_code, detail, path)
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {"raw": response.text}
        return data if isinstance(data, dict) else {"items": data}

    # ----------------------------------------------------------------- sessions

    def create_session(
        self,
        prompt: str,
        *,
        title: str | None = None,
        playbook_id: str | None = None,
        child_playbook_id: str | None = None,
        tags: list[str] | None = None,
        max_acu_limit: int | None = None,
        structured_output_schema: dict | None = None,
        parent_session_id: str | None = None,
        idempotent: bool = False,
        knowledge_ids: list[str] | None = None,
        secret_ids: list[str] | None = None,
    ) -> SessionState:
        body: dict[str, Any] = {"prompt": prompt}
        if title:
            body["title"] = title[:120]
        if playbook_id:
            body["playbook_id"] = playbook_id
        if tags:
            body["tags"] = tags
        if max_acu_limit:
            body["max_acu_limit"] = int(max_acu_limit)
        if structured_output_schema:
            body["structured_output_schema"] = structured_output_schema
        if knowledge_ids is not None:
            body["knowledge_ids"] = knowledge_ids
        if secret_ids is not None:
            body["secret_ids"] = secret_ids

        if self._flavor() == "v3":
            if child_playbook_id:
                body["child_playbook_id"] = child_playbook_id
            body["structured_output_required"] = bool(structured_output_schema)
            path = f"/v3/organizations/{self.org_id}/sessions"
            params = {"devin_id": parent_session_id} if parent_session_id else None
            data = self._request("POST", path, json=body, params=params)
        else:
            body["idempotent"] = idempotent
            data = self._request("POST", "/v1/sessions", json=body)
        state = _normalize(data)
        logger.info("devin session created: %s (%s)", state.session_id, state.url)
        return state

    def get_session(self, session_id: str) -> SessionState:
        if self._flavor() == "v3":
            path = f"/v3/organizations/{self.org_id}/sessions/{session_id}"
        else:
            path = f"/v1/sessions/{session_id}"
        return _normalize(self._request("GET", path), fallback_id=session_id)

    def send_message(self, session_id: str, message: str) -> dict:
        if self._flavor() == "v3":
            return self._request(
                "POST",
                f"/v3/enterprise/sessions/{session_id}/messages",
                json={"message": message},
                params={"org_id": self.org_id} if self.org_id else None,
            )
        return self._request("POST", f"/v1/sessions/{session_id}/message", json={"message": message})

    def cancel_session(self, session_id: str, reason: str = "cycle cancelled by scientist") -> dict:
        """Ask the session to stop, then archive it so it is put to sleep."""
        try:
            self.send_message(session_id, f"Stop work now: {reason}. Do not start new tasks.")
        except DevinAPIError as exc:  # already suspended/finished is fine
            logger.info("cancel message to %s failed (%s), archiving anyway", session_id, exc)
        if self._flavor() == "v3":
            return self._request(
                "POST", f"/v3/organizations/{self.org_id}/sessions/{session_id}/archive"
            )
        return {"detail": "archive is only supported on the v3 API"}

    def list_sessions(self, tags: list[str] | None = None, limit: int = 50) -> list[dict]:
        if self._flavor() == "v3":
            params: dict[str, Any] = {"limit": limit}
            if tags:
                params["tags"] = tags
            data = self._request(
                "GET", f"/v3/organizations/{self.org_id}/sessions", params=params
            )
        else:
            data = self._request("GET", "/v1/sessions", params={"limit": limit})
        return list(data.get("sessions") or data.get("items") or [])

    def child_sessions(self, session_id: str) -> list[str]:
        return self.get_session(session_id).child_session_ids

    # ---------------------------------------------------------------- playbooks

    def list_playbooks(self) -> list[dict]:
        if self._flavor() != "v3":
            return []
        data = self._request("GET", f"/v3/organizations/{self.org_id}/playbooks")
        return list(data.get("playbooks") or data.get("items") or [])

    def create_playbook(
        self, title: str, body: str, structured_output_schema: dict | None = None
    ) -> dict:
        if self._flavor() != "v3":
            raise DevinNotConfigured("playbook management requires the v3 organization API")
        payload: dict[str, Any] = {"title": title, "body": body}
        if structured_output_schema:
            payload["structured_output_schema"] = structured_output_schema
        return self._request("POST", f"/v3/organizations/{self.org_id}/playbooks", json=payload)

    # ---------------------------------------------------------------- schedules

    def list_schedules(self) -> list[dict]:
        """Scheduled sessions, used by the research daemon. v3 organization API only."""
        if self._flavor() != "v3":
            return []
        data = self._request("GET", f"/v3/organizations/{self.org_id}/schedules")
        return list(data.get("schedules") or data.get("items") or [])

    def create_schedule(
        self,
        name: str,
        prompt: str,
        *,
        frequency: str = "daily",
        interval_count: int = 1,
        playbook_id: str | None = None,
        tags: list[str] | None = None,
        notify_on: str = "failure",
    ) -> dict:
        if self._flavor() != "v3":
            raise DevinNotConfigured("schedules require the v3 organization API")
        payload: dict[str, Any] = {
            "name": name,
            "prompt": prompt,
            "schedule_type": "recurring",
            "frequency": frequency,
            "interval_count": int(interval_count),
            "notify_on": notify_on,
        }
        if playbook_id:
            payload["playbook_id"] = playbook_id
        if tags:
            payload["tags"] = tags
        return self._request("POST", f"/v3/organizations/{self.org_id}/schedules", json=payload)

    def delete_schedule(self, scheduled_session_id: str) -> dict:
        if self._flavor() != "v3":
            raise DevinNotConfigured("schedules require the v3 organization API")
        return self._request(
            "DELETE", f"/v3/organizations/{self.org_id}/schedules/{scheduled_session_id}"
        )

    # ---------------------------------------------------------------- knowledge

    def list_knowledge(self) -> list[dict]:
        """Existing org knowledge notes (v3: `/knowledge/notes`, v1: `/v1/knowledge`)."""
        if self._flavor() == "v3":
            data = self._request("GET", f"/v3/organizations/{self.org_id}/knowledge/notes")
        else:
            data = self._request("GET", "/v1/knowledge")
        return list(data.get("notes") or data.get("knowledge") or data.get("items") or [])

    def create_knowledge(self, name: str, body: str, trigger: str) -> dict:
        if self._flavor() == "v3":
            return self._request(
                "POST",
                f"/v3/organizations/{self.org_id}/knowledge/notes",
                json={"name": name, "body": body, "trigger": trigger},
            )
        return self._request(
            "POST",
            "/v1/knowledge",
            json={"name": name, "body": body, "trigger_description": trigger},
        )

    def update_knowledge(self, note_id: str, name: str, body: str, trigger: str) -> dict:
        if self._flavor() == "v3":
            return self._request(
                "PUT",
                f"/v3/organizations/{self.org_id}/knowledge/notes/{note_id}",
                json={"name": name, "body": body, "trigger": trigger},
            )
        return self._request(
            "PUT",
            f"/v1/knowledge/{note_id}",
            json={"name": name, "body": body, "trigger_description": trigger},
        )

    def health(self) -> dict:
        """Cheap authenticated call used by /healthz and the UI provider badge."""
        if self._flavor() == "v3":
            self._request("GET", f"/v3/organizations/{self.org_id}/sessions", params={"limit": 1})
        else:
            self._request("GET", "/v1/sessions", params={"limit": 1})
        return {"ok": True, "flavor": self.flavor, "org_id": self.org_id}
