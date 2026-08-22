"""Drift between in-silico proxies and measured wet-lab results.

This is the only honest source of a hit rate in DYB Pro: it is computed from `MeasuredResult`
rows a user ingested, never from the heuristics' own confidence. With fewer than
`MIN_PAIRS_FOR_AGREEMENT` measurements for an objective the agreement is reported as `None` rather
than as a small-sample number that would read as calibration.

Agreement is measured as Kendall's tau-b between the proxy ordering and the measured ordering of
the same commits, because the proxies are only ever claimed to rank designs: a proxy in arbitrary
units cannot be checked against an absolute measurement, only against the order it implies.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MeasuredResult, ProteinCommit

MIN_PAIRS_FOR_AGREEMENT = 5

# Objectives where a lower proxy value is the better design, so a positive tau means the proxy
# ordering agrees with the measured ordering once the sign is applied.
LOWER_IS_BETTER = {"ddg_proxy", "aggregation", "immunogenicity", "instability_index", "binding_score"}


def kendall_tau(pairs: list[tuple[float, float]]) -> float | None:
    """Kendall's tau-b for the paired values, or None when it is undefined."""
    n = len(pairs)
    if n < 2:
        return None
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = pairs[i][0] - pairs[j][0]
            dy = pairs[i][1] - pairs[j][1]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif dx * dy > 0:
                concordant += 1
            else:
                discordant += 1
    denom = ((concordant + discordant + ties_x) * (concordant + discordant + ties_y)) ** 0.5
    if denom == 0:
        return None
    return round((concordant - discordant) / denom, 3)


def project_calibration(db: Session, project_id: str) -> dict:
    """Per-objective proxy/measurement agreement and the measured hit rate for one project."""
    results = list(
        db.scalars(
            select(MeasuredResult).where(MeasuredResult.project_id == project_id)
        )
    )
    if not results:
        return {"measurements": 0, "hit_rate": None, "objectives": {}, "commits_measured": 0}

    commit_ids = {r.commit_id for r in results}
    commits = {
        c.id: c
        for c in db.scalars(select(ProteinCommit).where(ProteinCommit.id.in_(commit_ids)))
    }

    by_objective: dict[str, list[tuple[float, float]]] = {}
    for res in results:
        commit = commits.get(res.commit_id)
        if commit is None or not res.objective:
            continue
        proxy = commit.scores.get(res.objective)
        if proxy is None:
            continue
        sign = -1.0 if res.objective in LOWER_IS_BETTER else 1.0
        measured = res.value if res.higher_is_better else -res.value
        by_objective.setdefault(res.objective, []).append((sign * float(proxy), measured))

    objectives: dict[str, dict] = {}
    for objective, pairs in by_objective.items():
        enough = len(pairs) >= MIN_PAIRS_FOR_AGREEMENT
        objectives[objective] = {
            "pairs": len(pairs),
            "kendall_tau": kendall_tau(pairs) if enough else None,
            "note": (
                "rank agreement between proxy and measurement, this project only"
                if enough
                else f"needs at least {MIN_PAIRS_FOR_AGREEMENT} measured designs to report"
            ),
        }

    decided = [r for r in results if r.outcome in {"hit", "miss"}]
    hit_rate = (
        round(sum(1 for r in decided if r.outcome == "hit") / len(decided), 3) if decided else None
    )
    return {
        "measurements": len(results),
        "commits_measured": len(commit_ids),
        "hit_rate": hit_rate,
        "decided_measurements": len(decided),
        "objectives": objectives,
        "simulated_measurements": sum(1 for r in results if r.origin == "simulated"),
    }
