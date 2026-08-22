"""Devin supervision for one round of a Pharmakon drug-discovery program.

Deliberately persistence-agnostic: this module owns the *agent* lifecycle (playbooks, prompts,
session launch, polling, retry, cancel) and returns plain :class:`AgentOutcome` records. The
program service persists them, so the supervisor stays usable from a route, a Celery task or a
scheduled research daemon without dragging a specific ORM row into the agent layer.

Providers are the same two the protein-design layer has, with the same honesty rule: a run is
either a real Devin session (``devin``) or an explicitly-labelled ``local-simulation``. Nothing in
between, and the provider is recorded on every outcome.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.devin import pharma_simulation as sim
from app.devin.client import DevinAPIError, DevinClient, SessionState
from app.devin.pharma_prompts import (
    pharma_child_prompt,
    program_orchestrator_prompt,
    tags_for,
)
from app.devin.pharma_schemas import PHARMA_ROLE_SCHEMAS, PHARMA_ROLES
from app.devin.runner import PROVIDER_DEVIN, PROVIDER_SIM, ProviderUnavailable, resolve_provider

logger = logging.getLogger(__name__)

CHILD_ROLES = tuple(r for r in PHARMA_ROLES if r != "program")
DEFAULT_ACU_LIMIT = 8
MAX_CHILDREN = 6


@dataclass
class RoundContext:
    """Everything an agent needs to know about the program round it is working on."""

    program_id: str
    round_id: str
    program: dict
    stage: dict
    gate: dict
    evaluations: list[dict] = field(default_factory=list)
    assays: list[dict] = field(default_factory=list)
    drift: dict = field(default_factory=dict)
    history: str = ""
    target_sequence: str | None = None
    pocket: dict | None = None


@dataclass
class AgentOutcome:
    """One agent run, provider-agnostic, ready to be persisted by the caller."""

    role: str
    task: str
    prompt: str
    provider: str
    tags: list[str]
    acu_limit: int
    playbook_id: str | None = None
    devin_session_id: str | None = None
    devin_session_url: str | None = None
    status: str = "pending"
    devin_status: str | None = None
    structured_output: dict | None = None
    acus: float | None = None
    error: str | None = None
    log: list[dict] = field(default_factory=list)
    attempts: int = 0

    def note(self, message: str) -> None:
        self.log.append({"message": message[:1000]})

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "task": self.task,
            "prompt": self.prompt,
            "provider": self.provider,
            "tags": self.tags,
            "acu_limit": self.acu_limit,
            "playbook_id": self.playbook_id,
            "devin_session_id": self.devin_session_id,
            "devin_session_url": self.devin_session_url,
            "status": self.status,
            "devin_status": self.devin_status,
            "structured_output": self.structured_output,
            "acus": self.acus,
            "error": self.error,
            "log": self.log,
            "attempts": self.attempts,
        }


class PharmaSupervisor:
    """Runs the orchestrator session for a program round and fans out to specialist children."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: DevinClient | None = None,
        playbooks: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = resolve_provider(self.settings)
        self.playbooks = playbooks or {}
        self._client = client
        if self.provider == PROVIDER_DEVIN and self._client is None:
            self._client = DevinClient(self.settings)

    # ------------------------------------------------------------------ utils

    @property
    def client(self) -> DevinClient:
        if self._client is None:
            raise ProviderUnavailable("Devin client is not available in this provider mode")
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # --------------------------------------------------------------- planning

    def plan_round(self, ctx: RoundContext) -> AgentOutcome:
        prompt = program_orchestrator_prompt(
            program=ctx.program,
            stage=ctx.stage,
            gate=ctx.gate,
            evaluations=ctx.evaluations,
            assays=ctx.assays,
            drift=ctx.drift,
            history=ctx.history,
            available_roles=list(ctx.stage.get("roles") or CHILD_ROLES),
        )
        outcome = AgentOutcome(
            role="program",
            task=f"plan stage {ctx.stage.get('key')} round",
            prompt=prompt,
            provider=self.provider,
            tags=tags_for(ctx.program_id, ctx.round_id, str(ctx.stage.get("key")), "program"),
            acu_limit=self.settings.devin_orchestrator_acu_limit,
            playbook_id=self.playbooks.get("program"),
        )
        if self.provider == PROVIDER_SIM:
            self._simulate(
                outcome,
                lambda: sim.simulate_program_plan(
                    stage=ctx.stage, gate=ctx.gate, evaluations=ctx.evaluations
                ),
            )
            return outcome
        self._start_session(
            outcome,
            title=f"Pharmakon program {ctx.program.get('name')} - {ctx.stage.get('key')}",
            schema=PHARMA_ROLE_SCHEMAS["program"],
        )
        return outcome

    def child_specs(self, plan: dict, ctx: RoundContext) -> list[dict]:
        """Validate the orchestrator's fan-out: known roles, bounded count, deduplicated."""
        specs: list[dict] = []
        seen: set[str] = set()
        for item in (plan or {}).get("agents", []) or []:
            role = str((item or {}).get("role", "")).strip()
            if role not in CHILD_ROLES or role in seen:
                continue
            seen.add(role)
            specs.append(
                {
                    "role": role,
                    "task": str(item.get("task") or "").strip() or f"{role} assessment",
                    "acu_limit": int(item.get("acu_limit") or DEFAULT_ACU_LIMIT),
                    "must_avoid": list(item.get("must_avoid") or []),
                }
            )
            if len(specs) >= MAX_CHILDREN:
                break
        if not specs:
            stage_roles = [r for r in (ctx.stage.get("roles") or []) if r in CHILD_ROLES]
            specs = [
                {
                    "role": role,
                    "task": f"{role} assessment for stage {ctx.stage.get('key')}",
                    "acu_limit": DEFAULT_ACU_LIMIT,
                    "must_avoid": [],
                }
                for role in (stage_roles or ["medchem", "triage"])[:MAX_CHILDREN]
            ]
        return specs

    # ------------------------------------------------------------- fan-out

    def launch_child(
        self,
        spec: dict,
        ctx: RoundContext,
        *,
        strategy: str,
        parent_session_id: str | None = None,
    ) -> AgentOutcome:
        role = spec["role"]
        prompt = pharma_child_prompt(
            role=role,
            task=spec["task"],
            program=ctx.program,
            stage=ctx.stage,
            strategy=strategy,
            gate=ctx.gate,
            evaluations=ctx.evaluations,
            assays=ctx.assays,
            history=ctx.history,
            must_avoid=spec.get("must_avoid") or [],
            target_sequence=ctx.target_sequence,
            pocket=ctx.pocket,
        )
        outcome = AgentOutcome(
            role=role,
            task=spec["task"],
            prompt=prompt,
            provider=self.provider,
            tags=tags_for(ctx.program_id, ctx.round_id, str(ctx.stage.get("key")), role),
            acu_limit=min(
                int(spec.get("acu_limit") or DEFAULT_ACU_LIMIT),
                self.settings.devin_child_acu_limit,
            ),
            playbook_id=self.playbooks.get(role),
        )
        if self.provider == PROVIDER_SIM:
            self._simulate(
                outcome,
                lambda: sim.simulate(role, evaluations=ctx.evaluations, pocket=ctx.pocket),
            )
            return outcome
        self._start_session(
            outcome,
            title=f"Pharmakon {role} - {ctx.program.get('target_name')}",
            schema=PHARMA_ROLE_SCHEMAS[role],
            parent_session_id=parent_session_id,
        )
        return outcome

    def fan_out(
        self,
        plan: dict,
        ctx: RoundContext,
        *,
        parent_session_id: str | None = None,
    ) -> list[AgentOutcome]:
        strategy = str((plan or {}).get("strategy") or "").strip() or "no strategy recorded"
        return [
            self.launch_child(spec, ctx, strategy=strategy, parent_session_id=parent_session_id)
            for spec in self.child_specs(plan, ctx)
        ]

    # ------------------------------------------------------------------- poll

    def poll(self, outcome: AgentOutcome) -> AgentOutcome:
        if outcome.provider != PROVIDER_DEVIN or not outcome.devin_session_id:
            return outcome
        try:
            state: SessionState = self.client.get_session(outcome.devin_session_id)
        except DevinAPIError as exc:
            outcome.note(f"poll failed: {exc}")
            return outcome
        outcome.devin_status = f"{state.status}/{state.status_detail or '-'}"
        outcome.acus = state.acus_consumed
        if state.structured_output:
            outcome.structured_output = state.structured_output
        if state.is_terminal or state.structured_output:
            if state.structured_output:
                outcome.status = "finished"
            else:
                outcome.status = "failed"
                outcome.error = f"devin session ended in status {state.status}"
        return outcome

    def wait_for(
        self,
        outcomes: list[AgentOutcome],
        *,
        timeout: float | None = None,
        poll_interval: float | None = None,
        sleep=time.sleep,
    ) -> list[AgentOutcome]:
        if self.provider == PROVIDER_SIM:
            return outcomes
        timeout = timeout or self.settings.devin_session_timeout_seconds
        poll_interval = poll_interval or self.settings.devin_poll_interval_seconds
        deadline = time.monotonic() + timeout
        pending = [o for o in outcomes if o.status in {"pending", "running"}]
        while pending and time.monotonic() < deadline:
            for outcome in list(pending):
                self.poll(outcome)
                if outcome.status in {"finished", "failed", "cancelled"}:
                    pending.remove(outcome)
            if pending:
                sleep(poll_interval)
        for outcome in pending:
            outcome.status = "timeout"
            outcome.error = f"no structured output within {timeout:.0f}s"
            outcome.note(outcome.error)
        return outcomes

    def cancel(self, outcomes: list[AgentOutcome], reason: str = "cancelled by scientist") -> int:
        cancelled = 0
        for outcome in outcomes:
            if outcome.status in {"finished", "failed", "cancelled", "timeout"}:
                continue
            if outcome.provider == PROVIDER_DEVIN and outcome.devin_session_id:
                try:
                    self.client.cancel_session(outcome.devin_session_id, reason)
                except DevinAPIError as exc:
                    outcome.note(f"cancel failed: {exc}")
            outcome.status = "cancelled"
            outcome.error = reason
            cancelled += 1
        return cancelled

    # -------------------------------------------------------------- internals

    def _simulate(self, outcome: AgentOutcome, produce) -> None:
        outcome.attempts += 1
        outcome.note(f"local simulation: {outcome.role}")
        try:
            outcome.structured_output = produce()
            outcome.status = "finished"
            outcome.devin_status = "simulated"
        except Exception as exc:  # pragma: no cover - defensive
            outcome.status = "failed"
            outcome.error = f"{type(exc).__name__}: {exc}"
            outcome.note(outcome.error)

    def _start_session(
        self,
        outcome: AgentOutcome,
        *,
        title: str,
        schema: dict,
        parent_session_id: str | None = None,
    ) -> None:
        outcome.attempts += 1
        try:
            state = self.client.create_session(
                outcome.prompt,
                title=title[:120],
                playbook_id=outcome.playbook_id,
                tags=outcome.tags,
                max_acu_limit=outcome.acu_limit,
                structured_output_schema=schema,
                parent_session_id=parent_session_id,
            )
        except DevinAPIError as exc:
            outcome.status = "failed"
            outcome.error = str(exc)[:1000]
            outcome.note(outcome.error)
            return
        outcome.devin_session_id = state.session_id
        outcome.devin_session_url = state.url
        outcome.status = "running"
        outcome.devin_status = state.status
        outcome.note(f"devin session started: {state.url}")


def harvest_molecules(outcomes: list[AgentOutcome]) -> list[dict]:
    """Collect molecule proposals from every finished medchem run, in a stable order."""
    proposals: list[dict] = []
    seen: set[str] = set()
    for outcome in outcomes:
        if outcome.role != "medchem" or outcome.status != "finished":
            continue
        for item in (outcome.structured_output or {}).get("molecules", []) or []:
            smiles = str((item or {}).get("smiles") or "").strip()
            if not smiles or smiles in seen:
                continue
            seen.add(smiles)
            proposals.append({**item, "smiles": smiles, "source_role": outcome.role})
    return proposals


def harvest_findings(outcomes: list[AgentOutcome]) -> dict:
    """Reduce agent outputs into the numeric findings the deterministic gate consumes."""
    findings: dict = {}
    for outcome in outcomes:
        if outcome.status != "finished" or not outcome.structured_output:
            continue
        out = outcome.structured_output
        # The offline simulation cannot reach literature, patent or clinical sources, so its
        # placeholder scores are withheld from the gate: the criterion stays unmeasured instead of
        # deciding a program on a number nobody looked up.
        world_evidence = outcome.provider != sim.PROVIDER
        if outcome.role == "target":
            findings["pocket_residues"] = out.get("pocket_residues") or []
            if world_evidence:
                findings["target_evidence_score"] = out.get("target_evidence_score")
                findings["citation_count"] = len(
                    [f for f in out.get("findings", []) if (f or {}).get("citation")]
                )
        elif outcome.role == "ip":
            if world_evidence:
                findings["freedom_to_operate"] = out.get("freedom_to_operate")
        elif outcome.role == "clinical":
            dose = out.get("starting_dose_mg")
            findings["starting_dose_defined"] = bool(dose) and bool(out.get("starting_dose_basis"))
            findings["starting_dose_mg"] = dose
        elif outcome.role == "synthesis":
            feas = [
                r.get("scale_up_feasibility")
                for r in out.get("routes", [])
                if isinstance(r.get("scale_up_feasibility"), int | float)
            ]
            if feas:
                findings["cmc_feasibility"] = max(feas)
        elif outcome.role == "triage":
            findings["recommended_candidate"] = out.get("recommended_candidate")
            findings["recommended_backup"] = out.get("recommended_backup")
    return findings
