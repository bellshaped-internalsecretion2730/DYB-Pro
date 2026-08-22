"""Pharmakon HTTP API: programs, molecules, rounds, gates, approvals, evidence, dossier.

The shape of this router mirrors how the program actually moves: you create a program, run rounds,
and the only things a human must touch are the gates the autonomy level reserves for them.
"""

from __future__ import annotations

import csv
import io
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.pharma_schemas import (
    ApprovalRequest,
    AssayIngest,
    AutonomyUpdate,
    DaemonUpdate,
    ExperimentOut,
    GateOut,
    MoleculeOut,
    ProgramCreate,
    ProgramOut,
    ResearchEventOut,
    ResearchIngest,
    RoundOut,
)
from app.chem.smiles import SmilesError
from app.config import get_settings
from app.db import get_db
from app.devin import daemon as daemonlib
from app.devin.client import DevinClient, DevinNotConfigured
from app.models import (
    AssayResult,
    DrugProgram,
    Experiment,
    GateDecision,
    MoleculeCommit,
    ProgramRound,
    Project,
    ResearchEvent,
    User,
    utcnow,
)
from app.pharma.dossier import build_dossier, dossier_markdown
from app.pharma.economics import portfolio_view
from app.pharma.metrics import prediction_drift
from app.pharma.stages import AUTONOMY_LEVELS, ladder
from app.pharma.stages import stage as stage_spec
from app.security import enforce_quota, record_usage, require_role
from app.services import program as programlib
from app.worker import enqueue_round

logger = logging.getLogger(__name__)
router = APIRouter()

viewer = Depends(require_role("viewer"))
scientist = Depends(require_role("scientist"))


# ------------------------------------------------------------------ serialisers


def _program_out(db: Session, program: DrugProgram) -> ProgramOut:
    spec = stage_spec(program.current_stage)
    molecules = db.scalar(
        select(func.count(MoleculeCommit.id)).where(MoleculeCommit.program_id == program.id)
    )
    return ProgramOut(
        id=program.id,
        project_id=program.project_id,
        name=program.name,
        target_name=program.target_name,
        indication=program.indication,
        objective=program.objective,
        current_stage=program.current_stage,
        stage_order=spec.order,
        stage_name=spec.name,
        autonomy_level=program.autonomy_level,
        autonomy_label=AUTONOMY_LEVELS.get(program.autonomy_level, "unknown"),
        status=program.status,
        provider=program.provider,
        rounds_run=program.rounds_run,
        stage_cycles=program.stage_cycles,
        acus_used=program.acus_used,
        assays_ingested=program.assays_ingested,
        molecule_count=int(molecules or 0),
        daemon_enabled=program.daemon_enabled,
        knowledge_note_id=program.knowledge_note_id,
        candidate_molecule_id=program.candidate_molecule_id,
        backup_molecule_id=program.backup_molecule_id,
        created_at=program.created_at,
        last_research_at=program.last_research_at,
    )


def _molecule_out(m: MoleculeCommit) -> MoleculeOut:
    return MoleculeOut(
        id=m.id,
        short_id=m.id[:12],
        program_id=m.program_id,
        label=m.label,
        smiles=m.smiles,
        formula=m.formula,
        scaffold_key=m.scaffold_key,
        parent_ids=m.parent_ids or [],
        stage=m.stage,
        composite_score=m.composite_score,
        verdict=m.verdict,
        rationale=m.rationale,
        agent_role=m.agent_role,
        provider=m.provider,
        devin_session_url=m.devin_session_url,
        citations=m.citations or [],
        evaluation=m.evaluation or {},
        created_at=m.created_at,
    )


def _round_out(r: ProgramRound) -> RoundOut:
    return RoundOut(
        id=r.id,
        program_id=r.program_id,
        stage=r.stage,
        number=r.number,
        status=r.status,
        provider=r.provider,
        summary=r.summary,
        error=r.error,
        acus_used=r.acus_used,
        plan=r.plan or {},
        metrics=r.metrics or {},
        findings=r.findings or {},
        gate=r.gate or {},
        experiment_plan=r.experiment_plan or {},
        orchestrator_session_url=r.orchestrator_session_url,
        created_at=r.created_at,
        finished_at=r.finished_at,
    )


def _gate_out(g: GateDecision) -> GateOut:
    return GateOut(
        id=g.id,
        program_id=g.program_id,
        round_id=g.round_id,
        stage=g.stage,
        decision=g.decision,
        score=g.score,
        criteria=g.criteria or [],
        blocking_failures=g.blocking_failures or [],
        rationale=g.rationale,
        recommended_actions=g.recommended_actions or [],
        next_stage=g.next_stage,
        autonomy_level=g.autonomy_level,
        requires_approval=g.requires_approval,
        approval_reason=g.approval_reason,
        approval_status=g.approval_status,
        approved_by=g.approved_by,
        approval_note=g.approval_note,
        applied=g.applied,
        created_at=g.created_at,
        decided_at=g.decided_at,
    )


def _get_program(db: Session, program_id: str) -> DrugProgram:
    program = db.get(DrugProgram, program_id)
    if program is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    return program


# ---------------------------------------------------------------------- static


@router.get("/pharma/ladder", tags=["pharma"])
def stage_ladder() -> dict:
    return {
        "stages": ladder(),
        "autonomy_levels": AUTONOMY_LEVELS,
        "caveat": (
            "All in-silico values are deterministic heuristics and ranking hypotheses. They are "
            "not experimental evidence, docking, free-energy calculations or validated human PK."
        ),
    }


# -------------------------------------------------------------------- programs


@router.post("/projects/{project_id}/programs", response_model=ProgramOut, tags=["pharma"])
def create_program(
    project_id: str,
    payload: ProgramCreate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> ProgramOut:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    program = DrugProgram(
        project_id=project.id,
        name=payload.name,
        target_name=payload.target_name or project.target_name,
        target_sequence=payload.target_sequence or project.target_sequence,
        pocket_residues=payload.pocket_residues,
        indication=payload.indication,
        objective=payload.objective or project.goal,
        autonomy_level=payload.autonomy_level,
    )
    db.add(program)
    db.flush()
    if payload.seed_smiles:
        try:
            commits, rejected = programlib.commit_molecules(
                db,
                program,
                [{"smiles": s, "rationale": "seeded by the scientist"} for s in payload.seed_smiles],
                stage_key=program.current_stage,
                round_id=None,
                provider="human",
            )
        except SmilesError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid SMILES: {exc}") from exc
        if rejected:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"error": "unparseable SMILES", "rejected": rejected},
            )
        logger.info("program %s seeded with %d molecules", program.id, len(commits))
    db.commit()
    return _program_out(db, program)


@router.get("/projects/{project_id}/programs", response_model=list[ProgramOut], tags=["pharma"])
def list_project_programs(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[ProgramOut]:
    rows = db.scalars(
        select(DrugProgram)
        .where(DrugProgram.project_id == project_id)
        .order_by(DrugProgram.created_at.desc())
    )
    return [_program_out(db, p) for p in rows]


@router.get("/pharma/programs", response_model=list[ProgramOut], tags=["pharma"])
def list_programs(db: Session = Depends(get_db), user: User = viewer) -> list[ProgramOut]:
    rows = db.scalars(select(DrugProgram).order_by(DrugProgram.created_at.desc()))
    return [_program_out(db, p) for p in rows]


@router.get("/pharma/programs/{program_id}", tags=["pharma"])
def get_program(program_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    program = _get_program(db, program_id)
    evaluations = programlib.portfolio(db, program.id)
    assays = programlib.assay_rows(db, program.id)
    latest_gate = db.scalar(
        select(GateDecision)
        .where(GateDecision.program_id == program.id)
        .order_by(GateDecision.created_at.desc())
        .limit(1)
    )
    gate = _gate_out(latest_gate).model_dump() if latest_gate else None
    return {
        "program": _program_out(db, program).model_dump(),
        "stage": next((s for s in ladder() if s["key"] == program.current_stage), None),
        "ladder": ladder(),
        "latest_gate": gate,
        "gate_history": programlib.gate_history(db, program.id),
        "portfolio": evaluations[:20],
        "drift": prediction_drift(evaluations, assays),
        "economics": programlib.economics_for(db, program),
        "daemon": daemonlib.daemon_status(programlib.program_dict(program)),
        "next_action": programlib.next_action(db, program),
    }


@router.post("/pharma/programs/{program_id}/autonomy", response_model=ProgramOut, tags=["pharma"])
def set_autonomy(
    program_id: str,
    payload: AutonomyUpdate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> ProgramOut:
    program = _get_program(db, program_id)
    program.autonomy_level = payload.autonomy_level
    programlib.record_event(
        db,
        program,
        "autonomy_changed",
        claim=f"autonomy set to L{payload.autonomy_level} by {user.email}",
        payload={"autonomy_level": payload.autonomy_level},
    )
    db.commit()
    return _program_out(db, program)


# ---------------------------------------------------------------------- rounds


@router.post("/pharma/programs/{program_id}/advance", response_model=RoundOut, tags=["pharma"])
def advance_program(
    program_id: str,
    acu_limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = scientist,
) -> RoundOut:
    program = _get_program(db, program_id)
    if program.status in {"killed", "completed"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"program is '{program.status}'")
    pending = db.scalar(
        select(GateDecision)
        .where(
            GateDecision.program_id == program.id,
            GateDecision.approval_status == "pending",
        )
        .limit(1)
    )
    if pending is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "a gate decision is awaiting human approval",
                "gate_decision_id": pending.id,
                "reason": pending.approval_reason,
            },
        )
    enforce_quota(db, user, acu_limit)
    rnd = programlib.start_round(db, program)
    db.commit()
    enqueue_round(rnd.id)
    db.refresh(rnd)
    record_usage(db, user.id, "cycle", 1.0, rnd.id)
    db.commit()
    return _round_out(rnd)


@router.get("/pharma/programs/{program_id}/rounds", response_model=list[RoundOut], tags=["pharma"])
def list_rounds(
    program_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[RoundOut]:
    rows = db.scalars(
        select(ProgramRound)
        .where(ProgramRound.program_id == program_id)
        .order_by(ProgramRound.created_at.desc())
    )
    return [_round_out(r) for r in rows]


@router.get("/pharma/rounds/{round_id}", tags=["pharma"])
def get_round(round_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    rnd = db.get(ProgramRound, round_id)
    if rnd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "round not found")
    from app.models import PharmaAgentRun

    runs = db.scalars(
        select(PharmaAgentRun)
        .where(PharmaAgentRun.round_id == round_id)
        .order_by(PharmaAgentRun.created_at)
    ).all()
    return {
        "round": _round_out(rnd).model_dump(),
        "agents": [
            {
                "id": r.id,
                "role": r.role,
                "task": r.task,
                "provider": r.provider,
                "status": r.status,
                "devin_status": r.devin_status,
                "devin_session_url": r.devin_session_url,
                "playbook_id": r.playbook_id,
                "tags": r.tags or [],
                "attempts": r.attempts,
                "acu_limit": r.acu_limit,
                "acus": r.acus,
                "structured_output": r.structured_output or {},
                "log": r.log or [],
                "error": r.error,
                "created_at": r.created_at,
                "finished_at": r.finished_at,
            }
            for r in runs
        ],
    }


# ------------------------------------------------------------------- molecules


@router.get(
    "/pharma/programs/{program_id}/molecules", response_model=list[MoleculeOut], tags=["pharma"]
)
def list_molecules(
    program_id: str,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> list[MoleculeOut]:
    rows = db.scalars(
        select(MoleculeCommit)
        .where(MoleculeCommit.program_id == program_id)
        .order_by(MoleculeCommit.composite_score.desc())
        .limit(limit)
    )
    return [_molecule_out(m) for m in rows]


@router.get("/pharma/molecules/{molecule_id}", tags=["pharma"])
def get_molecule(molecule_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    molecule = db.get(MoleculeCommit, molecule_id)
    if molecule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "molecule not found")
    parents = [
        _molecule_out(p).model_dump()
        for p in (db.get(MoleculeCommit, pid) for pid in molecule.parent_ids or [])
        if p is not None
    ]
    children = db.scalars(
        select(MoleculeCommit).where(MoleculeCommit.program_id == molecule.program_id)
    ).all()
    assays = db.scalars(
        select(AssayResult).where(AssayResult.molecule_hash == molecule.id)
    ).all()
    return {
        "molecule": _molecule_out(molecule).model_dump(),
        "parents": parents,
        "children": [
            _molecule_out(c).model_dump() for c in children if molecule.id in (c.parent_ids or [])
        ],
        "measured": [
            {
                "assay": a.assay,
                "metric": a.metric,
                "value": a.value,
                "unit": a.unit,
                "operator": a.operator,
                "source": a.source,
                "created_at": a.created_at,
            }
            for a in assays
        ],
    }


# ----------------------------------------------------------------------- gates


@router.get("/pharma/programs/{program_id}/gates", response_model=list[GateOut], tags=["pharma"])
def list_gates(program_id: str, db: Session = Depends(get_db), user: User = viewer) -> list[GateOut]:
    rows = db.scalars(
        select(GateDecision)
        .where(GateDecision.program_id == program_id)
        .order_by(GateDecision.created_at.desc())
    )
    return [_gate_out(g) for g in rows]


@router.post("/pharma/gates/{gate_id}/approval", tags=["pharma"])
def decide_gate(
    gate_id: str,
    payload: ApprovalRequest,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    decision = db.get(GateDecision, gate_id)
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "gate decision not found")
    if decision.approval_status not in {"pending"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"gate is already '{decision.approval_status}'"
        )
    program = _get_program(db, decision.program_id)
    decision.approval_status = "approved" if payload.approve else "rejected"
    decision.approved_by = user.email
    decision.approval_note = payload.note[:4000]
    decision.decided_at = utcnow()
    if payload.approve:
        action = programlib.apply_decision(db, program, decision)
    else:
        program.status = "active"
        program.stage_cycles += 1
        action = "rejected by scientist: the program stays in this stage"
    programlib.record_event(
        db,
        program,
        "gate_approval",
        claim=f"{decision.stage} {decision.decision} {decision.approval_status} by {user.email}",
        implication=action,
        payload={"gate_decision_id": decision.id, "note": decision.approval_note},
    )
    db.commit()
    return {"gate": _gate_out(decision).model_dump(), "action": action}


# -------------------------------------------------------- evidence and memory


@router.post("/pharma/programs/{program_id}/assays", tags=["pharma"])
def ingest_assay(
    program_id: str,
    payload: AssayIngest,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    program = _get_program(db, program_id)
    if db.get(MoleculeCommit, payload.molecule_hash) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no molecule commit with that hash in this platform"
        )
    result = AssayResult(
        program_id=program.id,
        molecule_hash=payload.molecule_hash,
        assay=payload.assay,
        metric=payload.metric,
        value=payload.value,
        unit=payload.unit,
        operator=payload.operator,
        source=payload.source,
        notes=payload.notes,
        ingested_by=user.email,
    )
    db.add(result)
    program.assays_ingested += 1
    db.flush()
    evaluations = programlib.portfolio(db, program.id)
    drift = prediction_drift(evaluations, programlib.assay_rows(db, program.id))
    programlib.record_event(
        db,
        program,
        "assay_result",
        claim=f"measured {payload.metric}={payload.value}{payload.unit} for "
        f"{payload.molecule_hash[:12]}",
        citation=payload.source,
        role="wetlab",
        payload={"drift": drift, "assay": payload.model_dump()},
        provider="measured",
    )
    db.commit()
    return {"assay_id": result.id, "drift": drift}


@router.get("/pharma/programs/{program_id}/drift", tags=["pharma"])
def program_drift(program_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    program = _get_program(db, program_id)
    return prediction_drift(
        programlib.portfolio(db, program.id), programlib.assay_rows(db, program.id)
    )


@router.post("/pharma/programs/{program_id}/research", tags=["pharma"])
def ingest_research(
    program_id: str,
    payload: ResearchIngest,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    program = _get_program(db, program_id)
    # A gate-moving number is only accepted with a citation, and it is recorded as a separate
    # `human_evidence` event so the audit trail shows who supplied it rather than implying an
    # agent measured it.
    if payload.metric and not payload.citation:
        raise HTTPException(
            status_code=422,
            detail="a metric value moves a gate, so it is only accepted with a citation",
        )
    event = programlib.record_event(
        db,
        program,
        "human_evidence" if payload.metric else payload.kind,
        claim=payload.claim,
        role=payload.role,
        citation=payload.citation,
        implication=payload.implication,
        payload={"metric": payload.metric, "value": payload.value} if payload.metric else {},
        provider="reported",
    )
    program.last_research_at = utcnow()
    db.commit()
    return {"event_id": event.id, "accepted": True, "metric": payload.metric or None}


@router.get(
    "/pharma/programs/{program_id}/research",
    response_model=list[ResearchEventOut],
    tags=["pharma"],
)
def list_research(
    program_id: str,
    limit: int = Query(default=120, le=500),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> list[ResearchEventOut]:
    rows = db.scalars(
        select(ResearchEvent)
        .where(ResearchEvent.program_id == program_id)
        .order_by(ResearchEvent.created_at.desc())
        .limit(limit)
    )
    return [
        ResearchEventOut(
            id=e.id,
            round_id=e.round_id,
            role=e.role,
            kind=e.kind,
            claim=e.claim,
            citation=e.citation,
            implication=e.implication,
            payload=e.payload or {},
            provider=e.provider,
            created_at=e.created_at,
        )
        for e in rows
    ]


@router.get("/pharma/programs/{program_id}/memory", tags=["pharma"])
def program_memory(program_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    program = _get_program(db, program_id)
    evaluations = programlib.portfolio(db, program.id, limit=5)
    drift = prediction_drift(
        programlib.portfolio(db, program.id), programlib.assay_rows(db, program.id)
    )
    from app.devin import campaign_memory

    digest = campaign_memory.build_digest(
        program=programlib.program_dict(program),
        gate_history=programlib.gate_history(db, program.id),
        exclusions=programlib.exclusions(db, program.id),
        lessons=[],
        drift=drift,
        best_molecules=evaluations,
    )
    return {"digest": digest, "knowledge_note_id": program.knowledge_note_id}


@router.post("/pharma/programs/{program_id}/memory/sync", tags=["pharma"])
def sync_memory(program_id: str, db: Session = Depends(get_db), user: User = scientist) -> dict:
    program = _get_program(db, program_id)
    drift = prediction_drift(
        programlib.portfolio(db, program.id), programlib.assay_rows(db, program.id)
    )
    result = programlib.sync_campaign_memory(db, program, drift)
    db.commit()
    return result


# ----------------------------------------------------------------- experiments


@router.get(
    "/pharma/programs/{program_id}/experiments",
    response_model=list[ExperimentOut],
    tags=["pharma"],
)
def list_experiments(
    program_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[ExperimentOut]:
    rows = db.scalars(
        select(Experiment)
        .where(Experiment.program_id == program_id)
        .order_by(Experiment.created_at.desc(), Experiment.priority)
    )
    return [
        ExperimentOut(
            id=e.id,
            program_id=e.program_id,
            stage=e.stage,
            assay=e.assay,
            endpoint=e.endpoint,
            unit=e.unit,
            gate_metric=e.gate_metric,
            molecules=e.molecules or [],
            predicted_value=e.predicted_value,
            falsification=e.falsification,
            cost_usd=e.cost_usd,
            turnaround_days=e.turnaround_days,
            blocking=e.blocking,
            priority=e.priority,
            status=e.status,
            created_at=e.created_at,
        )
        for e in rows
    ]


@router.post("/pharma/experiments/{experiment_id}/status", tags=["pharma"])
def set_experiment_status(
    experiment_id: str,
    value: str = Query(pattern="^(approved|rejected|running|complete)$"),
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "experiment not found")
    experiment.status = value
    experiment.approved_by = user.email
    db.commit()
    return {"experiment_id": experiment.id, "status": experiment.status}


# --------------------------------------------------------------------- daemon


@router.get("/pharma/programs/{program_id}/daemon", tags=["pharma"])
def get_daemon(program_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    program = _get_program(db, program_id)
    return daemonlib.daemon_status(programlib.program_dict(program))


@router.post("/pharma/programs/{program_id}/daemon", tags=["pharma"])
def set_daemon(
    program_id: str,
    payload: DaemonUpdate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    program = _get_program(db, program_id)
    settings = get_settings()
    result: dict = {"enabled": payload.enabled}
    if payload.enabled:
        try:
            with DevinClient(settings) as client:
                created = daemonlib.ensure_schedule(
                    programlib.program_dict(program),
                    api_base=settings.public_api_base,
                    frequency=payload.frequency,
                    client=client,
                )
        except DevinNotConfigured as exc:
            created = {"created": False, "scheduled_session_id": None, "error": str(exc)}
        program.daemon_enabled = True
        program.daemon_schedule_id = created.get("scheduled_session_id")
        result.update(created)
    else:
        if program.daemon_schedule_id:
            result.update(daemonlib.disable_schedule(program.daemon_schedule_id))
        program.daemon_enabled = False
        program.daemon_schedule_id = None
    programlib.record_event(
        db,
        program,
        "daemon_toggled",
        claim=f"research daemon {'enabled' if payload.enabled else 'disabled'} by {user.email}",
        payload=result,
    )
    db.commit()
    return {**result, "status": daemonlib.daemon_status(programlib.program_dict(program))}


# ------------------------------------------------------- portfolio and exports


@router.get("/pharma/portfolio", tags=["pharma"])
def pharma_portfolio(db: Session = Depends(get_db), user: User = viewer) -> dict:
    programs = list(db.scalars(select(DrugProgram)))
    rows = []
    for program in programs:
        economics = programlib.economics_for(db, program)
        rows.append(
            {
                "id": program.id,
                "name": program.name,
                "target": program.target_name,
                "indication": program.indication,
                "current_stage": program.current_stage,
                "status": program.status,
                "autonomy_level": program.autonomy_level,
                **economics,
            }
        )
    return {"programs": rows, "portfolio": portfolio_view(rows)}


@router.get("/pharma/programs/{program_id}/dossier", tags=["pharma"])
def program_dossier(
    program_id: str,
    fmt: str = Query(default="json", pattern="^(json|markdown)$"),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> Response:
    program = _get_program(db, program_id)
    evaluations = programlib.portfolio(db, program.id)
    assays = programlib.assay_rows(db, program.id)
    latest_round = db.scalar(
        select(ProgramRound)
        .where(ProgramRound.program_id == program.id)
        .order_by(ProgramRound.created_at.desc())
        .limit(1)
    )
    dossier = build_dossier(
        program=programlib.program_dict(program),
        candidate=evaluations[0] if evaluations else None,
        backup=evaluations[1] if len(evaluations) > 1 else None,
        assays=assays,
        gate_history=programlib.gate_history(db, program.id),
        findings=(latest_round.findings if latest_round else {}) or {},
        drift=prediction_drift(evaluations, assays),
        economics=programlib.economics_for(db, program),
    )
    stem = f"pharmakon-{program.id[:8]}-dossier"
    if fmt == "markdown":
        return PlainTextResponse(
            dossier_markdown(dossier),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{stem}.md"'},
        )
    return Response(
        content=json.dumps(dossier, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{stem}.json"'},
    )


def _csv_cell(value: object) -> str:
    """Render one CSV cell, defusing spreadsheet formula injection.

    SMILES and agent-supplied labels legitimately start with characters a spreadsheet reads as a
    formula (`-`, `+`, `=`, `@`), so the value is prefixed rather than rewritten: the text stays
    exactly as recorded and Excel/Sheets treat it as text.
    """
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@router.get("/pharma/programs/{program_id}/export/molecules", tags=["pharma"])
def export_molecules(
    program_id: str, db: Session = Depends(get_db), user: User = viewer
) -> PlainTextResponse:
    program = _get_program(db, program_id)
    rows = db.scalars(
        select(MoleculeCommit)
        .where(MoleculeCommit.program_id == program.id)
        .order_by(MoleculeCommit.composite_score.desc())
    ).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "smiles",
            "commit",
            "label",
            "scaffold",
            "stage",
            "composite_score",
            "verdict",
            "predicted_pkd",
            "molecular_weight",
            "clogp",
            "tpsa",
            "admet_score",
            "sa_score",
            "agent_role",
            "provider",
        ]
    )
    for m in rows:
        ev = m.evaluation or {}
        desc = ev.get("descriptors") or {}
        writer.writerow(
            [
                _csv_cell(v)
                for v in (
                    m.smiles,
                    m.id[:12],
                    m.label,
                    m.scaffold_key[:12],
                    m.stage,
                    m.composite_score,
                    m.verdict,
                    (ev.get("binding") or {}).get("pkd", ""),
                    desc.get("molecular_weight", ""),
                    desc.get("clogp", ""),
                    desc.get("tpsa", ""),
                    (ev.get("admet") or {}).get("admet_score", ""),
                    (ev.get("synthesis") or {}).get("sa_score", ""),
                    m.agent_role,
                    m.provider,
                )
            ]
        )
    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="pharmakon-{program.id[:8]}.csv"'
        },
    )
