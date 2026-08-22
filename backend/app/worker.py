"""Celery worker: long-running design cycles and program rounds run here, never in the request path."""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

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
    beat_schedule={
        "pharmakon-research-daemon": {
            "task": "pharmakon.daemon_tick",
            "schedule": crontab(hour="3", minute="0"),
        }
    },
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


@celery_app.task(name="pharmakon.run_round", bind=True, max_retries=1)
def run_round_task(self, round_id: str) -> dict:
    from app.services.program import run_round

    with session_scope() as db:
        rnd = run_round(db, round_id)
        return {
            "round_id": rnd.id,
            "status": rnd.status,
            "provider": rnd.provider,
            "gate": (rnd.gate or {}).get("decision"),
            "acus_used": rnd.acus_used,
            "error": rnd.error,
        }


@celery_app.task(name="pharmakon.daemon_tick")
def daemon_tick_task() -> dict:
    """Local research-daemon tick: advance every autonomous program that may move itself.

    This is the fallback for organisations without Devin scheduled sessions. It runs rounds the
    autonomy level permits, but unlike a real Devin schedule it cannot search literature, so it
    never produces new external evidence.
    """
    from app.models import DrugProgram
    from app.services.program import next_action, start_round

    acted: list[dict] = []
    with session_scope() as db:
        programs = list(
            db.scalars(select(DrugProgram).where(DrugProgram.status.in_(["active", "running"])))
        )
        for program in programs:
            if not program.daemon_enabled:
                continue
            plan = next_action(db, program)
            if plan["action"] not in {"run_round", "advance_stage"}:
                acted.append({"program_id": program.id, "skipped": plan["reason"]})
                continue
            rnd = start_round(db, program)
            db.commit()
            enqueue_round(rnd.id)
            acted.append({"program_id": program.id, "round_id": rnd.id, "action": plan["action"]})
    return {"programs": len(acted), "actions": acted}


def enqueue_round(round_id: str) -> str | None:
    """Queue a program round, falling back to inline execution when no broker is reachable."""
    try:
        return run_round_task.delay(round_id).id
    except Exception as exc:  # broker down: the demo must still work
        logger.warning("celery unavailable (%s); running round inline", exc)
        run_round_task.apply(args=[round_id])
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
