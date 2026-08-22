"""Celery worker: long-running design cycles run here, never in the request path."""

from __future__ import annotations

import logging

from celery import Celery

from app.config import get_settings
from app.db import session_scope

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "dyb-pro",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=int(settings.devin_session_timeout_seconds) + 900,
    task_soft_time_limit=int(settings.devin_session_timeout_seconds) + 600,
    worker_hijack_root_logger=False,
    # The daemon gets its own queue so a half-hour design cycle can never delay research, and so
    # the two can be scaled (or restarted) independently.
    task_routes={"dyb-pro.research_*": {"queue": "research"}},
)


@celery_app.task(name="dyb-pro.run_cycle", bind=True, max_retries=1)
def run_cycle_task(self, cycle_id: str) -> dict:
    from app.services.cycle import run_cycle

    with session_scope() as db:
        cycle = run_cycle(db, cycle_id)
        return {
            "cycle_id": cycle.id,
            "status": cycle.status,
            "provider": cycle.provider,
            "acus_used": cycle.acus_used,
            "error": cycle.error,
        }


@celery_app.task(name="dyb-pro.cancel_cycle")
def cancel_cycle_task(cycle_id: str, reason: str = "cancelled by scientist") -> dict:
    from app.devin.runner import cancel_cycle
    from app.models import DesignCycle, utcnow

    with session_scope() as db:
        cycle = db.get(DesignCycle, cycle_id)
        if cycle is None:
            return {"cancelled": 0, "error": "unknown cycle"}
        count = cancel_cycle(db, cycle, reason)
        if cycle.status not in {"committed", "partial", "failed"}:
            cycle.status = "cancelled"
            cycle.error = reason
            cycle.finished_at = utcnow()
        return {"cancelled": count, "status": cycle.status}


@celery_app.task(name="dyb-pro.research_tick")
def research_tick_task() -> dict:
    """One heartbeat of the research daemon: drain the debounced queue of research events."""
    from app.services import research

    with session_scope() as db:
        events = research.tick(db)
        return {
            "processed": len(events),
            "events": [{"id": e.id, "status": e.status, "provider": e.provider} for e in events],
        }


@celery_app.task(name="dyb-pro.research_event")
def research_event_task(event_id: str) -> dict:
    """Process one specific research event, used when a scientist triggers research by hand."""
    from app.services import research

    with session_scope() as db:
        event = research.process_event(db, event_id)
        return {"id": event.id, "status": event.status, "error": event.error}


celery_app.conf.beat_schedule = {
    "research-daemon-tick": {
        "task": "dyb-pro.research_tick",
        "schedule": settings.research_tick_seconds,
    }
}


def enqueue_research_event(event_id: str) -> str | None:
    """Queue one research event; the row stays `queued` for the next tick if the broker is down."""
    try:
        return research_event_task.delay(event_id).id
    except Exception as exc:
        logger.warning("celery unavailable (%s); research event %s stays queued", exc, event_id)
        return None


def enqueue_cycle(cycle_id: str) -> str | None:
    """Queue a cycle, falling back to inline execution when no broker is reachable."""
    try:
        result = run_cycle_task.delay(cycle_id)
        return result.id
    except Exception as exc:  # broker down: the demo must still work
        logger.warning("celery unavailable (%s); running cycle inline", exc)
        run_cycle_task.apply(args=[cycle_id])
        return None
