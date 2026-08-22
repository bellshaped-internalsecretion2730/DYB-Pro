"""Git-like version control for protein designs.

A commit is immutable and content-addressed: its id is the sha256 of a canonical JSON encoding of
its semantic content (sequence, structure digest, parents, scores, agent, prompt, citations,
timestamp). Branch heads move; commits never change.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Branch, ProteinCommit, utcnow
from app.toolkit import sequence as seqlib


class MergeConflict(Exception):
    def __init__(self, conflicts: list[dict]) -> None:
        super().__init__(f"{len(conflicts)} conflicting position(s)")
        self.conflicts = conflicts


def canonical_timestamp(dt: datetime) -> str:
    """UTC, second precision: identical content must hash identically across processes and
    after a database round-trip that may drop timezone/microseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def commit_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def structure_digest(content: str | None, key: str | None) -> str:
    """Digest of the coordinates themselves, falling back to the storage key when absent."""
    if content:
        return "c:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
    return "k:" + hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:16]


def get_branch(db: Session, project_id: str, name: str, create: bool = False) -> Branch | None:
    branch = db.scalar(
        select(Branch).where(Branch.project_id == project_id, Branch.name == name)
    )
    if branch is None and create:
        branch = Branch(project_id=project_id, name=name)
        db.add(branch)
        db.flush()
    return branch


def commit_design(
    db: Session,
    *,
    project_id: str,
    sequence: str,
    message: str,
    label: str = "",
    parent_ids: list[str] | None = None,
    branch: str = "main",
    mutations: list[dict] | None = None,
    scores: dict | None = None,
    uncertainty: dict | None = None,
    filters: dict | None = None,
    rationale: str = "",
    agent_role: str = "human",
    provider: str = "human",
    devin_session_id: str | None = None,
    devin_session_url: str | None = None,
    prompt: str = "",
    citations: list[str] | None = None,
    structure_key: str | None = None,
    structure_source: str = "none",
    structure_content: str | None = None,
    cycle_id: str | None = None,
    cycle_round: int = 0,
    created_at: datetime | None = None,
) -> ProteinCommit:
    """Create an immutable commit and move the branch head. Idempotent by content hash.

    `structure_content` is the coordinate text itself. Hashing only the storage key made the
    commit id depend on a filename rather than on the structure: two different sets of coordinates
    written to the same key hashed identically, and the same coordinates under two keys did not.
    """
    sequence = seqlib.clean_sequence(sequence)
    parents = sorted(parent_ids or [])
    timestamp = created_at or utcnow()
    payload = {
        "project_id": project_id,
        "sequence": sequence,
        "structure": structure_digest(structure_content, structure_key),
        "structure_source": structure_source,
        "parents": parents,
        "scores": scores or {},
        "uncertainty": uncertainty or {},
        "filters": filters or {},
        "agent_role": agent_role,
        "provider": provider,
        "prompt": prompt,
        "citations": sorted(citations or []),
        "mutations": [m.get("mutation") for m in (mutations or [])],
        "branch": branch,
        "timestamp": canonical_timestamp(timestamp),
        "message": message,
    }
    cid = commit_hash(payload)
    existing = db.get(ProteinCommit, cid)
    if existing is not None:
        return existing

    commit = ProteinCommit(
        id=cid,
        project_id=project_id,
        branch=branch,
        label=label or (message[:60] if message else cid[:8]),
        message=message,
        sequence=sequence,
        structure_key=structure_key,
        structure_source=structure_source,
        parent_ids=parents,
        mutations=mutations or [],
        scores=scores or {},
        uncertainty=uncertainty or {},
        filters=filters or {},
        rationale=rationale,
        agent_role=agent_role,
        provider=provider,
        devin_session_id=devin_session_id,
        devin_session_url=devin_session_url,
        prompt=prompt,
        citations=citations or [],
        cycle_id=cycle_id,
        cycle_round=cycle_round,
        created_at=timestamp,
    )
    db.add(commit)
    db.flush()
    br = get_branch(db, project_id, branch, create=True)
    br.head_commit_id = cid
    db.flush()
    return commit


def lineage(db: Session, commit_id: str, direction: str = "ancestors") -> list[ProteinCommit]:
    """Breadth-first walk of the DAG from `commit_id` (excluding the commit itself)."""
    start = db.get(ProteinCommit, commit_id)
    if start is None:
        return []
    seen: set[str] = {commit_id}
    out: list[ProteinCommit] = []
    frontier = [start]
    while frontier:
        node = frontier.pop(0)
        if direction == "ancestors":
            nxt = [db.get(ProteinCommit, p) for p in node.parent_ids]
        else:
            nxt = list(
                db.scalars(
                    select(ProteinCommit).where(ProteinCommit.project_id == node.project_id)
                )
            )
            nxt = [c for c in nxt if node.id in (c.parent_ids or [])]
        for child in nxt:
            if child is None or child.id in seen:
                continue
            seen.add(child.id)
            out.append(child)
            frontier.append(child)
    return out


def common_ancestor(db: Session, a: str, b: str) -> str | None:
    anc_a = {c.id for c in lineage(db, a)} | {a}
    node_b = db.get(ProteinCommit, b)
    frontier = [node_b] if node_b else []
    seen = set()
    while frontier:
        node = frontier.pop(0)
        if node.id in anc_a:
            return node.id
        for pid in node.parent_ids:
            if pid in seen:
                continue
            seen.add(pid)
            parent = db.get(ProteinCommit, pid)
            if parent is not None:
                frontier.append(parent)
    return None


def diff_commits(db: Session, a_id: str, b_id: str) -> dict:
    """What changed between two commits, and why."""
    a = db.get(ProteinCommit, a_id)
    b = db.get(ProteinCommit, b_id)
    if a is None or b is None:
        raise KeyError("unknown commit")
    mutations = seqlib.diff_sequences(a.sequence, b.sequence)
    score_delta = {}
    for key in sorted(set(a.scores) | set(b.scores)):
        before, after = a.scores.get(key), b.scores.get(key)
        if isinstance(before, int | float) and isinstance(after, int | float):
            score_delta[key] = {
                "before": before,
                "after": after,
                "delta": round(after - before, 4),
            }
        else:
            score_delta[key] = {"before": before, "after": after, "delta": None}
    filters_before = set((a.filters or {}).get("failed", []))
    filters_after = set((b.filters or {}).get("failed", []))
    return {
        "from": _commit_stub(a),
        "to": _commit_stub(b),
        "common_ancestor": common_ancestor(db, a_id, b_id),
        "mutations": mutations,
        "length_delta": len(b.sequence) - len(a.sequence),
        "identity": seqlib.align(a.sequence, b.sequence).identity,
        "score_delta": score_delta,
        "filters_fixed": sorted(filters_before - filters_after),
        "filters_introduced": sorted(filters_after - filters_before),
        "why": {
            "from_rationale": a.rationale,
            "to_rationale": b.rationale,
            "to_agent": b.agent_role,
            "to_provider": b.provider,
            "to_session": b.devin_session_url,
            "to_prompt": b.prompt[:2000],
            "citations": b.citations,
        },
    }


def _commit_stub(c: ProteinCommit) -> dict:
    return {
        "id": c.id,
        "short_id": c.id[:12],
        "label": c.label,
        "branch": c.branch,
        "message": c.message,
        "agent_role": c.agent_role,
        "provider": c.provider,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def branch_from(db: Session, project_id: str, name: str, commit_id: str) -> Branch:
    commit = db.get(ProteinCommit, commit_id)
    # A commit id from another project must read as unknown here, not as a usable branch point.
    if commit is None or commit.project_id != project_id:
        raise KeyError(f"unknown commit {commit_id}")
    existing = get_branch(db, project_id, name)
    if existing is not None:
        raise ValueError(f"branch '{name}' already exists")
    branch = Branch(project_id=project_id, name=name, head_commit_id=commit_id)
    db.add(branch)
    db.flush()
    return branch


def merge_branches(
    db: Session,
    project_id: str,
    ours: str,
    theirs: str,
    message: str | None = None,
    into: str | None = None,
) -> ProteinCommit:
    """Three-way merge of two branch heads by recombining mutations from their common ancestor.

    Raises `MergeConflict` when both sides changed the same position differently — conflicts are
    reported for a human to resolve, never silently merged.
    """
    ours_branch = get_branch(db, project_id, ours)
    theirs_branch = get_branch(db, project_id, theirs)
    if ours_branch is None or theirs_branch is None:
        raise KeyError("unknown branch")
    if not (ours_branch.head_commit_id and theirs_branch.head_commit_id):
        raise ValueError("both branches must have commits before merging")

    a = db.get(ProteinCommit, ours_branch.head_commit_id)
    b = db.get(ProteinCommit, theirs_branch.head_commit_id)
    base_id = common_ancestor(db, a.id, b.id)
    base = db.get(ProteinCommit, base_id) if base_id else None
    base_seq = base.sequence if base is not None else a.sequence

    ours_muts = {m["position"]: m for m in seqlib.diff_sequences(base_seq, a.sequence) if m.get("mutation")}
    theirs_muts = {m["position"]: m for m in seqlib.diff_sequences(base_seq, b.sequence) if m.get("mutation")}

    conflicts = [
        {
            "position": pos,
            "ours": ours_muts[pos]["mutation"],
            "theirs": theirs_muts[pos]["mutation"],
        }
        for pos in sorted(set(ours_muts) & set(theirs_muts))
        if ours_muts[pos]["mt"] != theirs_muts[pos]["mt"]
    ]
    if conflicts:
        raise MergeConflict(conflicts)

    combined = {**ours_muts, **theirs_muts}
    tokens = [combined[pos]["mutation"] for pos in sorted(combined)]
    merged_seq, applied = seqlib.apply_mutations(base_seq, tokens)

    # A recombined sequence has no scores. Averaging the parents' would invent values for a
    # sequence neither parent has -- mutations are not additive (epistasis) -- and those averages
    # would then flow into rankings and the history digest as if they had been computed. The
    # parents' scores are kept for reference only, under keys that cannot be mistaken for scores.
    merged_scores: dict = {
        "needs_rescoring": 1.0,
        "parent_scores": {"ours": dict(a.scores or {}), "theirs": dict(b.scores or {})},
    }

    return commit_design(
        db,
        project_id=project_id,
        sequence=merged_seq,
        message=message or f"merge {theirs} into {ours}",
        label=f"merge/{ours}+{theirs}",
        parent_ids=[a.id, b.id],
        branch=into or ours,
        mutations=applied,
        scores=merged_scores,
        filters={"passed": False, "failed": ["requires_rescoring"], "checks": []},
        uncertainty={"needs_rescoring": True},
        rationale=(
            f"Recombination of {len(ours_muts)} mutation(s) from '{ours}' and "
            f"{len(theirs_muts)} from '{theirs}' over common ancestor "
            f"{(base_id or 'root')[:12]}. This sequence has not been scored: run a cycle on this "
            f"branch to evaluate it. Combining mutations is not additive, so the parents' scores "
            f"do not carry over."
        ),
        agent_role="version-control",
        provider="foldsmith",
        citations=["merge of two design branches; scores require re-evaluation"],
    )


def export_graph(db: Session, project_id: str) -> dict:
    commits = list(
        db.scalars(
            select(ProteinCommit)
            .where(ProteinCommit.project_id == project_id)
            .order_by(ProteinCommit.created_at.asc())
        )
    )
    branches = list(db.scalars(select(Branch).where(Branch.project_id == project_id)))
    heads = {b.head_commit_id: b.name for b in branches if b.head_commit_id}
    nodes = [
        {
            "id": c.id,
            "short_id": c.id[:12],
            "label": c.label,
            "branch": c.branch,
            "message": c.message,
            "agent_role": c.agent_role,
            "provider": c.provider,
            "devin_session_url": c.devin_session_url,
            "mutations": [m.get("mutation") for m in c.mutations],
            "scores": c.scores,
            "uncertainty": c.uncertainty,
            "passed_filters": bool((c.filters or {}).get("passed")),
            "failed_filters": (c.filters or {}).get("failed", []),
            "cycle_round": c.cycle_round,
            "is_head": c.id in heads,
            "head_of": heads.get(c.id),
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "rationale": c.rationale,
        }
        for c in commits
    ]
    edges = [
        {"source": parent, "target": c.id}
        for c in commits
        for parent in (c.parent_ids or [])
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "branches": [
            {"name": b.name, "head": b.head_commit_id, "head_short": (b.head_commit_id or "")[:12]}
            for b in branches
        ],
    }
