"""Research Module + wet-lab loop API: the surface the daemon pane and the labelling UX use."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.devin.runner import provider_status
from app.models import (
    MetricDefinition,
    Project,
    ProteinCommit,
    ResearchProject,
    ResidueLabel,
    User,
    WetlabPlan,
)
from app.security import require_role
from app.services import daemon as daemon_svc
from app.services import lab_research as research_svc
from app.services import labels as label_svc
from app.services import proposal as proposal_svc
from app.services import wetlab_loop
from app.skills import wetlab_metrics as wm

logger = logging.getLogger(__name__)
router = APIRouter()

viewer = Depends(require_role("viewer"))
scientist = Depends(require_role("scientist"))


# ------------------------------------------------------------------- payloads


class LabelCreate(BaseModel):
    kind: str = Field(description="active_site|liability|epitope|mutation_intent|note")
    name: str = ""
    residues: list[int] = Field(default_factory=list)
    note: str = ""
    parent_label_id: str | None = None


class HandoffRequest(BaseModel):
    """The interaction state a scientist hands over when they stop driving the workspace."""

    commit_id: str | None = None
    host: str = ""
    notes: str = ""
    label_ids: list[str] = Field(
        default_factory=list, description="labels written during this sitting"
    )


class PlanRequest(BaseModel):
    host: str = "E. coli BL21(DE3)"
    max_assays: int = Field(default=4, ge=1, le=6)


class SimulateRequest(BaseModel):
    seed: int | None = None


class ResultsSubmit(BaseModel):
    """Either structured rows or pasted CSV/JSON text — the lab may hand over either."""

    rows: list[dict] = Field(default_factory=list)
    text: str = ""
    plan_id: str | None = None
    source: str = "scientist"
    construct_label: str = ""
    operator: str = ""
    notes: str = ""


# -------------------------------------------------------------------- helpers


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _campaign(db: Session, project_id: str) -> tuple[Project, ResearchProject]:
    project = _project(db, project_id)
    rp = research_svc.ensure_research_project(db, project)
    db.commit()
    return project, rp


def _commit(db: Session, commit_id: str) -> ProteinCommit:
    commit = db.get(ProteinCommit, commit_id)
    if commit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    return commit


def _commit_campaign(db: Session, commit_id: str) -> tuple[ProteinCommit, Project, ResearchProject]:
    commit = _commit(db, commit_id)
    project, rp = _campaign(db, commit.project_id)
    return commit, project, rp


def _event_out(event) -> dict:
    return {
        "id": event.id,
        "sequence_no": event.sequence_no,
        "kind": event.kind,
        "trigger": event.trigger,
        "role": event.role,
        "summary": event.summary,
        "payload": event.payload,
        "skills_used": event.skills_used,
        "citations": event.citations,
        "provider": event.provider,
        "devin_session_url": event.devin_session_url,
        "parent_event_id": event.parent_event_id,
        "commit_id": event.commit_id,
        "created_at": event.created_at,
    }


def _plan_out(plan: WetlabPlan) -> dict:
    return {
        "id": plan.id,
        "commit_id": plan.commit_id,
        "status": plan.status,
        "risk": plan.risk,
        "predictions": plan.predictions,
        "constructs": plan.constructs,
        "assays": plan.assays,
        "controls": plan.controls,
        "thresholds": plan.thresholds,
        "failure_modes": plan.failure_modes,
        "total_cost_usd": plan.total_cost_usd,
        "information_per_usd": plan.information_per_usd,
        "rationale": plan.rationale,
        "skills_used": plan.skills_used,
        "citations": plan.citations,
        "provider": plan.provider,
        "devin_session_url": plan.devin_session_url,
        "created_at": plan.created_at,
    }


def _result_out(result) -> dict:
    return {
        "id": result.id,
        "commit_id": result.commit_id,
        "plan_id": result.plan_id,
        "source": result.source,
        "construct_label": result.construct_label,
        "measurements": result.measurements,
        "residuals": result.residuals,
        "error_model": result.error_model,
        "notes": result.notes,
        "operator": result.operator,
        "created_at": result.created_at,
    }


# ------------------------------------------------------------------- campaign


@router.get("/projects/{project_id}/research", tags=["research"])
def campaign_overview(
    project_id: str, db: Session = Depends(get_db), user: User = viewer
) -> dict:
    """Everything the research pane needs in one call: digest, daemon, learned, latest proposal."""
    _, rp = _campaign(db, project_id)
    digest = research_svc.research_digest(db, rp)
    events = research_svc.events(db, rp, limit=60)
    proposal_event = next((e for e in events if e.kind == "proposal"), None)
    return {
        "campaign": digest["campaign"],
        "daemon": daemon_svc.daemon_status(db, rp),
        "digest": digest,
        "learned": research_svc.learned_summary(db, rp),
        "proposal": proposal_event.payload if proposal_event else None,
        "events": [_event_out(e) for e in events],
    }


@router.get("/projects/{project_id}/research/daemon", tags=["research"])
def daemon_state(project_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    _, rp = _campaign(db, project_id)
    return daemon_svc.daemon_status(db, rp)


@router.post("/projects/{project_id}/research/refresh", tags=["research"])
def daemon_refresh(
    project_id: str,
    commit_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    """Ask the daemon to look again now (the queue still debounces bursts)."""
    project, rp = _campaign(db, project_id)
    task = daemon_svc.notify(
        db, project, "manual_refresh", commit_id=commit_id, debounce_seconds=0
    )
    db.commit()
    return {"task_id": task.id, "status": task.status, "daemon": daemon_svc.daemon_status(db, rp)}


@router.post("/projects/{project_id}/research/handoff", tags=["research"], status_code=202)
def swarm_handoff(
    project_id: str,
    body: HandoffRequest,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    """Hand the current interaction state to the swarm as one autonomous research/design run.

    This is the explicit end of a sitting: the selected version, the labels written just now, the
    expression host and any free-text notes are bundled into a single daemon task. It reuses the
    daemon queue (so a double-click coalesces instead of launching two swarms) and reports the
    provider honestly — when Devin is unconfigured the run is queued for the deterministic skill
    pass and says so, rather than claiming a Devin session that does not exist.
    """
    project, rp = _campaign(db, project_id)
    commit = _commit(db, body.commit_id) if body.commit_id else None
    if commit is not None and commit.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    if commit is None:
        path = research_svc.version_path(db, rp)
        commit = path[-1] if path else None
    if commit is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "campaign has no protein version yet")

    handed: list[ResidueLabel] = []
    unknown: list[str] = []
    for label_id in body.label_ids:
        label = db.get(ResidueLabel, label_id)
        if label is None or label.commit_id != commit.id:
            unknown.append(label_id)
            continue
        handed.append(label)

    context = {
        "commit_id": commit.id,
        "commit_label": commit.label,
        "host": body.host.strip(),
        "notes": body.notes.strip()[:4000],
        "label_ids": [lb.id for lb in handed],
        "labels": [
            {"id": lb.id, "kind": lb.kind, "name": lb.name, "residues": lb.residues}
            for lb in handed
        ],
        "unknown_label_ids": unknown,
        "requested_by": user.email,
    }
    task = daemon_svc.notify(
        db, project, "handoff_requested", commit_id=commit.id, payload=context,
        debounce_seconds=0,
    )
    event = research_svc.append_event(
        db,
        rp,
        kind="handoff",
        summary=(
            f"scientist handed {commit.label or commit.id[:8]} to the swarm: "
            f"{len(handed)} label(s) this sitting, host {context['host'] or 'planner default'}"
            + (f"; notes: {context['notes'][:200]}" if context["notes"] else "")
        ),
        payload={**context, "daemon_task_id": task.id, "task_status": task.status},
        commit_id=commit.id,
        role="research-daemon",
        provider=rp.daemon_provider or "local-simulation",
        trigger="handoff",
    )
    db.commit()
    return {
        "handoff": context,
        "event_id": event.id,
        "daemon_task": {
            "id": task.id,
            "kind": task.kind,
            "status": task.status,
            "commit_id": task.commit_id,
            "coalesced_into": task.coalesced_into,
            "run_after": task.run_after,
        },
        "provider": provider_status(),
        "devin_session_url": rp.daemon_session_url,
        "daemon": daemon_svc.daemon_status(db, rp),
    }


@router.post("/projects/{project_id}/research/tick", tags=["research"])
def daemon_tick(
    project_id: str,
    limit: int = Query(default=2, ge=1, le=10),
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    """Run due daemon work in-process. The Celery beat does this continuously in deployment."""
    _, rp = _campaign(db, project_id)
    outcomes = daemon_svc.process_pending(db, rp, limit=limit)
    db.commit()
    return {"processed": len(outcomes), "outcomes": outcomes,
            "daemon": daemon_svc.daemon_status(db, rp)}


@router.post("/projects/{project_id}/research/seed-campaign", tags=["research"])
def seed_campaign(
    project_id: str, db: Session = Depends(get_db), user: User = scientist
) -> dict:
    """Build the seeded demo history (3 versions, papers, simulated assays, shrinking drift).

    Idempotent and synchronous: it runs the same research passes the daemon runs on live events, so
    it takes tens of seconds. A campaign that already has wet-lab results is left untouched.
    """
    from app.services.demo_campaign import seed_campaign as build

    project, rp = _campaign(db, project_id)
    summary = build(db, project)
    db.commit()
    return {**summary, "daemon": daemon_svc.daemon_status(db, rp)}


@router.get("/projects/{project_id}/research/events", tags=["research"])
def campaign_events(
    project_id: str,
    limit: int = Query(default=60, ge=1, le=300),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> list[dict]:
    _, rp = _campaign(db, project_id)
    return [_event_out(e) for e in research_svc.events(db, rp, limit=limit)]


@router.get("/projects/{project_id}/research/papers", tags=["research"])
def campaign_papers(
    project_id: str,
    limit: int = Query(default=100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: User = viewer,
) -> list[dict]:
    _, rp = _campaign(db, project_id)
    return [
        {
            "paper_key": p.paper_key,
            "title": p.title,
            "doi": p.doi,
            "year": p.year,
            "venue": p.venue,
            "authors": p.authors,
            "url": p.url,
            "source": p.source,
            "citation": p.citation,
            "citation_count": p.citation_count,
            "relevance": p.relevance,
            "extracted_metrics": p.extracted_metrics,
            "query": p.query,
            "first_seen_at": p.first_seen_at,
        }
        for p in research_svc.papers(db, rp, limit=limit)
    ]


@router.get("/projects/{project_id}/research/drift", tags=["research"])
def campaign_drift(project_id: str, db: Session = Depends(get_db), user: User = viewer) -> list[dict]:
    _, rp = _campaign(db, project_id)
    return [
        {
            "metric": d.metric,
            "n_observations": d.n_observations,
            "bias": d.bias,
            "bias_sd": d.bias_sd,
            "slope": d.slope,
            "intercept": d.intercept,
            "residual_sd": d.residual_sd,
            "rmse": d.rmse,
            "history": d.history,
            "method": d.method,
            "updated_at": d.updated_at,
        }
        for d in research_svc.drift_models(db, rp)
    ]


@router.get("/projects/{project_id}/research/learned", tags=["research"])
def campaign_learned(project_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict:
    _, rp = _campaign(db, project_id)
    return research_svc.learned_summary(db, rp)


@router.get("/projects/{project_id}/research/proposal", tags=["research"])
def campaign_proposal(
    project_id: str,
    commit_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
    """The next version the daemon would build, from labels + drift + history."""
    project, rp = _campaign(db, project_id)
    path = research_svc.version_path(db, rp)
    if not path:
        raise HTTPException(status.HTTP_409_CONFLICT, "campaign has no protein version yet")
    commit = db.get(ProteinCommit, commit_id) if commit_id else path[-1]
    if commit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    return proposal_svc.propose_next(db, rp, project, commit)


@router.get("/research/metrics", tags=["research"])
def metric_vocabulary(db: Session = Depends(get_db), user: User = viewer) -> list[dict]:
    """Canonical metric definitions published from skill.wetlab_metrics."""
    research_svc.upsert_metric_definitions(db)
    db.commit()
    rows = list(db.scalars(select(MetricDefinition).order_by(MetricDefinition.name)))
    return [
        {
            "name": r.name,
            "unit": r.unit,
            "higher_is_better": r.higher_is_better,
            "assay": r.assay,
            "assay_sd": r.assay_sd,
            "sd_kind": r.sd_kind,
            "pass_rule": r.pass_rule,
            "skill": r.skill,
            "citations": r.citations,
        }
        for r in rows
    ]


# --------------------------------------------------------------------- labels


@router.get("/commits/{commit_id}/labels", tags=["labels"])
def list_labels(
    commit_id: str,
    include_superseded: bool = False,
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
    commit = _commit(db, commit_id)
    rows = label_svc.labels_for_commit(db, commit.id, include_superseded=include_superseded)
    return {
        "commit_id": commit.id,
        "sequence_length": len(commit.sequence),
        "labels": [
            {
                "id": lb.id,
                "kind": lb.kind,
                "name": lb.name,
                "residues": lb.residues,
                "note": lb.note,
                "version": lb.version,
                "parent_label_id": lb.parent_label_id,
                "superseded_by": lb.superseded_by,
                "created_at": lb.created_at,
            }
            for lb in rows
        ],
        "tree": label_svc.label_tree(db, commit.id),
    }


@router.post("/commits/{commit_id}/labels", tags=["labels"], status_code=201)
def create_label(
    commit_id: str,
    body: LabelCreate,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    """Labels are research objects: writing one immediately wakes the daemon for the next pass."""
    commit, project, rp = _commit_campaign(db, commit_id)
    try:
        label = label_svc.create_label(
            db,
            commit,
            kind=body.kind,
            name=body.name,
            residues=body.residues,
            note=body.note,
            parent_label_id=body.parent_label_id,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    task = daemon_svc.notify(
        db, project, "label_changed", commit_id=commit.id,
        payload={"label_id": label.id, "kind": label.kind, "name": label.name},
    )
    db.commit()
    return {
        "label": {
            "id": label.id,
            "kind": label.kind,
            "name": label.name,
            "residues": label.residues,
            "note": label.note,
            "version": label.version,
            "parent_label_id": label.parent_label_id,
        },
        "daemon_task": {"id": task.id, "status": task.status},
        "daemon": daemon_svc.daemon_status(db, rp),
    }


# ------------------------------------------------------------------- wet lab


@router.get("/commits/{commit_id}/wetlab/risk", tags=["wetlab"])
def wetlab_risk(
    commit_id: str,
    host: str = wetlab_loop.DEFAULT_HOST,
    db: Session = Depends(get_db),
    user: User = viewer,
) -> dict:
    commit, project, rp = _commit_campaign(db, commit_id)
    prediction = wetlab_loop.predict_metrics(db, rp, commit, host=host)
    return {
        "commit_id": commit.id,
        "predictions": prediction["predictions"],
        "skills_used": prediction["skills_used"],
        "risk": wetlab_loop.risk_report(prediction, commit),
    }


@router.get("/commits/{commit_id}/wetlab/plan", tags=["wetlab"])
def latest_plan(commit_id: str, db: Session = Depends(get_db), user: User = viewer) -> dict | None:
    commit, _, rp = _commit_campaign(db, commit_id)
    plans = research_svc.plans_for(db, rp, commit.id)
    return _plan_out(plans[0]) if plans else None


@router.post("/commits/{commit_id}/wetlab/plan", tags=["wetlab"], status_code=201)
def create_plan(
    commit_id: str,
    body: PlanRequest,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    commit, project, rp = _commit_campaign(db, commit_id)
    plan = wetlab_loop.build_plan(
        db, rp, project, commit, host=body.host, max_assays=body.max_assays
    )
    db.commit()
    return _plan_out(plan)


@router.post("/wetlab/plans/{plan_id}/simulate", tags=["wetlab"], status_code=201)
def simulate_plan(
    plan_id: str,
    body: SimulateRequest,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    """Honest simulator: noisy draws from the predicted distribution, labelled `simulator`."""
    plan = db.get(WetlabPlan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
    rp = db.get(ResearchProject, plan.research_project_id)
    project = db.get(Project, plan.project_id)
    result = wetlab_loop.simulate_results(db, rp, plan, seed=body.seed)
    task = daemon_svc.notify(
        db, project, "results_registered", commit_id=plan.commit_id,
        payload={"result_id": result.id, "source": result.source}, debounce_seconds=0,
    )
    db.commit()
    return {"result": _result_out(result), "daemon_task": {"id": task.id, "status": task.status}}


@router.get("/commits/{commit_id}/wetlab/results", tags=["wetlab"])
def list_results(commit_id: str, db: Session = Depends(get_db), user: User = viewer) -> list[dict]:
    commit, _, rp = _commit_campaign(db, commit_id)
    return [_result_out(r) for r in research_svc.results_for(db, rp, commit.id)]


@router.post("/commits/{commit_id}/wetlab/results", tags=["wetlab"], status_code=201)
def submit_results(
    commit_id: str,
    body: ResultsSubmit,
    db: Session = Depends(get_db),
    user: User = scientist,
) -> dict:
    """Register real (or pasted) results, compute residuals and wake the Learn pass."""
    commit, project, rp = _commit_campaign(db, commit_id)
    rows = list(body.rows)
    if body.text.strip():
        rows.extend(wetlab_loop.parse_result_rows(body.text))
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no measurements supplied")
    plan = db.get(WetlabPlan, body.plan_id) if body.plan_id else None
    if plan is None:
        plans = research_svc.plans_for(db, rp, commit.id)
        plan = plans[0] if plans else None
    result = wetlab_loop.ingest_results(
        db, rp, commit.id, rows,
        source=body.source or "scientist",
        plan=plan,
        notes=body.notes,
        operator=body.operator or user.email,
        construct_label=body.construct_label,
    )
    task = daemon_svc.notify(
        db, project, "results_registered", commit_id=commit.id,
        payload={"result_id": result.id, "source": result.source}, debounce_seconds=0,
    )
    db.commit()
    return {
        "result": _result_out(result),
        "unknown_metrics": (result.raw or {}).get("unknown_metrics", []),
        "daemon_task": {"id": task.id, "status": task.status},
    }


@router.get("/wetlab/assays", tags=["wetlab"])
def assay_catalog(user: User = viewer) -> list[dict]:
    """Planning-only cost/duration catalogue used to rank information per dollar."""
    return [
        {"assay": name, **spec}
        for name, spec in wm.ASSAY_CATALOG.items()
    ]
