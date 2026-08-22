"""Celery worker: long-running design cycles run here, never in the request path."""

from __future__ import annotations

import logging

from celery import Celery

from app.config import get_settings
from app.db import session_scope

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "foldsmith",
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
)


@celery_app.task(name="foldsmith.run_cycle", bind=True, max_retries=1)
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


@celery_app.task(name="foldsmith.cancel_cycle")
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


def enqueue_cycle(cycle_id: str) -> str | None:
    """Queue a cycle, falling back to inline execution when no broker is reachable."""
    try:
        result = run_cycle_task.delay(cycle_id)
        return result.id
    except Exception as exc:  # broker down: the demo must still work
        logger.warning("celery unavailable (%s); running cycle inline", exc)
        run_cycle_task.apply(args=[cycle_id])
        return None
