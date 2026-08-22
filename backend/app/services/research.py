"""The always-live research daemon: watch the lineage, research what changed, re-estimate drift.

The design cycle answers "what should I build next?" once, on demand. This module answers "what do
we now know about this lineage?" continuously, and it is deliberately a different loop:

* **Durable queue, not a subscription.** Anything that changes a project calls `enqueue` which
  writes a ``queued`` :class:`ResearchEvent`. If no worker is alive the row simply waits, so a
  trigger is never lost to a restart.
* **Debounce by coalescing, never by dropping.** A cycle that commits 13 designs must not start 13
  research sessions, and must not silently discard 12 triggers either. Triggers arriving while a
  queued event is still inside the debounce window are merged into it: the event records every
  trigger in ``triggers`` and how many it absorbed in ``coalesced``.
* **The cache is the point.** Research is keyed by topic (`topic_key`), so a question asked for v2
  is answered from :class:`ResearchNote` for v9. Notes are append-only and their ``reuse_count``
  shows how much of an event was answered from cache rather than re-researched.

What an event does *not* do is invent knowledge: findings carry the provider that produced them
(real Devin literature agent or the labelled local simulation), and drift is only ever computed
from ingested measurements.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.devin import simulation
from app.devin.client import DevinAPIError, DevinClient
from app.devin.runner import PROVIDER_DEVIN, PROVIDER_SIM, ProviderUnavailable, resolve_provider
from app.devin.schemas import schema_for
from app.models import (
    Branch,
    MeasuredResult,
    Observation,
    Project,
    ProteinCommit,
    ResearchDaemonSession,
    ResearchEvent,
    ResearchNote,
    utcnow,
)
from app.services import calibration
from app.toolkit import developability as dev
from app.toolkit import folding
from app.toolkit import structure as structlib

logger = logging.getLogger(__name__)

TRIGGERS = {"commit", "measured_result", "cycle", "branch_head", "upload", "label", "manual"}


# ------------------------------------------------------------------ the queue


def enqueue(
    db: Session,
    project_id: str,
    trigger: str,
    ref: str | None = None,
    detail: str = "",
    settings: Settings | None = None,
) -> ResearchEvent:
    """Record a change for the daemon, coalescing into an event that has not started yet.

    Returns the event the trigger landed on, which is an existing row whenever one is still queued:
    the daemon must react to every change, but reacting once to a burst of thirteen commits is the
    correct reaction, not thirteen sessions.
    """
    settings = settings or get_settings()
    trigger = trigger if trigger in TRIGGERS else "manual"
    entry = {"trigger": trigger, "ref": ref, "detail": detail[:280], "at": utcnow().isoformat()}

    pending = db.scalar(
        select(ResearchEvent)
        .where(ResearchEvent.project_id == project_id, ResearchEvent.status == "queued")
        .order_by(ResearchEvent.created_at.asc())
    )
    if pending is not None:
        pending.triggers = [*(pending.triggers or []), entry]
        pending.coalesced = len(pending.triggers) - 1
        db.flush()
        return pending

    event = ResearchEvent(
        project_id=project_id,
        status="queued",
        trigger=trigger,
        triggers=[entry],
        coalesced=0,
    )
    db.add(event)
    db.flush()
    return event


def _epoch(dt: datetime) -> float:
    """Seconds since the epoch, treating a naive timestamp as UTC (SQLite drops the offset)."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def queued_events(
    db: Session, settings: Settings | None = None, now: float | None = None
) -> list[ResearchEvent]:
    """Queued events whose debounce window has elapsed, oldest first."""
    settings = settings or get_settings()
    cutoff = (now or time.time()) - settings.research_debounce_seconds
    events = list(
        db.scalars(
            select(ResearchEvent)
            .where(ResearchEvent.status == "queued")
            .order_by(ResearchEvent.created_at.asc())
        )
    )
    return [e for e in events if _epoch(e.created_at) <= cutoff]


def daemon_status(db: Session, settings: Settings | None = None) -> dict:
    """Health of the daemon as a whole: what is waiting, what ran last, and on which provider."""
    settings = settings or get_settings()
    events = list(
        db.scalars(select(ResearchEvent).order_by(ResearchEvent.created_at.desc()).limit(200))
    )
    last_done = next((e for e in events if e.status == "done"), None)
    try:
        provider = resolve_provider(settings)
    except ProviderUnavailable as exc:
        provider = None
        logger.debug("research daemon has no provider: %s", exc)
    return {
        "enabled": settings.research_daemon_enabled,
        "provider": provider,
        "debounce_seconds": settings.research_debounce_seconds,
        "tick_seconds": settings.research_tick_seconds,
        "queued": sum(1 for e in events if e.status == "queued"),
        "running": sum(1 for e in events if e.status == "running"),
        "failed": sum(1 for e in events if e.status == "failed"),
        "last_event_at": last_done.finished_at.isoformat() if last_done and last_done.finished_at else None,
        "cached_topics": int(db.scalar(select(func.count(ResearchNote.id))) or 0),
    }


# ----------------------------------------------------------- change detection


@dataclass
class Change:
    """What actually changed since the last completed event, in the project's own terms."""

    head: ProteinCommit | None
    previous_head_id: str | None
    new_commits: list[ProteinCommit] = field(default_factory=list)
    new_measurements: list[MeasuredResult] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "head_commit": self.head.id[:12] if self.head else None,
            "head_label": self.head.label if self.head else None,
            "previous_head_commit": self.previous_head_id[:12] if self.previous_head_id else None,
            "head_moved": bool(self.head and self.head.id != self.previous_head_id),
            "new_commits": [
                {"commit": c.id[:12], "label": c.label, "mutations": [m.get("mutation") for m in c.mutations]}
                for c in self.new_commits[:20]
            ],
            "new_commit_count": len(self.new_commits),
            "new_measurements": [
                {
                    "commit": m.commit_id[:12],
                    "assay": m.assay,
                    "objective": m.objective,
                    "value": m.value,
                    "unit": m.unit,
                    "outcome": m.outcome,
                    "origin": m.origin,
                }
                for m in self.new_measurements[:20]
            ],
            "mutations": self.mutations,
        }


def detect_change(db: Session, project: Project, event: ResearchEvent) -> Change:
    """Diff the project against the state the last completed event saw."""
    last_done = db.scalar(
        select(ResearchEvent)
        .where(
            ResearchEvent.project_id == project.id,
            ResearchEvent.status == "done",
            ResearchEvent.id != event.id,
        )
        .order_by(ResearchEvent.created_at.desc())
    )
    since = last_done.finished_at if last_done else None

    commit_q = select(ProteinCommit).where(ProteinCommit.project_id == project.id)
    if since is not None:
        commit_q = commit_q.where(ProteinCommit.created_at > since)
    new_commits = list(db.scalars(commit_q.order_by(ProteinCommit.created_at.asc())))

    measure_q = select(MeasuredResult).where(MeasuredResult.project_id == project.id)
    if since is not None:
        measure_q = measure_q.where(MeasuredResult.created_at > since)
    new_measurements = list(db.scalars(measure_q.order_by(MeasuredResult.created_at.asc())))

    branch = db.scalar(
        select(Branch).where(Branch.project_id == project.id, Branch.name == "main")
    )
    head = db.get(ProteinCommit, branch.head_commit_id) if branch and branch.head_commit_id else None

    mutations: list[str] = []
    for commit in new_commits:
        for mut in commit.mutations:
            token = mut.get("mutation")
            if token and token not in mutations:
                mutations.append(token)

    return Change(
        head=head,
        previous_head_id=last_done.head_commit_id if last_done else None,
        new_commits=new_commits,
        new_measurements=new_measurements,
        mutations=mutations,
    )


# ------------------------------------------------------------------ the cache


def topics_for(change: Change, drift: dict, limit: int) -> list[dict]:
    """Research questions implied by the change, most specific first.

    A topic key is stable across versions on purpose: `mutation:T25V` asked for v2 is the same
    question in v9, and `drift:ddg_proxy` stays one topic however many measurements arrive.
    """
    topics: list[dict] = []

    def add(key: str, topic: str, question: str) -> None:
        if not any(t["topic_key"] == key for t in topics):
            topics.append({"topic_key": key, "topic": topic, "question": question})

    for objective, info in sorted((drift.get("objectives") or {}).items()):
        tau = info.get("kendall_tau")
        if tau is not None and tau < 0.5:
            add(
                f"drift:{objective}",
                f"{objective} disagrees with measurement",
                f"Measured designs rank differently from the {objective} proxy (Kendall tau "
                f"{tau} over {info['pairs']} designs). What is known to make this proxy "
                "mis-rank designs, and which cheap measurement would separate the cases?",
            )

    for token in change.mutations[:limit]:
        add(
            f"mutation:{token}",
            f"substitution {token}",
            f"What does the literature report about the substitution {token} (or its position) "
            "for stability, expression and aggregation?",
        )

    if change.head is not None:
        motifs = (dev.liabilities(change.head.sequence) or {}).get("motifs") or {}
        for motif, hits in sorted(motifs.items()):
            if hits:
                add(
                    f"liability:{motif}",
                    f"liability motif {motif}",
                    f"The current head carries {len(hits)} {motif} site(s). How much does this "
                    "motif matter in practice for the intended host and assay format?",
                )

    for res in change.new_measurements:
        if res.outcome == "miss" and res.objective:
            add(
                f"miss:{res.objective}",
                f"measured miss on {res.objective}",
                f"A design predicted well by {res.objective} measured as a miss in {res.assay}. "
                "What confounders in this assay format explain that, and what control is missing?",
            )

    return topics[:limit]


def cached_notes(db: Session, project_id: str, topics: list[dict]) -> dict[str, ResearchNote]:
    keys = [t["topic_key"] for t in topics]
    if not keys:
        return {}
    rows = db.scalars(
        select(ResearchNote).where(
            ResearchNote.project_id == project_id, ResearchNote.topic_key.in_(keys)
        )
    )
    return {n.topic_key: n for n in rows}


# --------------------------------------------------------------- the provider


def _daemon_session(
    db: Session, project: Project, client: DevinClient, settings: Settings, prompt: str
) -> ResearchDaemonSession:
    """The project's own research session, created once and messaged thereafter."""
    existing = db.get(ResearchDaemonSession, project.id)
    if existing is not None:
        client.send_message(existing.session_id, prompt)
        existing.messages_sent += 1
        existing.last_used_at = utcnow()
        db.flush()
        return existing
    state = client.create_session(
        prompt,
        title=f"DYB Pro research daemon — {project.name}",
        tags=["dyb-pro", "research-daemon", f"project:{project.id}"],
        max_acu_limit=settings.research_acu_limit,
        structured_output_schema=schema_for("literature"),
    )
    session = ResearchDaemonSession(
        project_id=project.id,
        session_id=state.session_id,
        session_url=state.url,
        status=state.status,
        messages_sent=1,
    )
    db.add(session)
    db.flush()
    return session


def daemon_prompt(project: Project, change: Change, drift: dict, topics: list[dict]) -> str:
    lines = [
        f"You are DYB Pro's research daemon for project '{project.name}'.",
        f"Research goal: {project.goal}",
        "",
        "Something changed in the protein lineage. Research only the open questions below, and "
        "answer each with a claim, a citation to a real paper, and the implication for the next "
        "design round. Say so explicitly when the literature does not settle a question.",
        "",
        f"What changed: {change.as_dict()}",
        f"Proxy-vs-measurement drift so far: {drift}",
        "",
        "Open questions:",
    ]
    lines += [f"  - [{t['topic_key']}] {t['question']}" for t in topics]
    lines += [
        "",
        "Never state an in-silico proxy as a measured property, and never predict an assay result: "
        "nothing in this system is calibrated against experiment.",
    ]
    return "\n".join(lines)


def _research_topics(
    db: Session,
    project: Project,
    event: ResearchEvent,
    change: Change,
    drift: dict,
    topics: list[dict],
    settings: Settings,
) -> tuple[list[dict], int]:
    """Answer uncached topics through the provider and write them to the cache.

    Returns the new findings and how many notes were written. The Devin path talks to the
    project's long-lived daemon session; the simulation path is deterministic and labelled.
    """
    if not topics:
        return [], 0

    provider = event.provider
    findings: list[dict] = []
    session_url: str | None = None

    if provider == PROVIDER_DEVIN:
        prompt = daemon_prompt(project, change, drift, topics)
        client = DevinClient(settings)
        try:
            session = _daemon_session(db, project, client, settings, prompt)
            event.devin_session_id = session.session_id
            event.devin_session_url = session.session_url
            session_url = session.session_url
            state = client.get_session(session.session_id)
            deadline = time.monotonic() + settings.devin_session_timeout_seconds
            while not state.structured_output and not state.is_terminal and time.monotonic() < deadline:
                time.sleep(settings.devin_poll_interval_seconds)
                state = client.get_session(session.session_id)
            event.acus = state.acus_consumed
            session.status = state.status
            findings = list((state.structured_output or {}).get("findings") or [])
        except DevinAPIError as exc:
            event.error = f"devin research failed: {exc}"[:2000]
            logger.warning("research event %s: %s", event.id, event.error)
        finally:
            client.close()
    else:
        evidence = event.metrics.get("toolkit", {}) if event.metrics else {}
        findings = list(
            simulation.simulate_literature_agent(
                change.head.sequence if change.head else "", {"descriptors": evidence.get("descriptors", {})}
            ).get("findings")
            or []
        )

    written = 0
    for index, topic in enumerate(topics):
        # Every topic gets a note even when the provider returned less than one finding each:
        # an unanswered question is itself cacheable state, and re-asking it costs ACUs.
        answer = findings[index] if index < len(findings) else None
        note = ResearchNote(
            project_id=project.id,
            topic_key=topic["topic_key"],
            topic=topic["topic"],
            question=topic["question"],
            findings=[answer] if answer else [],
            citations=[answer.get("citation")] if answer and answer.get("citation") else [],
            provider=provider,
            devin_session_url=session_url,
            first_event_id=event.id,
        )
        db.add(note)
        written += 1
    db.flush()
    return findings, written


# ------------------------------------------------------------- one event


def process_event(db: Session, event_id: str, settings: Settings | None = None) -> ResearchEvent:
    """Run one research event to completion: detect, reuse, research, recompute, record."""
    settings = settings or get_settings()
    event = db.get(ResearchEvent, event_id)
    if event is None:
        raise KeyError(f"unknown research event {event_id}")
    if event.status != "queued":
        return event
    project = db.get(Project, event.project_id)
    if project is None:
        event.status = "failed"
        event.error = "project no longer exists"
        event.finished_at = utcnow()
        db.commit()
        return event

    event.status = "running"
    event.started_at = utcnow()
    try:
        event.provider = resolve_provider(settings)
    except ProviderUnavailable as exc:
        event.provider = PROVIDER_SIM
        logger.warning("research daemon falling back: %s", exc)
    db.commit()

    try:
        change = detect_change(db, project, event)
        drift = calibration.project_calibration(db, project.id)
        event.head_commit_id = change.head.id if change.head else None
        event.changed = change.as_dict()
        event.drift = drift

        # Recompute the physics/chemistry proxies on the current head so the daemon's view of the
        # protein is never staler than the version graph.
        if change.head is not None:
            model = folding.fold_sequence(change.head.sequence, name=change.head.label or "head")
            profile = dev.profile(change.head.sequence, structure=model)
            event.metrics = {
                "toolkit": {
                    "descriptors": profile["descriptors"],
                    "ddg": profile["ddg"],
                    "solubility": profile["solubility"],
                    "aggregation": profile["aggregation"],
                    "liabilities": profile["liabilities"],
                },
                "geometry": {
                    **structlib.summary(model),
                    "is_model": structlib.is_model(model),
                    "geometry_usable": structlib.geometry_usable(model),
                },
                "caveat": (
                    "recomputed from the same uncalibrated toolkit as the design cycle; the "
                    "geometry is a generated CA model, not a measured structure"
                ),
            }
        db.flush()

        topics = topics_for(change, drift, settings.research_max_topics_per_event)
        cached = cached_notes(db, project.id, topics)
        for note in cached.values():
            note.reuse_count += 1
            note.last_used_at = utcnow()
        fresh_topics = [t for t in topics if t["topic_key"] not in cached]
        new_findings, written = _research_topics(
            db, project, event, change, drift, fresh_topics, settings
        )

        event.cache_hits = len(cached)
        event.cache_writes = written
        event.findings = [
            *(
                {
                    "topic_key": key,
                    "topic": note.topic,
                    "from_cache": True,
                    "findings": note.findings,
                    "provider": note.provider,
                }
                for key, note in cached.items()
            ),
            *(
                {
                    "topic_key": topic["topic_key"],
                    "topic": topic["topic"],
                    "from_cache": False,
                    "findings": [new_findings[i]] if i < len(new_findings) else [],
                    "provider": event.provider,
                }
                for i, topic in enumerate(fresh_topics)
            ),
        ]
        event.summary = summarize(change, drift, event)
        event.status = "done"
        event.finished_at = utcnow()
        db.add(
            Observation(
                project_id=project.id,
                role="research-daemon",
                kind="research_event",
                summary=event.summary[:4000],
                payload={
                    "event": event.id,
                    "trigger": event.trigger,
                    "coalesced": event.coalesced,
                    "cache_hits": event.cache_hits,
                    "cache_writes": event.cache_writes,
                    "provider": event.provider,
                },
            )
        )
        db.commit()
        return event
    except Exception as exc:  # a failed event must stay visible, not vanish from the queue
        logger.exception("research event %s failed", event_id)
        event.status = "failed"
        event.error = f"{type(exc).__name__}: {exc}"[:2000]
        event.finished_at = utcnow()
        db.commit()
        return event


def summarize(change: Change, drift: dict, event: ResearchEvent) -> str:
    parts: list[str] = []
    if change.new_commits:
        parts.append(f"{len(change.new_commits)} new commit(s)")
    if change.new_measurements:
        parts.append(f"{len(change.new_measurements)} new measurement(s)")
    if change.head is not None and change.head.id != change.previous_head_id:
        parts.append(f"head now {change.head.label or change.head.id[:12]}")
    if not parts:
        parts.append("no lineage change since the last event")
    tau_notes = [
        f"{obj} tau {info['kendall_tau']}"
        for obj, info in sorted((drift.get("objectives") or {}).items())
        if info.get("kendall_tau") is not None
    ]
    drift_text = "; ".join(tau_notes) if tau_notes else "no objective has enough measurements for drift"
    return (
        f"{', '.join(parts)}. Research: {event.cache_hits} topic(s) from cache, "
        f"{event.cache_writes} researched. Drift: {drift_text}."
    )


def tick(db: Session, settings: Settings | None = None, limit: int = 5) -> list[ResearchEvent]:
    """Process the debounced queue. Safe to call as often as you like; it is the daemon's heartbeat."""
    settings = settings or get_settings()
    if not settings.research_daemon_enabled:
        return []
    processed: list[ResearchEvent] = []
    for event in queued_events(db, settings)[:limit]:
        processed.append(process_event(db, event.id, settings))
    return processed


# -------------------------------------------------------------------- reading


def event_payload(event: ResearchEvent) -> dict:
    return {
        "id": event.id,
        "project_id": event.project_id,
        "status": event.status,
        "trigger": event.trigger,
        "triggers": event.triggers,
        "coalesced": event.coalesced,
        "summary": event.summary,
        "changed": event.changed,
        "findings": event.findings,
        "metrics": event.metrics,
        "drift": event.drift,
        "cache_hits": event.cache_hits,
        "cache_writes": event.cache_writes,
        "provider": event.provider,
        "devin_session_url": event.devin_session_url,
        "acus": event.acus,
        "error": event.error,
        "created_at": event.created_at.isoformat(),
        "finished_at": event.finished_at.isoformat() if event.finished_at else None,
    }


def note_payload(note: ResearchNote) -> dict:
    return {
        "id": note.id,
        "topic_key": note.topic_key,
        "topic": note.topic,
        "question": note.question,
        "findings": note.findings,
        "citations": note.citations,
        "provider": note.provider,
        "devin_session_url": note.devin_session_url,
        "reuse_count": note.reuse_count,
        "created_at": note.created_at.isoformat(),
        "last_used_at": note.last_used_at.isoformat(),
    }


def project_research(db: Session, project_id: str, limit: int = 25) -> dict:
    events = list(
        db.scalars(
            select(ResearchEvent)
            .where(ResearchEvent.project_id == project_id)
            .order_by(ResearchEvent.created_at.desc())
            .limit(limit)
        )
    )
    notes = list(
        db.scalars(
            select(ResearchNote)
            .where(ResearchNote.project_id == project_id)
            .order_by(ResearchNote.last_used_at.desc())
        )
    )
    session = db.get(ResearchDaemonSession, project_id)
    return {
        "daemon": daemon_status(db),
        "session": (
            {
                "session_url": session.session_url,
                "status": session.status,
                "messages_sent": session.messages_sent,
            }
            if session
            else None
        ),
        "events": [event_payload(e) for e in events],
        "notes": [note_payload(n) for n in notes],
        "cache": {
            "topics": len(notes),
            "reuses": sum(n.reuse_count for n in notes),
            "note": "research notes are append-only: nothing in the cache is ever discarded",
        },
    }
