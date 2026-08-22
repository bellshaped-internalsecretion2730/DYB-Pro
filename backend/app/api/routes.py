"""HTTP API. Everything the workspace UI and external clients need."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AgentRunOut,
    AutonomyDecisionOut,
    AutonomyLedgerOut,
    BranchCreate,
    CommitOut,
    CycleCreate,
    CycleOut,
    FilterPerformanceOut,
    MeasuredResultCreate,
    MeasuredResultOut,
    MergeRequest,
    ObservationOut,
    OverrideIn,
    OverrideOut,
    ProblemSpecCreate,
    ProblemSpecOut,
    ProjectCreate,
    ProjectOut,
    ProviderStatus,
)
from app.config import get_settings
from app.db import get_db
from app.devin.runner import provider_status
from app.models import (
    AgentRun,
    Artifact,
    AutonomyDecision,
    Branch,
    DesignCycle,
    MeasuredResult,
    Observation,
    ProblemSpec,
    Project,
    ProteinCommit,
    ResearchEvent,
    User,
    utcnow,
)
from app.security import (
    can_access_project,
    current_user,
    enforce_quota,
    record_usage,
    require_project,
    require_role,
    usage_snapshot,
)
from app.services import autonomy, calibration, economics, ingest, learning, problem, research, wetlab
from app.storage import store
from app.toolkit import sequence as seqlib
from app.versioning import (
    MergeConflict,
    branch_from,
    diff_commits,
    export_graph,
    get_branch,
    lineage,
    merge_branches,
)
from app.worker import cancel_cycle_task, enqueue_cycle, enqueue_research_event

logger = logging.getLogger(__name__)
router = APIRouter()

viewer = Depends(require_role("viewer"))
scientist = Depends(require_role("scientist"))


# --------------------------------------------------------------------- system


@router.get("/healthz", tags=["system"])
def healthz(db: Session = Depends(get_db)) -> dict:
    db.execute(select(func.count(User.id)))
    return {"status": "ok"}


@router.get("/providers", response_model=ProviderStatus, tags=["system"])
def providers() -> ProviderStatus:
    return ProviderStatus(**provider_status())


@router.get("/me", tags=["system"])
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return usage_snapshot(db, user)


# -------------------------------------------------------------------- projects


def _project_out(db: Session, project: Project) -> ProjectOut:
    commit_count = db.scalar(
        select(func.count(ProteinCommit.id)).where(ProteinCommit.project_id == project.id)
    )
    cycle_count = db.scalar(
        select(func.count(DesignCycle.id)).where(DesignCycle.project_id == project.id)
    )
    branches = list(db.scalars(select(Branch).where(Branch.project_id == project.id)))
    main = next((b for b in branches if b.name == "main"), None)
    return ProjectOut(
        id=project.id,
        name=project.name,
        goal=project.goal,
        target_name=project.target_name,
        target_sequence=project.target_sequence,
        is_demo=project.is_demo,
        created_at=project.created_at,
        commit_count=int(commit_count or 0),
        cycle_count=int(cycle_count or 0),
        branches=[b.name for b in branches],
        head_commit_id=main.head_commit_id if main else None,
    )


def _get_commit(db: Session, user: User, commit_id: str) -> ProteinCommit:
    """Load a commit, treating one from an inaccessible project as non-existent."""
    commit = db.get(ProteinCommit, commit_id)
    if commit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "commit not found")
    require_project(db, user, commit.project_id)
    return commit


@router.get("/projects", response_model=list[ProjectOut], tags=["projects"])
def list_projects(db: Session = Depends(get_db), user: User = viewer) -> list[ProjectOut]:
    projects = list(db.scalars(select(Project).order_by(Project.created_at.desc())))
    return [_project_out(db, p) for p in projects if can_access_project(user, p)]


@router.post("/projects", response_model=ProjectOut, tags=["projects"])
def create_project(
    payload: ProjectCreate, db: Session = Depends(get_db), user: User = scientist
) -> ProjectOut:
    project = Project(
        name=payload.name,
        goal=payload.goal,
        target_name=payload.target_name,
        target_sequence=seqlib.clean_sequence(payload.target_sequence)
        if payload.target_sequence
        else "",
        owner_id=user.id,
    )
    db.add(project)
    db.flush()
    get_branch(db, project.id, "main", create=True)
    db.commit()
    return _project_out(db, project)


@router.get("/projects/{project_id}", response_model=ProjectOut, tags=["projects"])
def get_project(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> ProjectOut:
    return _project_out(db, require_project(db, user, project_id))


@router.post("/projects/{project_id}/uploads", tags=["projects"])
async def upload(
    project_id: str,
    file: UploadFile = File(...),
    branch: str = "main",
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    project = require_project(db, user, project_id, write=True)
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")
    try:
        result = ingest.ingest_file(db, project, file.filename or "upload.txt", data, branch)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    event = research.enqueue(db, project.id, "upload", ref=file.filename, detail=str(result))
    db.commit()
    enqueue_research_event(event.id)
    return result


@router.get("/projects/{project_id}/artifacts", tags=["projects"])
def list_artifacts(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[dict]:
    require_project(db, user, project_id)
    rows = db.scalars(
        select(Artifact)
        .where(Artifact.project_id == project_id)
        .order_by(Artifact.created_at.desc())
    )
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "filename": a.filename,
            "key": a.key,
            "backend": a.backend,
            "sha256": a.sha256,
            "size": a.size,
            "created_at": a.created_at,
        }
        for a in rows
    ]


@router.get("/artifacts/{artifact_id}/content", tags=["projects"])
def artifact_content(
    artifact_id: str, db: Session = Depends(get_db), user: User = viewer
) -> Response:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    require_project(db, user, artifact.project_id)
    try:
        data = store.get(artifact.key, backend=artifact.backend)
    except OSError as exc:
        raise HTTPException(status.HTTP_410_GONE, f"artifact bytes unavailable: {exc}") from exc
    return Response(content=data, media_type=artifact.content_type)


# ---------------------------------------------------------------------- cycles


def _cycle_out(cycle: DesignCycle) -> CycleOut:
    return CycleOut(
        id=cycle.id,
        project_id=cycle.project_id,
        brief=cycle.brief,
        round=cycle.round,
        status=cycle.status,
        provider=cycle.provider,
        branch=cycle.branch,
        summary=cycle.summary,
        error=cycle.error,
        acu_limit=cycle.acu_limit,
        acus_used=cycle.acus_used,
        orchestrator_session_url=cycle.orchestrator_session_url,
        plan=cycle.plan or {},
        created_at=cycle.created_at,
        finished_at=cycle.finished_at,
    )


@router.post("/projects/{project_id}/cycles", response_model=CycleOut, tags=["cycles"])
def start_cycle(
    project_id: str,
    payload: CycleCreate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> CycleOut:
    project = require_project(db, user, project_id, write=True)
    enforce_quota(db, user, payload.acu_limit)
    branch = get_branch(db, project.id, payload.branch, create=True)
    if not branch.head_commit_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "upload a sequence or structure before running a design cycle",
        )
    last_round = db.scalar(
        select(func.max(DesignCycle.round)).where(DesignCycle.project_id == project.id)
    )
    cycle = DesignCycle(
        project_id=project.id,
        user_id=user.id,
        brief=payload.brief,
        round=int(last_round or 0) + 1,
        branch=payload.branch,
        acu_limit=payload.acu_limit,
        status="queued",
    )
    db.add(cycle)
    db.commit()
    enqueue_cycle(cycle.id)
    db.refresh(cycle)
    record_usage(db, user.id, "cycle", 1.0, cycle.id)
    db.commit()
    return _cycle_out(cycle)


@router.get("/projects/{project_id}/cycles", response_model=list[CycleOut], tags=["cycles"])
def list_cycles(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[CycleOut]:
    require_project(db, user, project_id)
    rows = db.scalars(
        select(DesignCycle)
        .where(DesignCycle.project_id == project_id)
        .order_by(DesignCycle.created_at.desc())
    )
    return [_cycle_out(c) for c in rows]


def _get_cycle(db: Session, user: User, cycle_id: str, write: bool = False) -> DesignCycle:
    cycle = db.get(DesignCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cycle not found")
    require_project(db, user, cycle.project_id, write=write)
    return cycle


@router.get("/cycles/{cycle_id}", response_model=CycleOut, tags=["cycles"])
def get_cycle(cycle_id: str, db: Session = Depends(get_db), user: User = viewer) -> CycleOut:
    return _cycle_out(_get_cycle(db, user, cycle_id))


@router.get("/cycles/{cycle_id}/agents", response_model=list[AgentRunOut], tags=["cycles"])
def cycle_agents(
    cycle_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[AgentRunOut]:
    _get_cycle(db, user, cycle_id)
    rows = db.scalars(
        select(AgentRun).where(AgentRun.cycle_id == cycle_id).order_by(AgentRun.created_at)
    )
    return [
        AgentRunOut(
            id=r.id,
            role=r.role,
            task=r.task,
            provider=r.provider,
            status=r.status,
            devin_status=r.devin_status,
            devin_session_url=r.devin_session_url,
            playbook_id=r.playbook_id,
            tags=r.tags or [],
            attempts=r.attempts,
            acu_limit=r.acu_limit,
            acus=r.acus,
            structured_output=r.structured_output or {},
            log=r.log or [],
            error=r.error,
            created_at=r.created_at,
            finished_at=r.finished_at,
        )
        for r in rows
    ]


@router.get("/cycles/{cycle_id}/shortlist", tags=["cycles"])
def cycle_shortlist(cycle_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    cycle = _get_cycle(db, user, cycle_id)
    if not cycle.shortlist:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cycle is '{cycle.status}', no shortlist yet")
    return cycle.shortlist


@router.post("/cycles/{cycle_id}/cancel", tags=["cycles"])
def cancel_cycle(
    cycle_id: str,
    reason: str = "cancelled by scientist",
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    cycle = _get_cycle(db, user, cycle_id, write=True)
    if cycle.status in {"committed", "partial", "failed", "cancelled"}:
        return {"status": cycle.status, "cancelled": 0}
    try:
        cancel_cycle_task.delay(cycle.id, reason)
    except Exception:  # broker down
        cancel_cycle_task.apply(args=[cycle.id, reason])
    db.refresh(cycle)
    return {"status": "cancelling", "cycle_id": cycle.id}


@router.get("/projects/{project_id}/timeline", response_model=list[ObservationOut], tags=["cycles"])
def timeline(
    project_id: str,
    limit: int = Query(default=120, le=500),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> list[ObservationOut]:
    require_project(db, user, project_id)
    rows = db.scalars(
        select(Observation)
        .where(Observation.project_id == project_id)
        .order_by(Observation.created_at.desc())
        .limit(limit)
    )
    return [
        ObservationOut(
            id=o.id,
            cycle_id=o.cycle_id,
            role=o.role,
            kind=o.kind,
            summary=o.summary,
            payload=o.payload or {},
            created_at=o.created_at,
        )
        for o in rows
    ]


@router.get("/projects/{project_id}/memory", tags=["cycles"])
def project_memory(project_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    project = require_project(db, user, project_id)
    digest = learning.history_digest(db, project)
    return {
        "digest": digest,
        "prompt_view": learning.digest_to_prompt(digest),
        "calibration": calibration.project_calibration(db, project.id),
    }


# ------------------------------------------------------------ measured results


def _result_out(r: MeasuredResult) -> MeasuredResultOut:
    return MeasuredResultOut(
        id=r.id,
        project_id=r.project_id,
        commit_id=r.commit_id,
        assay=r.assay,
        objective=r.objective,
        readout=r.readout,
        value=r.value,
        unit=r.unit,
        higher_is_better=r.higher_is_better,
        outcome=r.outcome,
        origin=r.origin,
        notes=r.notes,
        created_at=r.created_at,
    )


@router.post(
    "/projects/{project_id}/results", response_model=MeasuredResultOut, tags=["wetlab"]
)
def add_result(
    project_id: str,
    payload: MeasuredResultCreate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> MeasuredResultOut:
    """Ingest a wet-lab measurement so the next cycle can learn from it.

    The commit itself is never modified: measurements live alongside the immutable design and feed
    the history digest and the drift calibration.
    """
    require_project(db, user, project_id, write=True)
    commit = _get_commit(db, user, payload.commit_id)
    if commit.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "commit not found")
    result = MeasuredResult(
        project_id=project_id,
        commit_id=commit.id,
        assay=payload.assay,
        objective=payload.objective,
        readout=payload.readout,
        value=payload.value,
        unit=payload.unit,
        higher_is_better=payload.higher_is_better,
        outcome=payload.outcome,
        origin=payload.origin,
        notes=payload.notes,
        reported_by=user.id,
    )
    db.add(result)
    db.flush()
    db.add(
        Observation(
            project_id=project_id,
            role="wetlab",
            kind="measured_result",
            summary=(
                f"{payload.assay} on {commit.label or commit.id[:12]}: "
                f"{payload.value} {payload.unit} ({payload.outcome}, {payload.origin})"
            )[:4000],
            payload={
                "commit": commit.id,
                "objective": payload.objective,
                "value": payload.value,
                "unit": payload.unit,
                "outcome": payload.outcome,
                "origin": payload.origin,
            },
        )
    )
    event = research.enqueue(
        db,
        project_id,
        "measured_result",
        ref=commit.id,
        detail=f"{payload.assay} {payload.value} {payload.unit} ({payload.outcome})",
    )
    db.commit()
    db.refresh(result)
    enqueue_research_event(event.id)
    return _result_out(result)


@router.get(
    "/projects/{project_id}/results", response_model=list[MeasuredResultOut], tags=["wetlab"]
)
def list_results(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[MeasuredResultOut]:
    require_project(db, user, project_id)
    rows = db.scalars(
        select(MeasuredResult)
        .where(MeasuredResult.project_id == project_id)
        .order_by(MeasuredResult.created_at.desc())
    )
    return [_result_out(r) for r in rows]


@router.get("/projects/{project_id}/calibration", tags=["wetlab"])
def project_drift(project_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    """Proxy-vs-measurement agreement for this project, plus what is still missing to have any."""
    require_project(db, user, project_id)
    return calibration.project_calibration(db, project_id)


def _problem_spec_out(db: Session, spec: ProblemSpec) -> ProblemSpecOut:
    return ProblemSpecOut(
        id=spec.id,
        project_id=spec.project_id,
        version=spec.version,
        status=spec.status,
        objectives=problem.objectives_with_calibration(db, spec),
        hard_constraints=spec.hard_constraints or [],
        deciding_objective=spec.deciding_objective,
        target_readout=spec.target_readout,
        notes=spec.notes,
        created_by=spec.created_by,
        created_at=spec.created_at,
        superseded_at=spec.superseded_at,
    )


@router.post(
    "/projects/{project_id}/problem-spec",
    response_model=ProblemSpecOut,
    tags=["problem-spec"],
)
def create_problem_spec(
    project_id: str,
    payload: ProblemSpecCreate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> ProblemSpecOut:
    require_project(db, user, project_id, write=True)
    try:
        validated = problem.validate_spec(payload.model_dump())
    except problem.SpecError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    active = db.scalar(
        select(ProblemSpec).where(
            ProblemSpec.project_id == project_id,
            ProblemSpec.status == "active",
        )
    )
    if active is not None:
        active.status = "superseded"
        active.superseded_at = utcnow()
    version = (
        db.scalar(
            select(func.max(ProblemSpec.version)).where(ProblemSpec.project_id == project_id)
        )
        or 0
    ) + 1
    spec = ProblemSpec(
        project_id=project_id,
        version=int(version),
        status="active",
        objectives=validated["objectives"],
        hard_constraints=validated["hard_constraints"],
        deciding_objective=validated["deciding_objective"],
        target_readout=validated["target_readout"],
        notes=validated["notes"],
        created_by=user.id,
    )
    db.add(spec)
    db.commit()
    db.refresh(spec)
    return _problem_spec_out(db, spec)


@router.get(
    "/projects/{project_id}/problem-spec",
    response_model=ProblemSpecOut,
    tags=["problem-spec"],
)
def get_problem_spec(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> ProblemSpecOut:
    require_project(db, user, project_id)
    spec = db.scalar(
        select(ProblemSpec).where(
            ProblemSpec.project_id == project_id,
            ProblemSpec.status == "active",
        )
    )
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project has no active problem spec")
    return _problem_spec_out(db, spec)


@router.get(
    "/projects/{project_id}/problem-spec/history",
    response_model=list[ProblemSpecOut],
    tags=["problem-spec"],
)
def problem_spec_history(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[ProblemSpecOut]:
    require_project(db, user, project_id)
    specs = db.scalars(
        select(ProblemSpec)
        .where(ProblemSpec.project_id == project_id)
        .order_by(ProblemSpec.version.desc())
        .limit(50)
    )
    return [_problem_spec_out(db, spec) for spec in specs]


def _autonomy_decision_out(decision: AutonomyDecision) -> AutonomyDecisionOut:
    return AutonomyDecisionOut(
        id=decision.id,
        project_id=decision.project_id,
        cycle_id=decision.cycle_id,
        commit_id=decision.commit_id,
        step=decision.step,
        decision=decision.decision,
        actor=decision.actor,
        autonomy=decision.autonomy,
        basis=decision.basis or {},
        reversible=decision.reversible,
        confidence_basis=decision.confidence_basis,
        overridden_by=decision.overridden_by,
        override_reason=decision.override_reason,
        overridden_at=decision.overridden_at,
        created_at=decision.created_at,
    )


@router.get(
    "/projects/{project_id}/autonomy-ledger",
    response_model=AutonomyLedgerOut,
    tags=["autonomy"],
)
def autonomy_ledger(
    project_id: str,
    cycle_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> AutonomyLedgerOut:
    require_project(db, user, project_id)
    decisions = autonomy.ledger(db, project_id, cycle_id=cycle_id, limit=limit)
    return AutonomyLedgerOut(
        summary=autonomy.summary(db, project_id),
        decisions=[_autonomy_decision_out(decision) for decision in decisions],
    )


@router.post(
    "/projects/{project_id}/autonomy-ledger/{decision_id}/override",
    response_model=OverrideOut,
    tags=["autonomy"],
)
def override_autonomy_decision(
    project_id: str,
    decision_id: str,
    payload: OverrideIn,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> OverrideOut:
    """recorded, not reverted: this annotates the decision and does not change any commit,
    branch head or pack."""
    require_project(db, user, project_id, write=True)
    decision = db.get(AutonomyDecision, decision_id)
    if decision is None or decision.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "autonomy decision not found")
    if not payload.reason.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "override reason must be non-empty")
    try:
        decision = autonomy.override(db, decision_id, user.id, payload.reason)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    db.refresh(decision)
    return OverrideOut(
        **_autonomy_decision_out(decision).model_dump(),
        effect=(
            "recorded, not reverted: this annotates the decision and does not change any commit, "
            "branch head or pack"
        ),
    )


@router.get(
    "/projects/{project_id}/filter-performance",
    response_model=FilterPerformanceOut,
    tags=["wetlab"],
)
def filter_performance(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> FilterPerformanceOut:
    """Filter confusion matrix from one collapsed observation per design."""
    require_project(db, user, project_id)
    spec = db.scalar(
        select(ProblemSpec).where(
            ProblemSpec.project_id == project_id,
            ProblemSpec.status == "active",
        )
    )
    rows = db.execute(
        select(ProteinCommit, MeasuredResult)
        .join(MeasuredResult, MeasuredResult.commit_id == ProteinCommit.id)
        .where(
            ProteinCommit.project_id == project_id,
            MeasuredResult.project_id == project_id,
        )
    ).all()
    return FilterPerformanceOut(**economics.filter_performance(rows, spec=spec))


# ------------------------------------------------------------- research daemon


@router.get("/research/daemon", tags=["research"])
def research_daemon(db: Session = Depends(get_db), user: User = viewer) -> dict:
    """Whether the daemon is alive, what it is waiting on, and which provider it would use."""
    return research.daemon_status(db)


@router.get("/projects/{project_id}/research", tags=["research"])
def project_research(
    project_id: str,
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
    """Research events for this lineage plus the append-only cache they were answered from."""
    require_project(db, user, project_id)
    return research.project_research(db, project_id, limit=limit)


@router.post("/projects/{project_id}/research", tags=["research"])
def trigger_research(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    """Ask the daemon to look now. Coalesces into a queued event rather than starting a second one."""
    require_project(db, user, project_id, write=True)
    event = research.enqueue(db, project_id, "manual", detail=f"requested by {user.email}")
    db.commit()
    enqueue_research_event(event.id)
    db.refresh(event)
    return research.event_payload(event)


@router.get("/research/events/{event_id}", tags=["research"])
def research_event(event_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    event = db.get(ResearchEvent, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "research event not found")
    require_project(db, user, event.project_id)
    return research.event_payload(event)


# -------------------------------------------------------------------- versions


def _commit_out(c: ProteinCommit) -> CommitOut:
    return CommitOut(
        id=c.id,
        short_id=c.id[:12],
        project_id=c.project_id,
        branch=c.branch,
        label=c.label,
        message=c.message,
        sequence=c.sequence,
        structure_key=c.structure_key,
        structure_source=c.structure_source,
        parent_ids=c.parent_ids or [],
        mutations=c.mutations or [],
        scores=c.scores or {},
        uncertainty=c.uncertainty or {},
        filters=c.filters or {},
        rationale=c.rationale,
        agent_role=c.agent_role,
        provider=c.provider,
        devin_session_url=c.devin_session_url,
        prompt=c.prompt,
        citations=c.citations or [],
        cycle_round=c.cycle_round,
        created_at=c.created_at,
    )


@router.get("/projects/{project_id}/graph", tags=["versions"])
def project_graph(project_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    require_project(db, user, project_id)
    return export_graph(db, project_id)


@router.get("/projects/{project_id}/commits", response_model=list[CommitOut], tags=["versions"])
def list_commits(
    project_id: str,
    branch: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> list[CommitOut]:
    require_project(db, user, project_id)
    stmt = select(ProteinCommit).where(ProteinCommit.project_id == project_id)
    if branch:
        stmt = stmt.where(ProteinCommit.branch == branch)
    rows = db.scalars(stmt.order_by(ProteinCommit.created_at.desc()).limit(limit))
    return [_commit_out(c) for c in rows]


@router.get("/commits/{commit_id}", response_model=CommitOut, tags=["versions"])
def get_commit(commit_id: str, db: Session = Depends(get_db), user: User = viewer) -> CommitOut:
    return _commit_out(_get_commit(db, user, commit_id))


@router.get("/commits/{commit_id}/lineage", tags=["versions"])
def commit_lineage(
    commit_id: str,
    direction: str = Query(default="ancestors", pattern="^(ancestors|descendants)$"),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
    _get_commit(db, user, commit_id)
    chain = lineage(db, commit_id, direction)
    return {
        "commit_id": commit_id,
        "direction": direction,
        "lineage": [_commit_out(c).model_dump() for c in chain],
    }


@router.get("/commits/{commit_id}/structure", tags=["versions"])
def commit_structure(
    commit_id: str, db: Session = Depends(get_db), user: User = viewer
) -> Response:
    commit = _get_commit(db, user, commit_id)
    if not commit.structure_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no structure for this commit")
    artifact = db.scalar(select(Artifact).where(Artifact.key == commit.structure_key))
    backend = artifact.backend if artifact else "s3"
    try:
        data = store.get(commit.structure_key, backend=backend)
    except OSError as exc:
        raise HTTPException(status.HTTP_410_GONE, f"structure unavailable: {exc}") from exc
    return Response(
        content=data,
        media_type="chemical/x-pdb",
        headers={"X-DYB-Pro-Structure-Source": commit.structure_source},
    )


@router.get("/diff", tags=["versions"])
def diff(
    a: str,
    b: str,
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
    # Both sides are resolved through the tenancy check first, so a diff cannot be used to read a
    # commit from a project the caller has no access to.
    _get_commit(db, user, a)
    _get_commit(db, user, b)
    try:
        return diff_commits(db, a, b)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown commit") from exc


@router.post("/projects/{project_id}/branches", tags=["versions"])
def create_branch(
    project_id: str,
    payload: BranchCreate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    require_project(db, user, project_id, write=True)
    try:
        branch = branch_from(db, project_id, payload.name, payload.from_commit)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return {"name": branch.name, "head_commit_id": branch.head_commit_id}


@router.post("/projects/{project_id}/merge", tags=["versions"])
def merge(
    project_id: str,
    payload: MergeRequest,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    require_project(db, user, project_id, write=True)
    try:
        commit = merge_branches(db, project_id, payload.ours, payload.theirs, payload.message)
    except MergeConflict as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"error": "merge conflict", "conflicts": exc.conflicts},
        ) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    db.commit()
    return _commit_out(commit).model_dump()


# --------------------------------------------------------------------- exports


@router.get("/cycles/{cycle_id}/export", tags=["exports"])
def export_cycle(
    cycle_id: str,
    fmt: str = Query(default="csv", pattern="^(csv|json|fasta)$"),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> Response:
    cycle = _get_cycle(db, user, cycle_id)
    pack = (cycle.shortlist or {}).get("pack")
    if not pack:
        raise HTTPException(status.HTTP_409_CONFLICT, f"cycle is '{cycle.status}', nothing to export")
    stem = f"dyb-pro-{cycle.id[:8]}-round{cycle.round}"
    if fmt == "json":
        return Response(
            content=json.dumps(cycle.shortlist, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{stem}.json"'},
        )
    if fmt == "fasta":
        return PlainTextResponse(
            wetlab.to_fasta(pack),
            headers={"Content-Disposition": f'attachment; filename="{stem}.fasta"'},
        )
    return PlainTextResponse(
        wetlab.to_csv(pack),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'},
    )


@router.get("/projects/{project_id}/export/graph", tags=["exports"])
def export_project_graph(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> Response:
    require_project(db, user, project_id)
    graph = export_graph(db, project_id)
    return Response(
        content=json.dumps(graph, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="dyb-pro-graph-{project_id[:8]}.json"'},
    )


@router.get("/projects/{project_id}/export/fasta", tags=["exports"])
def export_project_fasta(
    project_id: str,
    branch: str | None = None,
    db: Session = Depends(get_db),
    user: User = viewer,
) -> PlainTextResponse:
    require_project(db, user, project_id)
    stmt = select(ProteinCommit).where(ProteinCommit.project_id == project_id)
    if branch:
        stmt = stmt.where(ProteinCommit.branch == branch)
    commits = list(db.scalars(stmt.order_by(ProteinCommit.created_at)))
    records = [
        (
            f"{c.label} commit={c.id[:12]} round={c.cycle_round} "
            f"composite={c.scores.get('composite_score', 'na')} agent={c.agent_role}",
            c.sequence,
        )
        for c in commits
    ]
    return PlainTextResponse(
        seqlib.to_fasta(records),
        headers={"Content-Disposition": f'attachment; filename="dyb-pro-{project_id[:8]}.fasta"'},
    )


# ------------------------------------------------------------------------ demo


@router.post("/demo/seed", tags=["demo"])
def seed_demo(db: Session = Depends(get_db), user: User = scientist) -> dict:
    from app.seed import seed_demo_project

    project = seed_demo_project(db, owner_id=user.id)
    db.commit()
    return {"project": _project_out(db, project).model_dump()}


@router.get("/config", tags=["system"])
def public_config() -> dict:
    settings = get_settings()
    return {
        "app_env": settings.app_env,
        "devin_app_base": settings.devin_app_base,
        "provider": provider_status()["provider"],
    }
