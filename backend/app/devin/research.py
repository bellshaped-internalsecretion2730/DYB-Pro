"""Devin sessions for the research daemon: one long-lived supervisor plus event-driven children.

Unlike the design cycle, the daemon's supervisor session outlives every individual event: it is
created once per campaign, kept in :class:`~app.models.ResearchProject`, and messaged on each event.

If Devin is unreachable the work is *queued and reported as degraded* — never presented as
completed research. ``local-simulation`` means the deterministic skill-driven passes ran without a
Devin agent, and every row says so.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.devin.client import DevinAPIError, DevinClient, DevinNotConfigured, SessionState
from app.devin.runner import PROVIDER_DEVIN, PROVIDER_SIM, reconcile_playbooks, resolve_provider
from app.devin.schemas import schema_for
from app.models import ResearchProject, utcnow

logger = logging.getLogger(__name__)

TAG_PREFIX = "foldsmith"

CHILD_ROLES = (
    "research-literature",
    "research-metrics",
    "wetlab-planner",
    "learn-from-results",
    "insight-reporter",
)


def daemon_tags(rp: ResearchProject, role: str) -> list[str]:
    return [
        TAG_PREFIX,
        f"{TAG_PREFIX}:daemon",
        f"{TAG_PREFIX}:role:{role}",
        f"{TAG_PREFIX}:campaign:{rp.id[:12]}",
        f"{TAG_PREFIX}:project:{rp.project_id[:12]}",
    ]


@dataclass
class ChildResult:
    role: str
    status: str  # finished | failed | timeout | queued | unavailable
    output: dict | None = None
    session_id: str | None = None
    session_url: str | None = None
    error: str = ""
    acus: float = 0.0
    logs: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status == "finished" and bool(self.output)

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "status": self.status,
            "output": self.output,
            "devin_session_id": self.session_id,
            "devin_session_url": self.session_url,
            "error": self.error,
            "acus": self.acus,
        }


class ResearchAgents:
    """Devin fan-out for one campaign. Degrades loudly, never silently invents research."""

    def __init__(
        self,
        db: Session,
        rp: ResearchProject,
        settings: Settings | None = None,
        client: DevinClient | None = None,
    ) -> None:
        self.db = db
        self.rp = rp
        self.settings = settings or get_settings()
        self._client = client
        self.playbooks: dict[str, str] = {}
        self.degraded_reason = ""
        try:
            self.provider = resolve_provider(self.settings)
        except Exception as exc:  # no Devin and simulation disallowed
            self.provider = "unavailable"
            self.degraded_reason = str(exc)[:400]
            return
        if self.provider == PROVIDER_DEVIN:
            try:
                self._client = client or DevinClient(self.settings)
                self.playbooks = reconcile_playbooks(self.db, self._client)
            except (DevinAPIError, DevinNotConfigured, OSError) as exc:
                self.provider = PROVIDER_SIM if self.settings.allow_local_simulation else "unavailable"
                self.degraded_reason = f"Devin unreachable: {exc}"[:400]
                logger.warning("daemon falling back from Devin: %s", exc)

    # ------------------------------------------------------------------ basics

    @property
    def devin(self) -> bool:
        return self.provider == PROVIDER_DEVIN and self._client is not None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -------------------------------------------------------------- supervisor

    def ensure_supervisor(self, brief: str) -> dict:
        """Create (once) or reuse the long-lived supervisor session for this campaign."""
        state = {
            "provider": self.provider,
            "session_id": self.rp.daemon_session_id,
            "session_url": self.rp.daemon_session_url,
            "degraded_reason": self.degraded_reason,
        }
        if not self.devin:
            return state
        assert self._client is not None
        if self.rp.daemon_session_id:
            try:
                live = self._client.get_session(self.rp.daemon_session_id)
                if not live.is_terminal:
                    state.update(session_id=live.session_id, session_url=live.url, reused=True)
                    return state
            except DevinAPIError as exc:
                logger.warning("supervisor session %s unreadable: %s", self.rp.daemon_session_id, exc)
        try:
            created = self._client.create_session(
                brief,
                title=f"Foldsmith research daemon — {self.rp.name}"[:120],
                playbook_id=self.playbooks.get("research-daemon"),
                tags=daemon_tags(self.rp, "research-daemon"),
                max_acu_limit=self.settings.devin_orchestrator_acu_limit,
                structured_output_schema=schema_for("research-daemon"),
            )
        except DevinAPIError as exc:
            self.degraded_reason = f"supervisor session could not be created: {exc}"[:400]
            state["degraded_reason"] = self.degraded_reason
            return state
        self.rp.daemon_session_id = created.session_id
        self.rp.daemon_session_url = created.url
        self.db.flush()
        state.update(session_id=created.session_id, session_url=created.url, reused=False)
        return state

    def notify_supervisor(self, message: str) -> bool:
        """Push an event onto the live supervisor session. False when it could not be delivered."""
        if not self.devin or not self.rp.daemon_session_id:
            return False
        assert self._client is not None
        try:
            self._client.send_message(self.rp.daemon_session_id, message[:12000])
            return True
        except DevinAPIError as exc:
            self.degraded_reason = f"supervisor message failed: {exc}"[:400]
            return False

    # ------------------------------------------------------------------ children

    def child(
        self,
        role: str,
        prompt: str,
        *,
        acu_limit: int | None = None,
        timeout: float | None = None,
        sleep=time.sleep,
    ) -> ChildResult:
        """Run one child role to structured output, or report exactly why it did not run."""
        if role not in CHILD_ROLES:
            raise ValueError(f"unknown research child role: {role}")
        if not self.devin:
            return ChildResult(
                role=role,
                status="unavailable" if self.provider == "unavailable" else "queued",
                error=self.degraded_reason
                or "Devin is not configured; the deterministic skill pass ran instead",
            )
        assert self._client is not None
        try:
            created = self._client.create_session(
                prompt,
                title=f"Foldsmith {role} — {self.rp.name}"[:120],
                playbook_id=self.playbooks.get(role),
                tags=daemon_tags(self.rp, role),
                max_acu_limit=acu_limit or self.settings.daemon_child_acu_limit,
                structured_output_schema=schema_for(role),
                parent_session_id=self.rp.daemon_session_id,
            )
        except DevinAPIError as exc:
            return ChildResult(role=role, status="failed", error=str(exc)[:600])
        result = ChildResult(
            role=role, status="running", session_id=created.session_id, session_url=created.url
        )
        deadline = time.monotonic() + (timeout or self.settings.daemon_child_timeout_seconds)
        interval = self.settings.devin_poll_interval_seconds
        while time.monotonic() < deadline:
            try:
                live: SessionState = self._client.get_session(created.session_id)
            except DevinAPIError as exc:
                result.logs.append(f"poll failed: {exc}"[:300])
                sleep(interval)
                continue
            result.acus = live.acus_consumed
            if live.structured_output:
                result.output = live.structured_output
                result.status = "finished"
                return result
            if live.is_terminal:
                result.status = "failed"
                result.error = f"session ended in status {live.status} without structured output"
                return result
            sleep(interval)
        budget = timeout or self.settings.daemon_child_timeout_seconds
        result.status = "timeout"
        result.error = f"no structured output within {budget:.0f}s"
        with contextlib.suppress(DevinAPIError):
            self._client.cancel_session(created.session_id, "daemon child timed out")
        return result

    def cancel_supervisor(self, reason: str = "campaign paused by scientist") -> bool:
        if not self.devin or not self.rp.daemon_session_id:
            return False
        assert self._client is not None
        try:
            self._client.cancel_session(self.rp.daemon_session_id, reason)
        except DevinAPIError as exc:
            logger.warning("supervisor cancel failed: %s", exc)
            return False
        self.rp.daemon_session_id = None
        self.rp.daemon_session_url = None
        self.rp.daemon_status = "paused"
        self.rp.daemon_detail = reason
        self.rp.heartbeat_at = utcnow()
        self.db.flush()
        return True


# ------------------------------------------------------------------- prompts


def supervisor_brief(rp: ResearchProject, digest_text: str) -> str:
    return (
        f"You are the always-on Foldsmith research daemon for campaign '{rp.name}'.\n"
        f"Research question: {rp.question or 'improve wet-lab success and shrink in-silico drift'}\n\n"
        "Foldsmith will message you on every campaign event (new version, diff, label, wet-lab "
        "result). For each event decide what changed and which child roles are worth spawning.\n\n"
        "Current merged knowledge base (cached research plus the whole version path):\n"
        f"{digest_text}\n"
    )


def event_message(kind: str, summary: str, digest_text: str) -> str:
    return (
        f"EVENT: {kind}\n{summary}\n\n"
        "Merged knowledge base after this event:\n"
        f"{digest_text}\n\n"
        "Reply with structured output: what changed and which children to spawn."
    )


def literature_prompt(digest_text: str, corpus: list[dict], queries: list[str]) -> str:
    lines = [
        "Interpret the campaign's cached literature for the current design.",
        "",
        "Queries that produced this corpus:",
        *[f"  - {q}" for q in queries],
        "",
        "Cached corpus (title | year | doi | extracted numbers):",
    ]
    for paper in corpus[:20]:
        nums = ", ".join(
            f"{m.get('name')}={m.get('value')}{m.get('unit', '')}"
            for m in (paper.get("extracted_metrics") or [])[:4]
        )
        lines.append(
            f"  - {paper.get('title', '')[:160]} | {paper.get('year')} | "
            f"{paper.get('doi') or 'no doi'} | {nums or 'no numbers extracted'}"
        )
    lines += ["", "Campaign knowledge base:", digest_text]
    return "\n".join(lines)


def metrics_prompt(digest_text: str, metrics: dict, measured: dict) -> str:
    lines = ["Interpret the recomputed metrics for the current version.", "", "Metrics (skill-stamped):"]
    for name, m in list(metrics.items())[:40]:
        sd = m.get("sd")
        lines.append(
            f"  - {name} = {m.get('value')} {m.get('unit', '')}"
            f"{f' +/- {sd}' if sd else ''} [{m.get('skill')}] {m.get('method', '')[:90]}"
        )
    lines += ["", "Measured so far on this version:"]
    lines += [f"  - {k} = {v}" for k, v in (measured or {}).items()] or ["  - nothing measured yet"]
    lines += ["", "Campaign knowledge base:", digest_text]
    return "\n".join(lines)


def wetlab_prompt(digest_text: str, plan: dict) -> str:
    lines = [
        "Review and refine this wet-lab pack. It was assembled deterministically from "
        "skill outputs; your job is to justify or challenge it, not to invent numbers.",
        "",
        f"Predicted (value +/- sd): {plan.get('predictions')}",
        f"Named risks: {plan.get('risk', {}).get('risks')}",
        f"Assays chosen (ranked by information per dollar): {plan.get('assays')}",
        f"Thresholds: {plan.get('thresholds')}",
        f"Total cost: ${plan.get('total_cost_usd')}",
        "",
        "Campaign knowledge base:",
        digest_text,
    ]
    return "\n".join(lines)


def learn_prompt(digest_text: str, residuals: dict, drift: list[dict]) -> str:
    lines = ["Wet-lab results arrived. Explain the residuals and say how predictions must change.", ""]
    for metric, res in (residuals or {}).items():
        lines.append(
            f"  - {metric}: predicted {res.get('predicted')} measured {res.get('measured')} "
            f"residual {res.get('residual')} (z={res.get('z')}, assay sd {res.get('assay_sd')})"
        )
    lines += ["", "Calibration after this update:"]
    lines += [
        f"  - {d['metric']}: n={d['n']} bias={d['bias']} residual_sd={d['residual_sd']}"
        for d in drift
    ] or ["  - no calibration yet"]
    lines += ["", "Campaign knowledge base:", digest_text]
    return "\n".join(lines)


def insight_prompt(digest_text: str, learned: dict) -> str:
    lines = [
        "Write the 'what we learned v1 -> latest' view and propose exactly one next version.",
        "",
        "Deterministic lessons already derived from the ledger:",
        *[f"  - {lesson}" for lesson in learned.get("lessons", [])[:12]],
        "",
        "Drift trend:",
        *[
            f"  - {d['metric']}: n={d['n']} bias={d['bias']} residual_sd={d['residual_sd']} "
            f"shrinking={d['shrinking']}"
            for d in learned.get("drift", [])
        ],
        "",
        "Campaign knowledge base:",
        digest_text,
    ]
    return "\n".join(lines)
