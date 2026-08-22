"""HTTP API. Everything the workspace UI and external clients need."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.pharma_routes import router as pharma_router
from app.api.schemas import (
    AgentRunOut,
    BranchCreate,
    CommitOut,
    CycleCreate,
    CycleOut,
    MergeRequest,
    ObservationOut,
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
    Branch,
    DesignCycle,
    Observation,
    Project,
    ProteinCommit,
    User,
)
from app.security import current_user, enforce_quota, record_usage, require_role, usage_snapshot
from app.services import ingest, learning, wetlab
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
from app.worker import cancel_cycle_task, enqueue_cycle

logger = logging.getLogger(__name__)
router = APIRouter()
router.include_router(pharma_router)

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


def _get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.get("/projects", response_model=list[ProjectOut], tags=["projects"])
def list_projects(db: Session = Depends(get_db), user: User = viewer) -> list[ProjectOut]:
    projects = list(db.scalars(select(Project).order_by(Project.created_at.desc())))
    return [_project_out(db, p) for p in projects]


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
    return _project_out(db, _get_project(db, project_id))


@router.post("/projects/{project_id}/uploads", tags=["projects"])
async def upload(
    project_id: str,
    file: UploadFile = File(...),
    branch: str = "main",
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    project = _get_project(db, project_id)
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")
    try:
        result = ingest.ingest_file(db, project, file.filename or "upload.txt", data, branch)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    db.commit()
    return result


@router.get("/projects/{project_id}/artifacts", tags=["projects"])
def list_artifacts(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[dict]:
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
    project = _get_project(db, project_id)
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
    rows = db.scalars(
        select(DesignCycle)
        .where(DesignCycle.project_id == project_id)
        .order_by(DesignCycle.created_at.desc())
    )
    return [_cycle_out(c) for c in rows]


def _get_cycle(db: Session, cycle_id: str) -> DesignCycle:
    cycle = db.get(DesignCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cycle not found")
    return cycle


@router.get("/cycles/{cycle_id}", response_model=CycleOut, tags=["cycles"])
def get_cycle(cycle_id: str, db: Session = Depends(get_db), user: User = viewer) -> CycleOut:
    return _cycle_out(_get_cycle(db, cycle_id))


@router.get("/cycles/{cycle_id}/agents", response_model=list[AgentRunOut], tags=["cycles"])
def cycle_agents(
    cycle_id: str, db: Session = Depends(get_db), user: User = viewer
) -> list[AgentRunOut]:
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
    cycle = _get_cycle(db, cycle_id)
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
    cycle = _get_cycle(db, cycle_id)
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
    project = _get_project(db, project_id)
    digest = learning.history_digest(db, project)
    return {"digest": digest, "prompt_view": learning.digest_to_prompt(digest)}


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
    _get_project(db, project_id)
    return export_graph(db, project_id)


@router.get("/projects/{project_id}/commits", response_model=list[CommitOut], tags=["versions"])
def list_commits(
    project_id: str,
    branch: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> list[CommitOut]:
    stmt = select(ProteinCommit).where(ProteinCommit.project_id == project_id)
    if branch:
        stmt = stmt.where(ProteinCommit.branch == branch)
    rows = db.scalars(stmt.order_by(ProteinCommit.created_at.desc()).limit(limit))
    return [_commit_out(c) for c in rows]


@router.get("/commits/{commit_id}", response_model=CommitOut, tags=["versions"])
def get_commit(commit_id: str, db: Session = Depends(get_db), user: User = viewer) -> CommitOut:
    commit = db.get(ProteinCommit, commit_id)
    if commit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "commit not found")
    return _commit_out(commit)


@router.get("/commits/{commit_id}/lineage", tags=["versions"])
def commit_lineage(
    commit_id: str,
    direction: str = Query(default="ancestors", pattern="^(ancestors|descendants)$"),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
    if db.get(ProteinCommit, commit_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "commit not found")
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
    commit = db.get(ProteinCommit, commit_id)
    if commit is None or not commit.structure_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no structure for this commit")
    artifact = db.scalar(select(Artifact).where(Artifact.key == commit.structure_key))
    backend = artifact.backend if artifact else "s3"
    try:
        data = store.get(commit.structure_key, backend=backend)
    except OSError as exc:
        raise HTTPException(status.HTTP_410_GONE, f"structure unavailable: {exc}") from exc
    return Response(content=data, media_type="chemical/x-pdb")


@router.get("/diff", tags=["versions"])
def diff(
    a: str,
    b: str,
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
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
    _get_project(db, project_id)
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
    _get_project(db, project_id)
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
    cycle = _get_cycle(db, cycle_id)
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
    _get_project(db, project_id)
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
    _get_project(db, project_id)
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
