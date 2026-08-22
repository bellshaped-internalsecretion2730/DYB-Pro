"""Continuous learning: turn the version graph + observation log into agent context."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DesignCycle, Observation, Project, ProteinCommit


def history_digest(db: Session, project: Project, max_commits: int = 12) -> dict:
    """Everything a new agent must know before proposing anything.

    Includes: prior cycles and their outcome, best/worst commits by composite score, mutations
    already rejected (with the reason), and open questions carried forward.
    """
    commits = list(
        db.scalars(
            select(ProteinCommit)
            .where(ProteinCommit.project_id == project.id)
            .order_by(ProteinCommit.created_at.desc())
            .limit(200)
        )
    )
    cycles = list(
        db.scalars(
            select(DesignCycle)
            .where(DesignCycle.project_id == project.id)
            .order_by(DesignCycle.created_at.asc())
        )
    )
    observations = list(
        db.scalars(
            select(Observation)
            .where(Observation.project_id == project.id)
            .order_by(Observation.created_at.desc())
            .limit(120)
        )
    )

    def composite(commit: ProteinCommit) -> float:
        return float(commit.scores.get("composite_score", 0.0) or 0.0)

    scored = [c for c in commits if c.scores]
    scored.sort(key=composite, reverse=True)
    best = scored[:5]
    worst = [c for c in scored if c.filters and not c.filters.get("passed")][:5]

    rejected: dict[str, str] = {}
    for commit in commits:
        if commit.filters and not commit.filters.get("passed"):
            key = ",".join(sorted(m.get("mutation") or "" for m in commit.mutations)) or commit.id[:8]
            reasons = ", ".join(commit.filters.get("failed", [])) or "failed developability filters"
            rejected.setdefault(key, reasons)

    tried: dict[str, dict] = {}
    for commit in commits:
        for mut in commit.mutations:
            token = mut.get("mutation")
            if not token:
                continue
            entry = tried.setdefault(token, {"seen": 0, "best_composite": None, "outcomes": []})
            entry["seen"] += 1
            comp = composite(commit)
            if entry["best_composite"] is None or comp > entry["best_composite"]:
                entry["best_composite"] = comp
            entry["outcomes"].append(
                "passed" if (commit.filters or {}).get("passed") else "filtered"
            )

    open_questions = [
        o.summary for o in observations if o.kind == "open_question" and o.summary
    ][:8]

    return {
        "project": {"id": project.id, "name": project.name, "goal": project.goal},
        "cycles_completed": len([c for c in cycles if c.status in {"committed", "partial"}]),
        "cycle_history": [
            {
                "round": c.round,
                "brief": c.brief[:280],
                "status": c.status,
                "summary": c.summary[:400],
                "acus_used": c.acus_used,
            }
            for c in cycles[-6:]
        ],
        "commit_count": len(commits),
        "best_designs": [
            {
                "commit": c.id[:12],
                "label": c.label,
                "mutations": [m.get("mutation") for m in c.mutations],
                "composite": composite(c),
                "scores": c.scores,
                "rationale": c.rationale[:300],
            }
            for c in best
        ],
        "failed_designs": [
            {
                "commit": c.id[:12],
                "label": c.label,
                "mutations": [m.get("mutation") for m in c.mutations],
                "failed_filters": (c.filters or {}).get("failed", []),
                "rationale": c.rationale[:200],
            }
            for c in worst
        ],
        "exclusions": rejected,
        "mutation_ledger": dict(sorted(tried.items(), key=lambda kv: -kv[1]["seen"])[:40]),
        "open_questions": open_questions,
        "recent_observations": [
            {"kind": o.kind, "role": o.role, "summary": o.summary[:220]} for o in observations[:15]
        ],
    }


def digest_to_prompt(digest: dict) -> str:
    """Compact, deterministic rendering of the digest for inclusion in agent prompts."""
    lines: list[str] = []
    lines.append(f"Project: {digest['project']['name']}")
    lines.append(f"Research goal: {digest['project']['goal']}")
    lines.append(
        f"Prior cycles completed: {digest['cycles_completed']}; commits in version graph: "
        f"{digest['commit_count']}"
    )
    if digest["cycle_history"]:
        lines.append("\nPrevious cycles:")
        for c in digest["cycle_history"]:
            lines.append(f"  - round {c['round']} [{c['status']}]: {c['summary'] or c['brief']}")
    if digest["best_designs"]:
        lines.append("\nBest designs so far (highest composite score first):")
        for d in digest["best_designs"]:
            muts = "+".join(m for m in d["mutations"] if m) or "parent"
            lines.append(
                f"  - {d['label']} [{muts}] composite={d['composite']:.3f} "
                f"binding={d['scores'].get('binding_score')} ddG={d['scores'].get('ddg_proxy')} "
                f"why={d['rationale']}"
            )
    if digest["failed_designs"]:
        lines.append("\nDesigns that FAILED developability filters (do not repeat blindly):")
        for d in digest["failed_designs"]:
            muts = "+".join(m for m in d["mutations"] if m) or "parent"
            lines.append(f"  - {d['label']} [{muts}] failed: {', '.join(d['failed_filters'])}")
    if digest["exclusions"]:
        lines.append("\nExclusion list (mutation set -> reason):")
        for key, reason in list(digest["exclusions"].items())[:20]:
            lines.append(f"  - {key or 'parent'}: {reason}")
    if digest["mutation_ledger"]:
        lines.append("\nMutation ledger (already explored):")
        for token, info in list(digest["mutation_ledger"].items())[:20]:
            lines.append(
                f"  - {token}: tried {info['seen']}x, best composite "
                f"{info['best_composite']}, outcomes {set(info['outcomes'])}"
            )
    if digest["open_questions"]:
        lines.append("\nOpen questions carried forward:")
        lines.extend(f"  - {q}" for q in digest["open_questions"])
    return "\n".join(lines)


def exclusions_from_digest(digest: dict) -> dict[str, str]:
    return dict(digest.get("exclusions", {}))


def digest_json(digest: dict, limit: int = 6000) -> str:
    text = json.dumps(digest, indent=2, default=str)
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"
