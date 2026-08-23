"""The always-on Research Daemon.

Design rules that the tests pin down:

* **never drop an event** — a burst is *coalesced*, not deleted. The duplicate row is kept with
  ``status="coalesced"`` and a pointer to the task that absorbed it, so the ledger still shows that
  the event happened.
* **debounce** — a queued task for the same ``debounce_key`` absorbs newcomers and its ``run_after``
  slides forward by the debounce window, so a scientist dragging a label selection produces one
  research pass rather than thirty.
* **degrade loudly** — when Devin is unreachable the campaign status becomes ``degraded`` and the
  work stays queued. A deterministic skill pass still runs (that is real computation), but it is
  recorded with ``provider="local-simulation"`` and never as Devin research.
"""

from __future__ import annotations

import itertools
import logging
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.devin import research as devin_research
from app.models import DaemonTask, Project, ProteinCommit, ResearchProject, utcnow
from app.services import lab_research as research_svc
from app.services import labels as label_svc
from app.services import proposal as proposal_svc
from app.services import wetlab_loop
from app.versioning import graph as graph_mod

logger = logging.getLogger(__name__)

TASK_KINDS = (
    "version_created",
    "diff_detected",
    "label_changed",
    "results_registered",
    "manual_refresh",
    "handoff_requested",
)

TERMINAL = {"done", "failed", "coalesced"}


# ------------------------------------------------------------------ queueing


def debounce_key_for(kind: str, commit_id: str | None, extra: str = "") -> str:
    return ":".join(part for part in (kind, commit_id or "campaign", extra) if part)


def enqueue(
    db: Session,
    rp: ResearchProject,
    kind: str,
    *,
    commit_id: str | None = None,
    payload: dict | None = None,
    debounce_key: str | None = None,
    debounce_seconds: float | None = None,
) -> DaemonTask:
    """Queue an event. Bursts coalesce onto the pending task; nothing is ever discarded."""
    if kind not in TASK_KINDS:
        raise ValueError(f"unknown daemon task kind: {kind}")
    settings = get_settings()
    window = settings.daemon_debounce_seconds if debounce_seconds is None else debounce_seconds
    key = debounce_key or debounce_key_for(kind, commit_id)
    now = utcnow()
    existing = db.scalar(
        select(DaemonTask)
        .where(
            DaemonTask.research_project_id == rp.id,
            DaemonTask.debounce_key == key,
            DaemonTask.status == "queued",
        )
        .order_by(DaemonTask.created_at.asc())
    )
    task = DaemonTask(
        research_project_id=rp.id,
        project_id=rp.project_id,
        kind=kind,
        debounce_key=key,
        commit_id=commit_id,
        payload=payload or {},
        run_after=now + timedelta(seconds=window),
    )
    if existing is not None:
        task.status = "coalesced"
        task.coalesced_into = existing.id
        existing.coalesced_count = int(existing.coalesced_count or 0) + 1
        existing.run_after = now + timedelta(seconds=window)
        # deep-copied so SQLAlchemy sees a genuinely new value: mutating the loaded list in
        # place would compare equal to itself and the append would silently never be flushed.
        merged = deepcopy(dict(existing.payload or {}))
        merged["coalesced"] = [*merged.get("coalesced", []), payload or {}]
        existing.payload = merged
    db.add(task)
    db.flush()
    return task


def notify(
    db: Session,
    project: Project,
    kind: str,
    *,
    commit_id: str | None = None,
    payload: dict | None = None,
    debounce_seconds: float | None = None,
    dispatch: bool = True,
) -> DaemonTask:
    """Single entry point for the rest of the app: 'something changed, daemon go look'.

    The campaign is created on first use so a scientist never has to arm the daemon by hand.
    """
    rp = research_svc.ensure_research_project(db, project)
    task = enqueue(
        db, rp, kind, commit_id=commit_id, payload=payload, debounce_seconds=debounce_seconds
    )
    settings = get_settings()
    # In eager mode the "worker" is this thread, so dispatching here would run a full research
    # pass inside the request. The row stays queued and the tick (or the manual control) runs it.
    if dispatch and task.status == "queued" and settings.daemon_enabled and (
        not settings.celery_task_always_eager
    ):
        from app.worker import enqueue_daemon_task

        # committed first: the worker may pick the row up in another process immediately.
        db.commit()
        enqueue_daemon_task(task.id)
    return task


def queue_snapshot(db: Session, rp: ResearchProject, limit: int = 40) -> list[DaemonTask]:
    return list(
        db.scalars(
            select(DaemonTask)
            .where(DaemonTask.research_project_id == rp.id)
            .order_by(DaemonTask.created_at.desc())
            .limit(limit)
        )
    )


def claim_next(db: Session, rp: ResearchProject | None = None, now=None) -> DaemonTask | None:
    """Take the oldest due task and mark it processing."""
    now = now or utcnow()
    stmt = select(DaemonTask).where(DaemonTask.status == "queued", DaemonTask.run_after <= now)
    if rp is not None:
        stmt = stmt.where(DaemonTask.research_project_id == rp.id)
    task = db.scalar(stmt.order_by(DaemonTask.created_at.asc()))
    if task is None:
        return None
    task.status = "processing"
    task.attempts = int(task.attempts or 0) + 1
    db.flush()
    return task


def heartbeat(
    db: Session,
    rp: ResearchProject,
    status: str,
    detail: str = "",
    provider: str | None = None,
) -> ResearchProject:
    rp.daemon_status = status
    rp.daemon_detail = detail[:2000]
    if provider:
        rp.daemon_provider = provider
    rp.heartbeat_at = utcnow()
    db.flush()
    return rp


def daemon_status(db: Session, rp: ResearchProject) -> dict:
    tasks = queue_snapshot(db, rp)
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    settings = get_settings()
    return {
        "campaign_id": rp.id,
        "project_id": rp.project_id,
        "name": rp.name,
        "status": rp.daemon_status,
        "detail": rp.daemon_detail,
        "provider": rp.daemon_provider,
        "devin_session_id": rp.daemon_session_id,
        "devin_session_url": rp.daemon_session_url,
        "heartbeat_at": rp.heartbeat_at,
        "knowledge_version": rp.knowledge_version,
        "event_sequence": rp.event_sequence,
        "queue": counts,
        "queue_depth": counts.get("queued", 0),
        "tick_seconds": settings.daemon_tick_seconds,
        "debounce_seconds": settings.daemon_debounce_seconds,
        "tasks": [
            {
                "id": t.id,
                "kind": t.kind,
                "status": t.status,
                "commit_id": t.commit_id,
                "coalesced_count": t.coalesced_count,
                "attempts": t.attempts,
                "error": t.error,
                "created_at": t.created_at,
                "run_after": t.run_after,
                "processed_at": t.processed_at,
                "event_id": t.event_id,
            }
            for t in tasks[:20]
        ],
    }


# ------------------------------------------------------------------ processing


def _agents(db: Session, rp: ResearchProject, digest_text: str) -> devin_research.ResearchAgents:
    agents = devin_research.ResearchAgents(db, rp)
    if agents.devin:
        agents.ensure_supervisor(devin_research.supervisor_brief(rp, digest_text))
    return agents


def _record_child(
    db: Session,
    rp: ResearchProject,
    result: devin_research.ChildResult,
    *,
    kind: str,
    summary: str,
    payload: dict,
    commit_id: str | None,
    parent_event_id: str | None,
    skills_used: list[str],
    citations: list[str],
    provider: str,
) -> object:
    """One research pass -> one immutable event, whether or not the Devin child produced output."""
    enriched = dict(payload)
    enriched["agent"] = result.as_dict()
    if result.usable:
        enriched["agent_output"] = result.output
    elif result.status in {"queued", "unavailable"}:
        enriched["agent_note"] = result.error
    return research_svc.append_event(
        db,
        rp,
        kind=kind,
        summary=summary,
        payload=enriched,
        commit_id=commit_id,
        role=result.role,
        provider="devin" if result.usable else provider,
        skills_used=skills_used,
        citations=citations[:40],
        devin_session_id=result.session_id,
        devin_session_url=result.session_url,
        parent_event_id=parent_event_id,
    )


def research_pass(
    db: Session,
    rp: ResearchProject,
    commit: ProteinCommit,
    *,
    trigger: str,
    root_event_id: str | None = None,
    agents: devin_research.ResearchAgents | None = None,
    host: str | None = None,
) -> dict:
    """The full pass: literature -> metrics -> merged path re-research -> wet-lab plan -> proposal."""
    settings = get_settings()
    project = db.get(Project, rp.project_id)
    digest = research_svc.research_digest(db, rp, commit.id)
    digest_text = research_svc.digest_prompt(digest)
    owns_agents = agents is None
    agents = agents or _agents(db, rp, digest_text)
    provider = agents.provider if agents.provider != "unavailable" else "local-simulation"
    out: dict = {"events": [], "provider": provider, "degraded": bool(agents.degraded_reason)}
    try:
        if agents.devin:
            agents.notify_supervisor(
                devin_research.event_message(trigger, f"pass on {commit.label or commit.id[:8]}",
                                             digest_text)
            )

        # 1. literature on the new version -------------------------------------------------
        labels = research_svc.labels_for(db, commit.id)
        drift = research_svc.drift_models(db, rp)
        queries = research_svc.literature_queries(project, commit, labels, drift)
        lit = research_svc.run_literature_pass(
            db, rp, commit, queries, allow_network=settings.daemon_literature_network
        )
        lit_event = research_svc.append_event(
            db,
            rp,
            kind="literature",
            summary=(
                f"{len(lit['papers'])} paper(s) across {len(lit['queries'])} query(ies)"
                + (f"; degraded: {lit['degraded_reason']}" if lit["degraded"] else "")
            ),
            payload={"queries": lit["queries"], "sources": lit["sources_used"],
                     "degraded": lit["degraded"], "degraded_reason": lit["degraded_reason"]},
            commit_id=commit.id,
            role="research-literature",
            provider=provider,
            trigger=trigger,
            skills_used=["skill.literature"],
            citations=lit["citations"],
            parent_event_id=root_event_id,
        )
        cached = research_svc.cache_papers(
            db, rp, lit, query="; ".join(lit["queries"]), commit_id=commit.id, event_id=lit_event.id
        )
        lit_event.payload = {**lit_event.payload, **cached}
        out["literature"] = {**cached, "degraded": lit["degraded"]}
        out["events"].append(lit_event.id)

        if agents.devin:
            child = agents.child(
                "research-literature",
                devin_research.literature_prompt(digest_text, lit["papers"], lit["queries"]),
            )
            ev = _record_child(
                db, rp, child, kind="literature",
                summary=f"Devin literature agent on {len(lit['papers'])} cached paper(s)",
                payload={"queries": lit["queries"]}, commit_id=commit.id,
                parent_event_id=lit_event.id, skills_used=["skill.literature"],
                citations=lit["citations"], provider=provider,
            )
            out["events"].append(ev.id)

        # 2. metrics ------------------------------------------------------------------------
        metrics = research_svc.run_metrics_pass(db, commit)
        metrics_event = research_svc.append_event(
            db,
            rp,
            kind="metrics",
            summary=f"recomputed {len(metrics['metrics'])} skill-stamped metric(s) for "
                    f"{commit.label or commit.id[:8]}",
            payload={"metrics": metrics["metrics"]},
            commit_id=commit.id,
            role="research-metrics",
            provider=provider,
            trigger=trigger,
            skills_used=metrics["skills_used"],
            citations=metrics["citations"],
            parent_event_id=root_event_id,
        )
        out["events"].append(metrics_event.id)
        out["metrics"] = {"count": len(metrics["metrics"])}
        if agents.devin:
            child = agents.child(
                "research-metrics",
                devin_research.metrics_prompt(
                    digest_text, metrics["metrics"], (digest.get("focus") or {}).get("measured", {})
                ),
            )
            ev = _record_child(
                db, rp, child, kind="metrics", summary="Devin metric interpretation",
                payload={}, commit_id=commit.id, parent_event_id=metrics_event.id,
                skills_used=metrics["skills_used"], citations=metrics["citations"],
                provider=provider,
            )
            out["events"].append(ev.id)

        # 3. merge the whole path and research it again -------------------------------------
        merged = merged_path_pass(db, rp, commit, trigger=trigger, parent_event_id=root_event_id,
                                  settings_network=settings.daemon_literature_network)
        out["events"].append(merged["event_id"])
        out["merged_path"] = merged["summary"]

        # 4. wet-lab plan ------------------------------------------------------------------
        plan_kwargs = {"host": host} if host else {}
        plan = wetlab_loop.build_plan(
            db, rp, project, commit, event_id=root_event_id, provider=provider,
            devin_session_url=rp.daemon_session_url, **plan_kwargs,
        )
        plan_event = research_svc.append_event(
            db,
            rp,
            kind="wetlab_plan",
            summary=(
                f"wet-lab pack: {len(plan.assays)} assay(s), ${plan.total_cost_usd:.0f}, "
                f"{plan.information_per_usd:.4f} information per USD"
            ),
            payload={
                "plan_id": plan.id,
                "assays": plan.assays,
                "thresholds": plan.thresholds,
                "risk": plan.risk,
                "total_cost_usd": plan.total_cost_usd,
                "rationale": plan.rationale,
            },
            commit_id=commit.id,
            role="wetlab-planner",
            provider=provider,
            trigger=trigger,
            skills_used=plan.skills_used,
            citations=plan.citations,
            parent_event_id=root_event_id,
        )
        plan.event_id = plan_event.id
        out["events"].append(plan_event.id)
        out["plan_id"] = plan.id
        for metric, pred in plan.predictions.items():
            if not isinstance(pred.get("value"), int | float):
                continue
            research_svc.record_hypothesis(
                db, rp, commit,
                statement=f"{metric} of {commit.label or commit.id[:8]} measures "
                          f"{pred['value']} +/- {pred['sd']} {pred.get('unit', '')}".strip(),
                metric=metric,
                predicted_value=float(pred["value"]),
                predicted_sd=float(pred.get("sd") or 0.0),
                rationale=pred.get("method", ""),
                citations=plan.citations[:6],
                event_id=plan_event.id,
            )
        if agents.devin:
            child = agents.child(
                "wetlab-planner",
                devin_research.wetlab_prompt(
                    digest_text,
                    {"predictions": plan.predictions, "risk": plan.risk, "assays": plan.assays,
                     "thresholds": plan.thresholds, "total_cost_usd": plan.total_cost_usd},
                ),
            )
            ev = _record_child(
                db, rp, child, kind="wetlab_plan", summary="Devin wet-lab planner review",
                payload={"plan_id": plan.id}, commit_id=commit.id,
                parent_event_id=plan_event.id, skills_used=plan.skills_used,
                citations=plan.citations, provider=provider,
            )
            out["events"].append(ev.id)

        # 5. proposal + insight -------------------------------------------------------------
        insight = write_insight(db, rp, commit, trigger=trigger, agents=agents,
                               parent_event_id=root_event_id, digest_text=digest_text)
        out["events"].extend(insight["events"])
        out["proposal"] = insight["proposal"]

        rp.knowledge_version = int(rp.knowledge_version or 0) + 1
        heartbeat(
            db, rp,
            "degraded" if agents.degraded_reason else "live",
            agents.degraded_reason or f"pass complete for {commit.label or commit.id[:8]}",
            provider=provider,
        )
        db.flush()
        return out
    finally:
        if owns_agents:
            agents.close()


def merged_path_pass(
    db: Session,
    rp: ResearchProject,
    commit: ProteinCommit,
    *,
    trigger: str,
    parent_event_id: str | None,
    settings_network: bool = True,
) -> dict:
    """The second research pass: the whole v1..vN progress path, not just the newest version.

    This is what makes the campaign cumulative — the query set is built from the *trajectory*
    (which metrics moved, which drifted), and its results are merged into the same cache.
    """
    path = research_svc.version_path(db, rp)
    project = db.get(Project, rp.project_id)
    trajectory: list[str] = []
    for a, b in itertools.pairwise(path):
        moved = []
        for metric in ("predicted_tm", "solubility", "aggregation", "composite_score"):
            x, y = (a.scores or {}).get(metric), (b.scores or {}).get(metric)
            if isinstance(x, int | float) and isinstance(y, int | float) and abs(y - x) > 1e-9:
                moved.append(f"{metric} {x:+.3f}->{y:+.3f}")
        step = f"{a.label or a.id[:6]}->{b.label or b.id[:6]}: "
        trajectory.append(step + (", ".join(moved) or "no score change"))
    queries = [
        f"{(project.goal or 'protein engineering')} iterative design campaign measured melting "
        "temperature solubility correlation",
        "in silico prediction versus experimental protein stability measurement discrepancy",
    ]
    lit = research_svc.run_literature_pass(
        db, rp, commit, queries, limit=5, allow_network=settings_network
    )
    event = research_svc.append_event(
        db,
        rp,
        kind="literature",
        summary=(
            f"merged-path re-research over {len(path)} version(s): {len(lit['papers'])} paper(s)"
            + (f"; degraded: {lit['degraded_reason']}" if lit["degraded"] else "")
        ),
        payload={
            "merged_path": True,
            "version_count": len(path),
            "trajectory": trajectory,
            "queries": lit["queries"],
            "degraded": lit["degraded"],
        },
        commit_id=commit.id,
        role="research-literature",
        provider="local-simulation",
        trigger=trigger,
        skills_used=["skill.literature"],
        citations=lit["citations"],
        parent_event_id=parent_event_id,
    )
    cached = research_svc.cache_papers(
        db, rp, lit, query="; ".join(lit["queries"]), commit_id=commit.id, event_id=event.id
    )
    event.payload = {**event.payload, **cached}
    db.flush()
    return {
        "event_id": event.id,
        "summary": {"versions": len(path), "trajectory": trajectory, **cached},
    }


def write_insight(
    db: Session,
    rp: ResearchProject,
    commit: ProteinCommit,
    *,
    trigger: str,
    agents: devin_research.ResearchAgents,
    parent_event_id: str | None,
    digest_text: str,
) -> dict:
    """The 'v1 -> latest' insight plus the concrete next-version proposal."""
    project = db.get(Project, rp.project_id)
    learned = research_svc.learned_summary(db, rp)
    proposal = proposal_svc.propose_next(db, rp, project, commit)
    events: list[str] = []
    insight_event = research_svc.append_event(
        db,
        rp,
        kind="insight",
        summary="; ".join(learned["lessons"][:3]) or "no measurable lesson yet on this campaign",
        payload={"learned": learned},
        commit_id=commit.id,
        role="insight-reporter",
        provider=agents.provider if agents.provider != "unavailable" else "local-simulation",
        trigger=trigger,
        skills_used=["skill.math"],
        parent_event_id=parent_event_id,
    )
    events.append(insight_event.id)
    proposed = proposal.get("proposed")
    proposal_event = research_svc.append_event(
        db,
        rp,
        kind="proposal",
        summary=(
            f"next version {proposed['label']} ({'+'.join(proposed['mutations']) or 'no change'}) "
            f"to move {proposal['target_metric']}"
            if proposed
            else f"no proposal: {proposal.get('reason', 'nothing derivable')}"
        ),
        payload=proposal,
        commit_id=commit.id,
        role="insight-reporter",
        provider=agents.provider if agents.provider != "unavailable" else "local-simulation",
        trigger=trigger,
        skills_used=proposal.get("skills_used", []),
        citations=(proposed or {}).get("citations", []),
        parent_event_id=insight_event.id,
    )
    events.append(proposal_event.id)
    if agents.devin:
        child = agents.child(
            "insight-reporter", devin_research.insight_prompt(digest_text, learned)
        )
        ev = _record_child(
            db, rp, child, kind="insight", summary="Devin insight reporter",
            payload={"proposal": proposal.get("target_metric")}, commit_id=commit.id,
            parent_event_id=insight_event.id, skills_used=["skill.math"], citations=[],
            provider="local-simulation",
        )
        events.append(ev.id)
    return {"events": events, "proposal": proposal, "learned": learned}


def learn_pass(
    db: Session,
    rp: ResearchProject,
    commit: ProteinCommit,
    result_id: str,
    *,
    trigger: str = "results",
) -> dict:
    """Wet-lab results arrived: residuals -> drift update -> hypothesis resolution -> proposal."""
    from app.models import WetlabResult

    result = db.get(WetlabResult, result_id)
    if result is None:
        raise ValueError(f"unknown wet-lab result {result_id}")
    digest_text = research_svc.digest_prompt(research_svc.research_digest(db, rp, commit.id))
    agents = _agents(db, rp, digest_text)
    provider = agents.provider if agents.provider != "unavailable" else "local-simulation"
    try:
        drift = wetlab_loop.update_drift(db, rp, result)
        measured = {m["metric"]: m["value"] for m in result.measurements if m.get("value") is not None}
        resolved = research_svc.resolve_hypotheses(db, rp, commit.id, measured)
        within = [m for m, r in (result.residuals or {}).items()
                  if r.get("z") is not None and abs(float(r["z"])) <= 1.0]
        event = research_svc.append_event(
            db,
            rp,
            kind="learn",
            summary=(
                f"{len(result.measurements)} measurement(s) from {result.source}: "
                f"{len(within)} within one sigma, {len(drift)} calibration update(s), "
                f"{len(resolved)} hypothesis resolution(s)"
            ),
            payload={
                "result_id": result.id,
                "source": result.source,
                "residuals": result.residuals,
                "drift": drift,
                "resolved": resolved,
                "error_model": result.error_model,
            },
            commit_id=commit.id,
            role="learn-from-results",
            provider=provider,
            trigger=trigger,
            skills_used=["skill.wetlab_metrics", "skill.math"],
            parent_event_id=None,
        )
        if agents.devin:
            child = agents.child(
                "learn-from-results",
                devin_research.learn_prompt(digest_text, result.residuals or {}, drift),
            )
            _record_child(
                db, rp, child, kind="learn", summary="Devin learn-from-results agent",
                payload={"result_id": result.id}, commit_id=commit.id,
                parent_event_id=event.id, skills_used=["skill.math"], citations=[],
                provider=provider,
            )
        insight = write_insight(
            db, rp, commit, trigger=trigger, agents=agents, parent_event_id=event.id,
            digest_text=digest_text,
        )
        rp.knowledge_version = int(rp.knowledge_version or 0) + 1
        heartbeat(db, rp, "degraded" if agents.degraded_reason else "live",
                  agents.degraded_reason or f"learned from {result.source} results",
                  provider=provider)
        return {
            "event_id": event.id,
            "drift": drift,
            "resolved": resolved,
            "proposal": insight["proposal"],
            "provider": provider,
        }
    finally:
        agents.close()


def run_task(db: Session, task: DaemonTask) -> dict:
    """Execute one claimed task. Failures are recorded and retried within the attempt budget."""
    settings = get_settings()
    rp = db.get(ResearchProject, task.research_project_id)
    if rp is None:
        task.status = "failed"
        task.error = "campaign missing"
        task.processed_at = utcnow()
        db.flush()
        return {"status": "failed", "error": task.error}
    commit = db.get(ProteinCommit, task.commit_id) if task.commit_id else None
    if commit is None:
        path = research_svc.version_path(db, rp)
        commit = path[-1] if path else None
    if commit is None:
        task.status = "failed"
        task.error = "campaign has no protein version yet"
        task.processed_at = utcnow()
        db.flush()
        return {"status": "failed", "error": task.error}
    heartbeat(db, rp, "working", f"{task.kind} on {commit.label or commit.id[:8]}")
    try:
        if task.kind == "results_registered":
            outcome = learn_pass(db, rp, commit, task.payload.get("result_id", ""))
            event_id = outcome["event_id"]
        else:
            root = _root_event(db, rp, task, commit)
            outcome = research_pass(
                db, rp, commit, trigger=task.kind, root_event_id=root.id,
                host=(task.payload or {}).get("host") or None,
            )
            event_id = root.id
    except Exception as exc:  # a failed pass must not kill the daemon
        logger.exception("daemon task %s failed", task.id)
        task.error = f"{type(exc).__name__}: {exc}"[:2000]
        if int(task.attempts or 0) < settings.daemon_max_attempts:
            task.status = "queued"
            task.run_after = utcnow() + timedelta(seconds=settings.daemon_debounce_seconds)
        else:
            task.status = "failed"
            task.processed_at = utcnow()
        heartbeat(db, rp, "error", task.error)
        db.flush()
        return {"status": task.status, "error": task.error}
    task.status = "done"
    task.event_id = event_id
    task.processed_at = utcnow()
    db.flush()
    return {"status": "done", "event_id": event_id, "task_id": task.id, **outcome}


def _root_event(db: Session, rp: ResearchProject, task: DaemonTask, commit: ProteinCommit):
    """Open the pass with an event describing exactly what the daemon detected."""
    muts = "+".join(m["mutation"] for m in (commit.mutations or []) if m.get("mutation"))
    if task.kind == "version_created":
        kind, summary = "version_created", (
            f"version {commit.label or commit.id[:8]} committed with {muts or 'no mutation'}"
        )
    elif task.kind == "label_changed":
        kind, summary = "label_added", (
            "scientist labels changed on "
            f"{commit.label or commit.id[:8]}:\n{label_svc.label_context(db, commit.id)}"
        )
    elif task.kind == "handoff_requested":
        kind, summary = "handoff", _handoff_summary(db, task, commit)
    elif task.kind == "diff_detected":
        kind, summary = "diff_detected", _diff_summary(db, commit)
    else:
        kind, summary = "diff_detected", f"manual refresh requested for {commit.label or commit.id[:8]}"
    return research_svc.append_event(
        db, rp, kind=kind, summary=summary, commit_id=commit.id,
        payload={"task_id": task.id, "coalesced": int(task.coalesced_count or 0),
                 **(task.payload or {})},
        trigger=task.kind, provider=rp.daemon_provider or "local-simulation",
    )


def _handoff_summary(db: Session, task: DaemonTask, commit: ProteinCommit) -> str:
    """What the scientist handed over: the interaction state that opens this autonomous run."""
    payload = task.payload or {}
    lines = [
        f"scientist handed {commit.label or commit.id[:8]} to the agent swarm",
        f"host: {payload.get('host') or 'planner default'}",
        f"labels this sitting: {len(payload.get('label_ids') or [])}",
        f"notes: {payload.get('notes') or 'none'}",
        f"labels on this version:\n{label_svc.label_context(db, commit.id)}",
    ]
    return "\n".join(lines)


def _diff_summary(db: Session, commit: ProteinCommit) -> str:
    parent_id = (commit.parent_ids or [None])[0] if commit.parent_ids else None
    parent = db.get(ProteinCommit, parent_id) if parent_id else None
    if parent is None:
        return f"{commit.label or commit.id[:8]} is a root version (no parent to diff)"
    diff = graph_mod.diff_commits(db, parent.id, commit.id)
    muts = (
        ", ".join(m.get("mutation") for m in diff.get("mutations", [])[:8] if m.get("mutation"))
        or "no sequence change"
    )
    score_moves = ", ".join(
        f"{k} {v['before']}->{v['after']}"
        for k, v in list((diff.get("score_delta") or {}).items())[:5]
        if v.get("delta")
    )
    return f"diff {parent.label or parent.id[:8]} -> {commit.label or commit.id[:8]}: {muts}" + (
        f"; scores {score_moves}" if score_moves else ""
    )


def process_pending(
    db: Session, rp: ResearchProject | None = None, limit: int = 5, now=None
) -> list[dict]:
    """Drain due tasks. Used by the Celery beat tick and by the API's manual 'tick now' control."""
    results: list[dict] = []
    for _ in range(limit):
        task = claim_next(db, rp, now=now)
        if task is None:
            break
        results.append(run_task(db, task))
    return results
