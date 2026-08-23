"""The Pharmakon program engine: one autonomous round of a drug-discovery program.

A round is the small-molecule analogue of DYB Pro's design cycle, but it ends in a *decision*
rather than a shortlist:

    plan -> fan out to pharma specialists -> commit molecules -> score portfolio
         -> measure drift against wet-lab data -> evaluate the stage gate
         -> propose the experiments that would falsify the gate -> advance / recycle / stop

Everything is provider-labelled and append-only. Measured `AssayResult` rows always outrank the
deterministic predictions, and the gate is deterministic Python - agents supply evidence and
proposals, they never decide a stage on their own.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chem.binding import pocket_from_sequence
from app.chem.evaluate import rank_molecules
from app.chem.smiles import SmilesError, molecule_hash, parse_smiles, scaffold_key
from app.config import get_settings
from app.devin import campaign_memory
from app.devin.daemon import daemon_plan
from app.devin.pharma_runner import (
    AgentOutcome,
    PharmaSupervisor,
    RoundContext,
    harvest_findings,
    harvest_molecules,
)
from app.devin.runner import PROVIDER_DEVIN
from app.models import (
    AssayResult,
    DrugProgram,
    Experiment,
    GateDecision,
    MoleculeCommit,
    PharmaAgentRun,
    ProgramResearchEvent,
    ProgramRound,
    utcnow,
)
from app.pharma.economics import program_economics
from app.pharma.experiments import experiment_plan
from app.pharma.gates import evaluate_gate
from app.pharma.metrics import prediction_drift, program_metrics
from app.pharma.stages import stage as stage_spec
from app.services import ingest as ingestlib

logger = logging.getLogger(__name__)

MAX_PORTFOLIO = 60


def commit_id(program_id: str, graph_hash: str) -> str:
    """Program-scoped content address for a molecule.

    Molecule identity is the graph hash, but two programs that both explore the same molecule
    each need their own immutable commit: the evaluation depends on the program's target and
    stage, and one program's evidence is not another's. The graph hash is stored alongside so
    the same chemistry can still be found across the portfolio.
    """
    return hashlib.sha256(f"{program_id}:{graph_hash}".encode()).hexdigest()[:40]


# ------------------------------------------------------------------ read helpers


def program_pocket(program: DrugProgram) -> dict | None:
    if not program.target_sequence:
        return None
    residues = [int(r) for r in (program.pocket_residues or []) if str(r).isdigit()]
    return pocket_from_sequence(program.target_sequence, residues or None)


def portfolio(db: Session, program_id: str, limit: int = MAX_PORTFOLIO) -> list[dict]:
    """Stored evaluations for the program's molecules, best composite score first."""
    rows = db.scalars(
        select(MoleculeCommit)
        .where(MoleculeCommit.program_id == program_id)
        .order_by(MoleculeCommit.composite_score.desc())
        .limit(limit)
    ).all()
    out: list[dict] = []
    for row in rows:
        record = dict(row.evaluation or {})
        record.setdefault("smiles", row.smiles)
        # Downstream code (gates, drift, experiments) addresses molecules by commit id, which is
        # program-scoped; the global graph hash stays available as `graph_hash`.
        record["graph_hash"] = row.molecule_hash or record.get("molecule_hash")
        record["molecule_hash"] = row.id
        record["label"] = row.label
        out.append(record)
    return out


def assay_rows(db: Session, program_id: str) -> list[dict]:
    rows = db.scalars(
        select(AssayResult)
        .where(AssayResult.program_id == program_id)
        .order_by(AssayResult.created_at)
    ).all()
    return [
        {
            "molecule_hash": r.molecule_hash,
            "assay": r.assay,
            "metric": r.metric,
            "value": r.value,
            "unit": r.unit,
            "operator": r.operator,
            "source": r.source,
        }
        for r in rows
    ]


def gate_history(db: Session, program_id: str) -> list[dict]:
    rows = db.scalars(
        select(GateDecision)
        .where(GateDecision.program_id == program_id)
        .order_by(GateDecision.created_at)
    ).all()
    return [
        {
            "stage_key": r.stage,
            "decision": r.decision,
            "score": r.score,
            "rationale": r.rationale,
            "approval_status": r.approval_status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def exclusions(db: Session, program_id: str) -> list[dict]:
    """Molecules the platform has already ruled out, so agents stop re-proposing them."""
    rows = db.scalars(
        select(MoleculeCommit)
        .where(MoleculeCommit.program_id == program_id, MoleculeCommit.verdict == "reject")
        .order_by(MoleculeCommit.created_at.desc())
        .limit(campaign_memory.MAX_EXCLUSIONS)
    ).all()
    out: list[dict] = []
    for row in rows:
        alerts = ((row.evaluation or {}).get("liabilities") or {}).get("blocking") or []
        reason = ", ".join(str(a) for a in alerts) or row.verdict or "failed the portfolio filter"
        out.append({"smiles": row.smiles, "reason": reason})
    return out


# Evidence about the world (literature, patents, clinical practice) does not expire when a round
# ends, so it is carried forward across rounds. Evidence about *molecules* is never carried: it is
# recomputed from the commits and assay rows every round.
CARRIED_FINDINGS = (
    "target_evidence_score",
    "druggability_score",
    "freedom_to_operate",
    "citation_count",
    "starting_dose_defined",
    "starting_dose_mg",
    "scale_up_feasibility",
)


def carried_findings(db: Session, program_id: str) -> dict:
    """World-evidence findings accumulated over earlier rounds, plus human-supplied evidence.

    Later rounds win over earlier ones, and a human entry wins over any agent entry, because a
    scientist typing a number with a citation is stronger evidence than an agent's estimate.
    """
    merged: dict = {}
    rounds = db.scalars(
        select(ProgramRound)
        .where(ProgramRound.program_id == program_id)
        .order_by(ProgramRound.number)
    ).all()
    for rnd in rounds:
        for key in CARRIED_FINDINGS:
            value = (rnd.findings or {}).get(key)
            if value is not None:
                merged[key] = value
    human = db.scalars(
        select(ProgramResearchEvent)
        .where(
            ProgramResearchEvent.program_id == program_id,
            ProgramResearchEvent.kind == "human_evidence",
        )
        .order_by(ProgramResearchEvent.created_at)
    ).all()
    for event in human:
        payload = event.payload or {}
        key = str(payload.get("metric") or "")
        if key in CARRIED_FINDINGS and payload.get("value") is not None:
            merged[key] = payload["value"]
    return merged


def program_dict(program: DrugProgram) -> dict:
    return {
        "id": program.id,
        "name": program.name,
        "target_name": program.target_name,
        "indication": program.indication,
        "objective": program.objective,
        "current_stage": program.current_stage,
        "autonomy_level": program.autonomy_level,
        "status": program.status,
        "rounds_run": program.rounds_run,
        "stage_cycles": program.stage_cycles,
        "assays_ingested": program.assays_ingested,
        "acus_used": program.acus_used,
        "last_research_at": (
            program.last_research_at.isoformat() if program.last_research_at else None
        ),
    }


# ------------------------------------------------------------------ write helpers


def record_event(
    db: Session,
    program: DrugProgram,
    kind: str,
    *,
    claim: str = "",
    role: str = "system",
    round_id: str | None = None,
    citation: str = "",
    implication: str = "",
    payload: dict | None = None,
    provider: str = "deterministic",
    devin_session_id: str | None = None,
) -> ProgramResearchEvent:
    event = ProgramResearchEvent(
        program_id=program.id,
        round_id=round_id,
        role=role,
        kind=kind,
        claim=claim[:4000],
        citation=citation[:512],
        implication=implication[:4000],
        payload=payload or {},
        provider=provider,
        devin_session_id=devin_session_id,
    )
    db.add(event)
    db.flush()
    return event


_event = record_event


def _persist_run(
    db: Session, program: DrugProgram, rnd: ProgramRound, outcome: AgentOutcome
) -> PharmaAgentRun:
    run = PharmaAgentRun(
        program_id=program.id,
        round_id=rnd.id,
        role=outcome.role,
        task=outcome.task[:4000],
        prompt=outcome.prompt[:20000],
        provider=outcome.provider,
        devin_session_id=outcome.devin_session_id,
        devin_session_url=outcome.devin_session_url,
        playbook_id=outcome.playbook_id,
        tags=outcome.tags,
        status=outcome.status,
        devin_status=outcome.devin_status,
        attempts=outcome.attempts,
        acu_limit=outcome.acu_limit,
        acus=outcome.acus or 0.0,
        structured_output=outcome.structured_output or {},
        log=outcome.log,
        error=outcome.error,
        finished_at=utcnow() if outcome.status in {"finished", "failed", "timeout"} else None,
    )
    db.add(run)
    db.flush()
    return run


def commit_molecules(
    db: Session,
    program: DrugProgram,
    proposals: list[dict],
    *,
    stage_key: str,
    round_id: str | None,
    provider: str,
    session_by_role: dict[str, tuple[str | None, str | None]] | None = None,
) -> tuple[list[MoleculeCommit], list[dict]]:
    """Evaluate and content-address each proposal. Returns (new commits, rejected proposals).

    The commit id is the molecule graph hash, so the same molecule proposed twice in a program is
    the same immutable row - re-proposing it never rewrites its history.
    """
    pocket = program_pocket(program)
    sessions = session_by_role or {}
    smiles_list: list[str] = []
    meta: dict[str, dict] = {}
    rejected: list[dict] = []
    for proposal in proposals:
        smiles = str(proposal.get("smiles") or "").strip()
        if not smiles:
            continue
        try:
            parse_smiles(smiles)
        except SmilesError as exc:
            rejected.append({**proposal, "error": str(exc)})
            continue
        key = molecule_hash(smiles)
        if key in meta:
            continue
        meta[key] = proposal
        smiles_list.append(smiles)
    if not smiles_list:
        return [], rejected

    ranked = rank_molecules(
        smiles_list,
        target_sequence=program.target_sequence or None,
        pocket=pocket,
        stage=stage_spec(stage_key).scoring_stage,
    )
    commits: list[MoleculeCommit] = []
    for record in ranked.get("ranked", []):
        key = record.get("molecule_hash") or molecule_hash(record["smiles"])
        proposal = meta.get(key, {})
        commit_key = commit_id(program.id, key)
        existing = db.get(MoleculeCommit, commit_key)
        if existing is not None:
            continue
        role = str(proposal.get("agent_role") or "medchem")
        session_id, session_url = sessions.get(role, (None, None))
        parent_smiles = str(proposal.get("parent_smiles") or "").strip()
        parents: list[str] = []
        if parent_smiles:
            try:
                parents.append(commit_id(program.id, molecule_hash(parent_smiles)))
            except SmilesError:
                parents = []
        commit = MoleculeCommit(
            id=commit_key,
            program_id=program.id,
            molecule_hash=key,
            label=str(proposal.get("label") or key[:8])[:128],
            smiles=record["smiles"],
            formula=str((record.get("descriptors") or {}).get("formula") or ""),
            scaffold_key=scaffold_key(record["smiles"]),
            parent_ids=parents,
            transform=str(proposal.get("transform") or "")[:2000],
            stage=stage_key,
            evaluation=record,
            composite_score=float(record.get("composite_score") or 0.0),
            verdict=str(record.get("verdict") or ""),
            rationale=str(proposal.get("rationale") or "")[:4000],
            agent_role=role,
            provider=provider,
            devin_session_id=session_id,
            devin_session_url=session_url,
            citations=[str(c) for c in (proposal.get("citations") or [])],
            round_id=round_id,
        )
        db.add(commit)
        commits.append(commit)
    db.flush()
    return commits, rejected


def record_gate(
    db: Session,
    program: DrugProgram,
    gate: dict,
    *,
    round_id: str | None,
) -> GateDecision:
    decision = GateDecision(
        program_id=program.id,
        round_id=round_id,
        stage=gate["stage"],
        decision=gate["decision"],
        score=float(gate.get("score") or 0.0),
        criteria=gate.get("criteria") or [],
        blocking_failures=gate.get("blocking_failures") or [],
        rationale=gate.get("rationale", ""),
        recommended_actions=gate.get("recommended_actions") or [],
        next_stage=gate.get("next_stage"),
        autonomy_level=int(gate.get("autonomy_level") or program.autonomy_level),
        requires_approval=bool(gate.get("requires_approval")),
        approval_reason=gate.get("approval_reason", ""),
        approval_status="pending" if gate.get("requires_approval") else "not_required",
    )
    db.add(decision)
    db.flush()
    return decision


def apply_decision(db: Session, program: DrugProgram, decision: GateDecision) -> str:
    """Move the program according to an approved (or approval-free) gate decision."""
    if decision.applied:
        return "already_applied"
    if decision.requires_approval and decision.approval_status != "approved":
        program.status = "awaiting_approval"
        db.flush()
        return "awaiting_approval"

    action = "recycled"
    if decision.decision == "go" and decision.next_stage:
        program.current_stage = decision.next_stage
        program.stage_cycles = 0
        program.status = "active"
        action = f"advanced to {decision.next_stage}"
    elif decision.decision == "go":
        program.status = "completed"
        action = "program complete: candidate package ready for human sign-off"
    elif decision.decision == "kill":
        program.status = "killed"
        action = "killed"
    else:
        program.stage_cycles += 1
        program.status = "active"
        action = f"recycled within {program.current_stage} (cycle {program.stage_cycles})"
    decision.applied = True
    decision.decided_at = utcnow()
    db.flush()
    _event(
        db,
        program,
        "gate_applied",
        claim=f"{decision.stage}: {decision.decision} -> {action}",
        payload={"gate_decision_id": decision.id, "score": decision.score},
    )
    return action


def next_action(db: Session, program: DrugProgram) -> dict:
    """What the program may do next, given its latest gate and autonomy level.

    An already-applied gate no longer constrains anything: the program has moved on, so the next
    action is simply another round at whatever stage it now sits in.
    """
    latest = db.scalar(
        select(GateDecision)
        .where(GateDecision.program_id == program.id)
        .order_by(GateDecision.created_at.desc())
        .limit(1)
    )
    if latest is None or latest.applied:
        gate = {"decision": "recycle", "requires_approval": False}
    else:
        gate = {
            "stage": latest.stage,
            "decision": latest.decision,
            "requires_approval": latest.requires_approval
            and latest.approval_status != "approved",
            "approval_reason": latest.approval_reason,
        }
    return daemon_plan(program_dict(program), gate)


def sync_campaign_memory(db: Session, program: DrugProgram, drift: dict | None = None) -> dict:
    """Write the campaign digest to a Devin knowledge note so future sessions inherit it."""
    history = gate_history(db, program.id)
    best = portfolio(db, program.id, limit=5)
    recent = db.scalars(
        select(GateDecision)
        .where(GateDecision.program_id == program.id)
        .order_by(GateDecision.created_at.desc())
        .limit(3)
    ).all()
    lessons: list[str] = []
    for row in recent:
        lessons.extend(
            campaign_memory.lessons_from_gate(
                {
                    "stage": row.stage,
                    "decision": row.decision,
                    "criteria": row.criteria or [],
                    "blocking_failures": row.blocking_failures or [],
                },
                {"prediction_drift_rmse": (drift or {}).get("rmse")},
            )
        )
    digest = campaign_memory.build_digest(
        program=program_dict(program),
        gate_history=history,
        exclusions=exclusions(db, program.id),
        lessons=lessons,
        drift=drift,
        best_molecules=best,
    )
    result = campaign_memory.sync_note(
        program=program_dict(program), digest=digest, note_id=program.knowledge_note_id
    )
    if result.get("note_id"):
        program.knowledge_note_id = result["note_id"]
    db.flush()
    _event(
        db,
        program,
        "campaign_memory",
        claim="campaign digest updated",
        payload={"synced_to_devin": result.get("synced"), "error": result.get("error")},
        provider="devin" if result.get("synced") else "local",
    )
    return {**result, "digest": digest}


# ------------------------------------------------------------------ the round


def run_round(db: Session, round_id: str, sleep=time.sleep) -> ProgramRound:
    settings = get_settings()
    rnd = db.get(ProgramRound, round_id)
    if rnd is None:
        raise KeyError(f"unknown round {round_id}")
    if rnd.status == "cancelled":
        return rnd
    program = db.get(DrugProgram, rnd.program_id)
    spec = stage_spec(rnd.stage)

    supervisor = PharmaSupervisor(settings=settings)
    rnd.provider = supervisor.provider
    program.provider = supervisor.provider
    program.status = "running"

    evaluations = portfolio(db, program.id)
    assays = assay_rows(db, program.id)
    drift = prediction_drift(evaluations, assays)
    carried = carried_findings(db, program.id)
    metrics = program_metrics(
        evaluations=evaluations,
        assays=assays,
        findings=carried,
        stage_cycles=program.stage_cycles,
    )
    pre_gate = evaluate_gate(
        rnd.stage,
        metrics,
        autonomy_level=program.autonomy_level,
        stage_cycles=program.stage_cycles,
    ).as_dict()

    program_context = program_dict(program)
    program_context["binding_inputs"] = ingestlib.binding_input_summaries(
        db, program.project_id
    )

    digest = campaign_memory.build_digest(
        program=program_context,
        gate_history=gate_history(db, program.id),
        exclusions=exclusions(db, program.id),
        lessons=[],
        drift=drift,
        best_molecules=evaluations[:5],
    )
    ctx = RoundContext(
        program_id=program.id,
        round_id=rnd.id,
        program=program_context,
        stage={
            "key": spec.key,
            "name": spec.name,
            "objective": spec.objective,
            "roles": list(spec.roles),
            "exit_deliverable": spec.exit_deliverable,
        },
        gate=pre_gate,
        evaluations=evaluations,
        assays=assays,
        drift=drift,
        history=digest,
        target_sequence=program.target_sequence or None,
        pocket=program_pocket(program),
    )
    _event(
        db,
        program,
        "round_started",
        claim=f"round {rnd.number} of {spec.name} started via provider {supervisor.provider}",
        round_id=rnd.id,
        payload={"provider": supervisor.provider, "pre_gate": pre_gate, "drift": drift},
        provider=supervisor.provider,
    )
    db.commit()

    try:
        # 1) plan ---------------------------------------------------------------
        rnd.status = "planning"
        db.commit()
        plan_outcome = supervisor.plan_round(ctx)
        _persist_run(db, program, rnd, plan_outcome)
        rnd.orchestrator_session_id = plan_outcome.devin_session_id
        rnd.orchestrator_session_url = plan_outcome.devin_session_url
        plan = plan_outcome.structured_output or {}
        rnd.plan = plan
        _event(
            db,
            program,
            "plan",
            claim=str(plan.get("strategy") or "orchestrator returned no strategy"),
            role="program",
            round_id=rnd.id,
            payload={"plan": plan, "session": plan_outcome.devin_session_url},
            provider=plan_outcome.provider,
            devin_session_id=plan_outcome.devin_session_id,
        )
        db.commit()

        # 2) fan out ------------------------------------------------------------
        rnd.status = "fanning_out"
        db.commit()
        outcomes = supervisor.fan_out(
            plan, ctx, parent_session_id=plan_outcome.devin_session_id
        )
        rnd.status = "awaiting_agents"
        db.commit()
        supervisor.wait_for(outcomes, sleep=sleep)

        session_by_role: dict[str, tuple[str | None, str | None]] = {}
        for outcome in outcomes:
            _persist_run(db, program, rnd, outcome)
            session_by_role[outcome.role] = (
                outcome.devin_session_id,
                outcome.devin_session_url,
            )
            output = outcome.structured_output or {}
            _event(
                db,
                program,
                "agent_result" if outcome.status == "finished" else f"agent_{outcome.status}",
                claim=str(output.get("analysis") or outcome.error or "no analysis returned"),
                role=outcome.role,
                round_id=rnd.id,
                payload={"status": outcome.status, "output": output},
                provider=outcome.provider,
                devin_session_id=outcome.devin_session_id,
            )
            for finding in output.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                _event(
                    db,
                    program,
                    "literature",
                    claim=str(finding.get("claim") or "")[:4000],
                    role=outcome.role,
                    round_id=rnd.id,
                    citation=str(finding.get("citation") or ""),
                    implication=str(finding.get("implication") or ""),
                    payload=finding,
                    provider=outcome.provider,
                    devin_session_id=outcome.devin_session_id,
                )
        db.commit()

        # 3) commit molecules ---------------------------------------------------
        rnd.status = "scoring"
        proposals = harvest_molecules(outcomes)
        commits, rejected = commit_molecules(
            db,
            program,
            proposals,
            stage_key=rnd.stage,
            round_id=rnd.id,
            provider=supervisor.provider,
            session_by_role=session_by_role,
        )
        if rejected:
            _event(
                db,
                program,
                "invalid_proposals",
                claim=f"{len(rejected)} proposed structures were not parseable and were dropped",
                round_id=rnd.id,
                payload={"rejected": rejected[:20]},
            )
        # World evidence from earlier rounds still counts; this round's agents can only add to or
        # overwrite it, never silently erase it.
        findings = {**carried, **harvest_findings(outcomes)}
        rnd.findings = findings

        # 4) re-score the portfolio and gate -----------------------------------
        evaluations = portfolio(db, program.id)
        assays = assay_rows(db, program.id)
        drift = prediction_drift(evaluations, assays)
        metrics = program_metrics(
            evaluations=evaluations,
            assays=assays,
            findings=findings,
            stage_cycles=program.stage_cycles,
        )
        gate = evaluate_gate(
            rnd.stage,
            metrics,
            autonomy_level=program.autonomy_level,
            stage_cycles=program.stage_cycles,
        ).as_dict()
        rnd.metrics = metrics
        rnd.gate = gate
        decision = record_gate(db, program, gate, round_id=rnd.id)

        # 5) the experiments that would falsify this gate ----------------------
        plan_out = experiment_plan(gate, evaluations)
        rnd.experiment_plan = plan_out
        for proposal in plan_out.get("proposals", []):
            db.add(
                Experiment(
                    program_id=program.id,
                    round_id=rnd.id,
                    stage=rnd.stage,
                    assay=str(proposal.get("assay", ""))[:128],
                    endpoint=str(proposal.get("endpoint", ""))[:64],
                    unit=str(proposal.get("unit", ""))[:32],
                    gate_metric=str(proposal.get("metric", ""))[:64],
                    molecules=[
                        str(c.get("molecule_hash"))
                        for c in proposal.get("compounds") or []
                        if c.get("molecule_hash")
                    ],
                    predicted_value=(
                        float(proposal["predicted_value"])
                        if isinstance(proposal.get("predicted_value"), int | float)
                        else None
                    ),
                    falsification=str(proposal.get("falsifies_if", "")),
                    cost_usd=float(proposal.get("cost_usd") or 0.0),
                    turnaround_days=int(proposal.get("turnaround_days") or 0),
                    blocking=bool(proposal.get("blocking_for_gate")),
                    priority=int(proposal.get("priority") or 0),
                )
            )

        # 6) candidate bookkeeping and close out -------------------------------
        best = evaluations[0] if evaluations else None
        if best:
            program.candidate_molecule_id = best.get("molecule_hash")
        if len(evaluations) > 1:
            program.backup_molecule_id = evaluations[1].get("molecule_hash")
        acus = (plan_outcome.acus or 0.0) + sum(o.acus or 0.0 for o in outcomes)
        rnd.acus_used = round(acus, 3)
        program.acus_used = round(program.acus_used + acus, 3)
        program.rounds_run += 1
        rnd.summary = (
            f"{spec.name}: {len(commits)} new molecules, gate {gate['decision']} "
            f"(score {gate['score']}). {gate['rationale']}"
        )[:4000]
        rnd.status = "gated"
        rnd.finished_at = utcnow()

        next_action = daemon_plan(program_dict(program), gate)
        if next_action["action"] == "advance_stage" or (
            next_action["action"] == "run_round" and not decision.requires_approval
        ):
            apply_decision(db, program, decision)
        else:
            program.status = (
                "awaiting_approval"
                if next_action["action"] in {"await_approval", "await_human"}
                else "active"
            )
        _event(
            db,
            program,
            "round_finished",
            claim=rnd.summary,
            round_id=rnd.id,
            payload={
                "gate": gate,
                "metrics": metrics,
                "drift": drift,
                "next_action": next_action,
                "new_molecules": [c.id for c in commits],
                "experiments": len(plan_out.get("proposals", [])),
            },
            provider=supervisor.provider,
        )
        sync_campaign_memory(db, program, drift)
        db.commit()
        return rnd

    except Exception as exc:
        logger.exception("round %s failed", round_id)
        db.rollback()
        rnd = db.get(ProgramRound, round_id)
        program = db.get(DrugProgram, rnd.program_id)
        rnd.status = "failed"
        rnd.error = f"{type(exc).__name__}: {exc}"[:2000]
        rnd.finished_at = utcnow()
        program.status = "active"
        _event(db, program, "round_failed", claim=rnd.error or "unknown error", round_id=rnd.id)
        db.commit()
        return rnd
    finally:
        if supervisor.provider == PROVIDER_DEVIN:
            supervisor.close()


def start_round(db: Session, program: DrugProgram) -> ProgramRound:
    """Queue a round for the program's current stage."""
    number = (
        db.scalar(
            select(ProgramRound)
            .where(ProgramRound.program_id == program.id)
            .order_by(ProgramRound.number.desc())
            .limit(1)
        )
    )
    rnd = ProgramRound(
        program_id=program.id,
        stage=program.current_stage,
        number=(number.number + 1) if number else 1,
    )
    db.add(rnd)
    db.flush()
    return rnd


def economics_for(db: Session, program: DrugProgram) -> dict:
    created = program.created_at
    if created.tzinfo is None:  # SQLite hands back naive datetimes
        created = created.replace(tzinfo=UTC)
    months = max(0.1, (utcnow() - created).total_seconds() / (86400 * 30.44))
    return program_economics(
        current_stage=program.current_stage,
        acus_used=program.acus_used,
        cycles_run=program.rounds_run,
        assays_ingested=program.assays_ingested,
        months_elapsed=round(months, 2),
    )
