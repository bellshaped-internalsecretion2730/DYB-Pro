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
        self.flavor = self.settings.devin_api_flavor
        self.org_id = self.settings.devin_org_id
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

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        response = self._client.request(method, path, **kwargs)
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

        if self.flavor == "v3":
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
        if self.flavor == "v3":
            path = f"/v3/organizations/{self.org_id}/sessions/{session_id}"
        else:
            path = f"/v1/sessions/{session_id}"
        return _normalize(self._request("GET", path), fallback_id=session_id)

    def send_message(self, session_id: str, message: str) -> dict:
        if self.flavor == "v3":
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
        if self.flavor == "v3":
            return self._request(
                "POST", f"/v3/organizations/{self.org_id}/sessions/{session_id}/archive"
            )
        return {"detail": "archive is only supported on the v3 API"}

    def list_sessions(self, tags: list[str] | None = None, limit: int = 50) -> list[dict]:
        if self.flavor == "v3":
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
        if self.flavor != "v3":
            return []
        data = self._request("GET", f"/v3/organizations/{self.org_id}/playbooks")
        return list(data.get("playbooks") or data.get("items") or [])

    def create_playbook(
        self, title: str, body: str, structured_output_schema: dict | None = None
    ) -> dict:
        if self.flavor != "v3":
            raise DevinNotConfigured("playbook management requires the v3 organization API")
        payload: dict[str, Any] = {"title": title, "body": body}
        if structured_output_schema:
            payload["structured_output_schema"] = structured_output_schema
        return self._request("POST", f"/v3/organizations/{self.org_id}/playbooks", json=payload)

    def health(self) -> dict:
        """Cheap authenticated call used by /healthz and the UI provider badge."""
        if self.flavor == "v3":
            self._request("GET", f"/v3/organizations/{self.org_id}/sessions", params={"limit": 1})
        else:
            self._request("GET", "/v1/sessions", params={"limit": 1})
        return {"ok": True, "flavor": self.flavor, "org_id": self.org_id}
