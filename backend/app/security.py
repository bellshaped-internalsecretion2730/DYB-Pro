"""API-key auth, RBAC and usage quotas."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DesignCycle, Project, UsageRecord, User, utcnow

ROLE_RANK = {"viewer": 0, "scientist": 1, "admin": 2}


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def current_user(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> User:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key header")
    user = db.scalar(select(User).where(User.api_key_hash == hash_api_key(x_api_key)))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return user


def require_role(minimum: str):
    """Route dependency enforcing a minimum role."""

    def _dep(user: User = Depends(current_user)) -> User:
        if ROLE_RANK[user.role] < ROLE_RANK[minimum]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"role '{user.role}' cannot perform this action (requires '{minimum}')",
            )
        return user

    return _dep


def can_access_project(user: User, project: Project) -> bool:
    """Read access. Every member of the workspace may read, which is what the viewer role is for."""
    del project
    return ROLE_RANK[user.role] >= ROLE_RANK["viewer"]


def can_write_project(user: User, project: Project) -> bool:
    """Write access: the project's own scientist, or an admin.

    Read is workspace-wide but writes are not: uploading into, running cycles on, branching or
    merging someone else's lineage would corrupt their provenance, so only the owner (or an admin)
    may mutate a project. Projects created before ownership was recorded have no owner and stay
    writable rather than becoming read-only orphans.
    """
    return bool(
        user.role == "admin" or project.owner_id is None or project.owner_id == user.id
    )


def require_project(db: Session, user: User, project_id: str, write: bool = False) -> Project:
    """Load a project the caller is allowed to use, 404 when it does not exist."""
    project = db.get(Project, project_id)
    if project is None or not can_access_project(user, project):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if write and not can_write_project(user, project):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "project belongs to another scientist; ask its owner or an admin",
        )
    return project


def usage_snapshot(db: Session, user: User) -> dict:
    since = utcnow() - timedelta(days=1)
    acus = (
        db.scalar(
            select(func.coalesce(func.sum(UsageRecord.amount), 0.0)).where(
                UsageRecord.user_id == user.id, UsageRecord.kind == "acu"
            )
        )
        or 0.0
    )
    cycles_today = (
        db.scalar(
            select(func.count(DesignCycle.id)).where(
                DesignCycle.user_id == user.id, DesignCycle.created_at >= since
            )
        )
        or 0
    )
    return {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "acus_used": round(float(acus), 3),
        "acu_quota": user.acu_quota,
        "acus_remaining": round(max(0.0, user.acu_quota - float(acus)), 3),
        "cycles_last_24h": int(cycles_today),
        "cycle_quota": user.cycle_quota,
    }


def enforce_quota(db: Session, user: User, requested_acus: int) -> None:
    snap = usage_snapshot(db, user)
    if snap["cycles_last_24h"] >= user.cycle_quota:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"cycle quota exhausted ({user.cycle_quota}/24h)",
        )
    if snap["acus_remaining"] < requested_acus:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"ACU quota exhausted: {snap['acus_remaining']} left, cycle needs {requested_acus}",
        )


def record_usage(db: Session, user_id: str | None, kind: str, amount: float, ref: str | None = None) -> None:
    if not user_id or amount <= 0:
        return
    db.add(UsageRecord(user_id=user_id, kind=kind, amount=float(amount), ref=ref))
