"""Append-only ledger for the decisions made during a design cycle."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AutonomyDecision, utcnow

STEPS = {
    "plan",
    "fanout",
    "ranking_order",
    "filter_gate",
    "branch_head",
    "tier_choice",
    "shortlist_stop_rule",
    "probe_sampling",
}
LEVELS = ("autonomous", "agent_advised", "fallback", "human_required")


def record(
    db: Session,
    project_id: str,
    step: str,
    decision: str,
    *,
    actor: str,
    autonomy: str,
    basis: dict,
    reversible: bool,
    confidence_basis: str,
    cycle_id: str | None = None,
    commit_id: str | None = None,
) -> AutonomyDecision:
    """Add and flush one decision without committing the surrounding transaction."""
    if step not in STEPS:
        raise ValueError(f"unknown autonomy step: {step}")
    if autonomy not in LEVELS:
        raise ValueError(f"unknown autonomy level: {autonomy}")
    if not isinstance(basis, dict):
        raise ValueError("autonomy decision basis must be an object")
    if not isinstance(decision, str) or not decision or len(decision) > 255:
        raise ValueError("autonomy decision must be a non-empty string of at most 255 characters")
    if not confidence_basis:
        raise ValueError("autonomy decision confidence_basis must be non-empty")
    item = AutonomyDecision(
        project_id=project_id,
        cycle_id=cycle_id,
        commit_id=commit_id,
        step=step,
        decision=decision,
        actor=actor,
        autonomy=autonomy,
        basis=basis,
        reversible=reversible,
        confidence_basis=confidence_basis,
    )
    db.add(item)
    db.flush()
    return item


def ledger(
    db: Session, project_id: str, cycle_id: str | None = None, limit: int = 200
) -> list[AutonomyDecision]:
    """Read the newest ledger entries for a project, optionally narrowed to one cycle."""
    limit = min(max(limit, 1), 200)
    query = select(AutonomyDecision).where(AutonomyDecision.project_id == project_id)
    if cycle_id is not None:
        query = query.where(AutonomyDecision.cycle_id == cycle_id)
    query = query.order_by(AutonomyDecision.created_at.desc()).limit(limit)
    return list(db.scalars(query))


def summary(db: Session, project_id: str) -> dict:
    """Summarize recorded decisions without implying scientific independence."""
    rows = list(
        db.scalars(
            select(AutonomyDecision)
            .where(AutonomyDecision.project_id == project_id)
            .order_by(AutonomyDecision.created_at)
        )
    )
    by_step: dict[str, dict[str, int]] = {
        step: {level: 0 for level in LEVELS} for step in sorted(STEPS)
    }
    for row in rows:
        by_step.setdefault(row.step, {level: 0 for level in LEVELS})
        by_step[row.step][row.autonomy] = by_step[row.step].get(row.autonomy, 0) + 1
    decisions = len(rows)
    override_count = sum(1 for row in rows if row.overridden_by is not None)
    autonomous_count = sum(1 for row in rows if row.autonomy in {"autonomous", "agent_advised"})
    return {
        "decisions": decisions,
        "by_step": by_step,
        "autonomous_fraction": autonomous_count / decisions if decisions else None,
        "fallback_count": sum(1 for row in rows if row.autonomy == "fallback"),
        "human_required_count": sum(1 for row in rows if row.autonomy == "human_required"),
        "override_count": override_count,
        "override_rate": override_count / decisions if decisions else None,
        "meaning": (
            "fraction of recorded decision points, not of scientific judgement; "
            "an unrecorded decision is not counted"
        ),
    }


def override(
    db: Session, decision_id: str, user_id: str, reason: str
) -> AutonomyDecision:
    """Annotate one decision as overridden without changing its original fields."""
    if not reason or not reason.strip():
        raise ValueError("override reason must be non-empty")
    item = db.get(AutonomyDecision, decision_id)
    if item is None:
        raise KeyError("autonomy decision not found")
    if item.overridden_by is not None:
        raise ValueError("autonomy decision already has an override")
    item.overridden_by = user_id
    item.override_reason = reason
    item.overridden_at = utcnow()
    db.flush()
    return item
