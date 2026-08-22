"""Residue/region labels as first-class research objects.

Labels are versioned rather than edited: refining one writes a new row pointing at its parent and
marks the old row superseded, so the research ledger can always show what the daemon saw *then*.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProteinCommit, ResidueLabel

KINDS = ("active_site", "liability", "epitope", "mutation_intent", "note")


class LabelError(ValueError):
    pass


def validate(commit: ProteinCommit, kind: str, residues: list[int]) -> list[int]:
    if kind not in KINDS:
        raise LabelError(f"unknown label kind '{kind}' (expected one of {', '.join(KINDS)})")
    length = len(commit.sequence or "")
    cleaned = sorted({int(r) for r in residues})
    out_of_range = [r for r in cleaned if r < 1 or r > length]
    if out_of_range:
        raise LabelError(
            f"residues {out_of_range} are outside the 1..{length} range of {commit.label or commit.id[:8]}"
        )
    return cleaned


def create_label(
    db: Session,
    commit: ProteinCommit,
    *,
    kind: str,
    name: str,
    residues: list[int],
    note: str = "",
    parent_label_id: str | None = None,
    created_by: str | None = None,
) -> ResidueLabel:
    """Create a label, or a refinement of an existing one (which supersedes the parent)."""
    residues = validate(commit, kind, residues)
    version = 1
    parent = db.get(ResidueLabel, parent_label_id) if parent_label_id else None
    if parent is not None:
        if parent.commit_id != commit.id:
            raise LabelError("a refinement must stay on the same version as its parent label")
        version = parent.version + 1
    label = ResidueLabel(
        project_id=commit.project_id,
        commit_id=commit.id,
        kind=kind,
        name=(name or kind)[:128],
        residues=residues,
        note=note[:4000],
        parent_label_id=parent.id if parent else None,
        version=version,
        created_by=created_by,
    )
    db.add(label)
    db.flush()
    if parent is not None:
        parent.superseded_by = label.id
        db.flush()
    return label


def labels_for_commit(db: Session, commit_id: str, include_superseded: bool = False) -> list[ResidueLabel]:
    stmt = select(ResidueLabel).where(ResidueLabel.commit_id == commit_id)
    if not include_superseded:
        stmt = stmt.where(ResidueLabel.superseded_by.is_(None))
    return list(db.scalars(stmt.order_by(ResidueLabel.created_at.asc())))


def label_tree(db: Session, commit_id: str) -> list[dict]:
    """Nested view: each root label with the chain of refinements underneath it."""
    rows = labels_for_commit(db, commit_id, include_superseded=True)
    by_parent: dict[str | None, list[ResidueLabel]] = {}
    for row in rows:
        by_parent.setdefault(row.parent_label_id, []).append(row)

    def node(row: ResidueLabel) -> dict:
        return {
            "id": row.id,
            "kind": row.kind,
            "name": row.name,
            "residues": row.residues,
            "note": row.note,
            "version": row.version,
            "superseded": bool(row.superseded_by),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "refinements": [node(child) for child in by_parent.get(row.id, [])],
        }

    return [node(row) for row in by_parent.get(None, [])]


def label_context(db: Session, commit_id: str) -> str:
    """Prompt-ready rendering of the scientist's annotations for this version."""
    rows = labels_for_commit(db, commit_id)
    if not rows:
        return "no scientist labels on this version"
    lines = []
    for row in rows:
        residues = ", ".join(str(r) for r in row.residues) or "whole chain"
        lines.append(f"- [{row.kind}] {row.name} @ {residues}: {row.note or 'no note'}")
    return "\n".join(lines)
