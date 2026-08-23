"""Agent supervision: launch, poll, retry and cancel agent runs.

There are exactly two providers:
  * ``devin``            — real Devin sessions via :class:`DevinClient`.
  * ``local-simulation`` — deterministic offline heuristics, only when Devin is unconfigured and
                           ``ALLOW_LOCAL_SIMULATION=true``.

Both paths write the same ``AgentRun`` rows with the provider recorded, so provenance never lies.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.compute.workflow import capability_status
from app.config import Settings, get_settings
from app.devin import playbooks as pb
from app.devin import simulation
from app.devin.client import DevinAPIError, DevinClient, DevinNotConfigured, SessionState
from app.devin.prompts import tags_for
from app.devin.schemas import PLAN_SCHEMA, schema_for
from app.models import AgentRun, DesignCycle, Observation, PlaybookRef, utcnow

logger = logging.getLogger(__name__)

PROVIDER_DEVIN = "devin"
PROVIDER_SIM = simulation.PROVIDER


class ProviderUnavailable(RuntimeError):
    pass


def resolve_provider(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.devin_enabled:
        return PROVIDER_DEVIN
    if settings.allow_local_simulation:
        return PROVIDER_SIM
    raise ProviderUnavailable(
        "No Devin credentials configured and ALLOW_LOCAL_SIMULATION is false. Set DEVIN_API_KEY "
        "(and DEVIN_ORG_ID) to run real Devin agents."
    )


def provider_status(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    status: dict = {
        "devin_configured": settings.devin_enabled,
        "devin_api_flavor": settings.devin_api_flavor,
        "local_simulation_allowed": settings.allow_local_simulation,
        "openai_configured": settings.openai_enabled,
        "compute": capability_status(settings),
    }
    try:
        status["provider"] = resolve_provider(settings)
    except ProviderUnavailable as exc:
        status["provider"] = None
        status["error"] = str(exc)
    if settings.devin_enabled:
        try:
            with DevinClient(settings) as client:
                status["devin_reachable"] = bool(client.health()["ok"])
                # The health probe confirms the explicitly configured API flavor. Modern v3
                # credentials are never silently downgraded after an RBAC error.
                status["devin_api_flavor"] = client.api_flavor()
        except (DevinAPIError, DevinNotConfigured, OSError) as exc:
            status["devin_reachable"] = False
            status["devin_error"] = str(exc)[:300]
    return status


# ------------------------------------------------------------------ playbooks


def reconcile_playbooks(db: Session, client: DevinClient | None = None) -> dict[str, str]:
    """Reuse DYB Pro playbooks that already exist in the org; create the missing ones."""
    settings = get_settings()
    if not settings.devin_enabled:
        return {}
    owns_client = client is None
    client = client or DevinClient(settings)
    try:
        try:
            remote = {p.get("title"): p.get("playbook_id") for p in client.list_playbooks()}
        except DevinNotConfigured as exc:
            logger.info("playbooks unavailable on this key (%s); running without them", exc)
            return {}
        mapping: dict[str, str] = {}
        for spec in pb.all_specs():
            role = spec.slug.removeprefix(f"{pb.SLUG_PREFIX}-").removeprefix("agent-")
            body_hash = hashlib.sha256(spec.body.encode()).hexdigest()
            cached = db.get(PlaybookRef, role)
            if cached is not None and cached.body_hash == body_hash:
                mapping[role] = cached.playbook_id
                continue
            playbook_id = remote.get(spec.title)
            if not playbook_id:
                schema = PLAN_SCHEMA if role == "orchestrator" else schema_for(role)
                try:
                    created = client.create_playbook(spec.title, spec.body, schema)
                except DevinNotConfigured as exc:
                    # A personal key cannot manage org playbooks. Real sessions still run: every
                    # role prompt already carries its instructions and output schema inline.
                    logger.info("playbooks unavailable on this key (%s); running without them", exc)
                    db.flush()
                    return mapping
                playbook_id = created.get("playbook_id")
                logger.info("created Devin playbook %s -> %s", spec.title, playbook_id)
            if not playbook_id:
                continue
            if cached is None:
                db.add(PlaybookRef(role=role, playbook_id=playbook_id, title=spec.title, body_hash=body_hash))
            else:
                cached.playbook_id = playbook_id
                cached.body_hash = body_hash
                cached.title = spec.title
            mapping[role] = playbook_id
        db.flush()
        return mapping
    finally:
        if owns_client:
            client.close()


# ----------------------------------------------------------------- agent runs


@dataclass
class LaunchSpec:
    role: str
    task: str
    prompt: str
    schema: dict
    acu_limit: int
    title: str


class AgentSupervisor:
    """Owns the lifecycle of the orchestrator session and its children for one cycle."""

    def __init__(
        self,
        db: Session,
        cycle: DesignCycle,
        settings: Settings | None = None,
        client: DevinClient | None = None,
    ) -> None:
        self.db = db
        self.cycle = cycle
        self.settings = settings or get_settings()
        self.provider = resolve_provider(self.settings)
        self._client = client
        self.playbooks: dict[str, str] = {}
        if self.provider == PROVIDER_DEVIN:
            self._client = client or DevinClient(self.settings)
            try:
                self.playbooks = reconcile_playbooks(self.db, self._client)
            except DevinAPIError as exc:
                self._log_cycle("playbook_reconcile_failed", str(exc))

    # ------------------------------------------------------------------ utils

    @property
    def client(self) -> DevinClient:
        if self._client is None:
            raise ProviderUnavailable("Devin client is not available in this provider mode")
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _log_cycle(self, kind: str, summary: str, payload: dict | None = None) -> None:
        self.db.add(
            Observation(
                project_id=self.cycle.project_id,
                cycle_id=self.cycle.id,
                role="orchestrator",
                kind=kind,
                summary=summary[:2000],
                payload=payload or {},
            )
        )
        self.db.flush()

    @staticmethod
    def _append_log(run: AgentRun, message: str) -> None:
        entries = list(run.log or [])
        entries.append({"at": utcnow().isoformat(), "message": message[:1000]})
        run.log = entries[-60:]

    # ----------------------------------------------------------------- launch

    def launch(
        self,
        spec: LaunchSpec,
        *,
        parent_session_id: str | None = None,
        simulation_kwargs: dict | None = None,
    ) -> AgentRun:
        run = AgentRun(
            cycle_id=self.cycle.id,
            role=spec.role,
            task=spec.task,
            prompt=spec.prompt,
            provider=self.provider,
            playbook_id=self.playbooks.get(spec.role),
            tags=tags_for(self.cycle.project_id, self.cycle.id, self.cycle.round, spec.role),
            acu_limit=spec.acu_limit,
            max_attempts=self.settings.agent_max_attempts,
            status="pending",
        )
        self.db.add(run)
        self.db.flush()

        if self.provider == PROVIDER_SIM:
            self._run_simulated(run, spec, simulation_kwargs or {})
            return run

        self._start_devin_session(run, spec, parent_session_id)
        return run

    def _run_simulated(self, run: AgentRun, spec: LaunchSpec, kwargs: dict) -> None:
        run.attempts += 1
        run.started_at = utcnow()
        self._append_log(run, f"local simulation: {spec.role}")
        try:
            if spec.role == "orchestrator":
                output = simulation.simulate_plan(
                    kwargs.get("brief", ""),
                    kwargs.get("evidence", {}),
                    kwargs.get("shortlist_size", 5),
                )
            else:
                output = simulation.simulate(
                    spec.role,
                    sequence=kwargs.get("sequence", ""),
                    evidence=kwargs.get("evidence", {}),
                    exclusions=kwargs.get("exclusions", {}),
                    candidates=kwargs.get("candidates"),
                    shortlist_size=kwargs.get("shortlist_size", 5),
                )
            run.structured_output = output
            run.status = "finished"
            run.devin_status = "simulated"
        except Exception as exc:  # pragma: no cover - defensive
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            self._append_log(run, run.error)
        run.finished_at = utcnow()
        self.db.flush()

    def _start_devin_session(self, run: AgentRun, spec: LaunchSpec, parent_session_id: str | None) -> None:
        run.attempts += 1
        run.started_at = utcnow()
        try:
            state = self.client.create_session(
                spec.prompt,
                title=spec.title,
                playbook_id=self.playbooks.get(spec.role),
                tags=run.tags,
                max_acu_limit=spec.acu_limit,
                structured_output_schema=spec.schema,
                parent_session_id=parent_session_id,
                repos=[self.settings.devin_child_repo] if self.settings.devin_child_repo else None,
                secret_ids=self.settings.devin_secret_id_list or None,
            )
        except DevinAPIError as exc:
            run.status = "failed"
            run.error = str(exc)[:1000]
            self._append_log(run, run.error)
            self.db.flush()
            return
        run.devin_session_id = state.session_id
        run.devin_session_url = state.url
        run.status = "running"
        run.devin_status = state.status
        self._append_log(run, f"devin session started: {state.url}")
        self.db.flush()

    # ------------------------------------------------------------------- poll

    def poll(self, run: AgentRun) -> AgentRun:
        if run.provider != PROVIDER_DEVIN or not run.devin_session_id:
            return run
        try:
            state: SessionState = self.client.get_session(run.devin_session_id)
        except DevinAPIError as exc:
            self._append_log(run, f"poll failed: {exc}")
            self.db.flush()
            return run
        run.devin_status = f"{state.status}/{state.status_detail or '-'}"
        run.acus = state.acus_consumed
        if state.structured_output:
            run.structured_output = state.structured_output
        if state.messages:
            self._append_log(run, f"last message: {state.messages[-1].get('message', '')[:300]}")
        if state.is_terminal or state.structured_output:
            if state.failed and not state.structured_output:
                run.status = "failed"
                run.error = f"devin session ended in status {state.status}"
            elif state.structured_output:
                run.status = "finished"
            else:
                run.status = "failed"
                run.error = "session ended without structured output"
            run.finished_at = utcnow()
        self.db.flush()
        return run

    def wait_for(
        self,
        runs: list[AgentRun],
        *,
        timeout: float | None = None,
        poll_interval: float | None = None,
        sleep=time.sleep,
    ) -> list[AgentRun]:
        """Poll until every run reaches a terminal state, retrying failures within budget."""
        if self.provider == PROVIDER_SIM:
            return runs
        timeout = timeout or self.settings.devin_session_timeout_seconds
        poll_interval = poll_interval or self.settings.devin_poll_interval_seconds
        deadline = time.monotonic() + timeout
        pending = {r.id: r for r in runs}
        while pending and time.monotonic() < deadline:
            for run in list(pending.values()):
                self.poll(run)
                if run.status == "failed" and run.attempts < run.max_attempts:
                    self._append_log(run, f"retry {run.attempts}/{run.max_attempts}")
                    run.status = "pending"
                    run.error = None
                    self._start_devin_session(
                        run,
                        LaunchSpec(
                            role=run.role,
                            task=run.task,
                            prompt=run.prompt,
                            schema=schema_for(run.role),
                            acu_limit=run.acu_limit,
                            title=f"DYB Pro {run.role} retry",
                        ),
                        self.cycle.orchestrator_session_id,
                    )
                    continue
                if run.status in {"finished", "failed", "cancelled"}:
                    pending.pop(run.id, None)
            if pending:
                sleep(poll_interval)
        for run in pending.values():
            run.status = "timeout"
            run.error = f"no structured output within {timeout:.0f}s"
            run.finished_at = utcnow()
            self._append_log(run, run.error)
        self.db.flush()
        return runs

    # ----------------------------------------------------------------- cancel

    def cancel(self, reason: str = "cancelled by scientist") -> int:
        cancelled = 0
        runs = list(self.db.scalars(select(AgentRun).where(AgentRun.cycle_id == self.cycle.id)))
        for run in runs:
            if run.status in {"finished", "failed", "cancelled", "timeout"}:
                continue
            if run.provider == PROVIDER_DEVIN and run.devin_session_id:
                try:
                    self.client.cancel_session(run.devin_session_id, reason)
                except DevinAPIError as exc:
                    self._append_log(run, f"cancel failed: {exc}")
            run.status = "cancelled"
            run.error = reason
            run.finished_at = utcnow()
            self._append_log(run, reason)
            cancelled += 1
        if self.cycle.orchestrator_session_id and self.provider == PROVIDER_DEVIN:
            try:
                self.client.cancel_session(self.cycle.orchestrator_session_id, reason)
            except DevinAPIError as exc:
                self._log_cycle("cancel_failed", str(exc))
        self.db.flush()
        return cancelled


def cancel_cycle(db: Session, cycle: DesignCycle, reason: str = "cancelled by scientist") -> int:
    """Cancel a cycle's sessions without needing a live supervisor instance."""
    settings = get_settings()
    client = DevinClient(settings) if settings.devin_enabled else None
    try:
        supervisor = AgentSupervisor.__new__(AgentSupervisor)
        supervisor.db = db
        supervisor.cycle = cycle
        supervisor.settings = settings
        supervisor.provider = cycle.provider
        supervisor._client = client
        supervisor.playbooks = {}
        return supervisor.cancel(reason)
    finally:
        if client is not None:
            client.close()
