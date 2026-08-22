"""Multi-objective ranking with uncertainty and 'why this, not that' explanations.

Scores are normalized against fixed per-objective windows (see `SCALES`), not against the current
cohort, so a composite score means the same thing in round 1 and round 7 and adding a weak
candidate cannot change the ranking of the others.

The objectives are correlated (hydropathy feeds the solubility index, the aggregation fraction and
the destabilization proxy), so the weights are a stated preference, not independent evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.toolkit import sequence as seqlib

# objective -> (weight, direction) where direction=-1 means lower is better.
DEFAULT_OBJECTIVES: dict[str, tuple[float, int]] = {
    "binding_score": (0.30, -1),
    "ddg_proxy": (0.22, -1),
    "solubility": (0.18, 1),
    "aggregation": (0.14, -1),
    "immunogenicity": (0.10, -1),
    "instability_index": (0.06, -1),
}

FRIENDLY = {
    "binding_score": "interaction score (docking proxy)",
    "ddg_proxy": "destabilization risk (proxy)",
    "solubility": "solubility index (proxy)",
    "aggregation": "aggregation-prone fraction",
    "immunogenicity": "MHC-II motif density",
    "instability_index": "instability index",
}

# Fixed clamp window per objective: (low, high) on the raw score scale. Normalizing to the range
# of the current cohort instead makes every score relative to whoever else happened to be in the
# same batch, so "composite 0.82" means nothing across cycles and adding a bad candidate silently
# improves everyone else (rank reversal). These windows are hand-set from the observable range of
# each heuristic, not fitted, and values outside them are clamped.
SCALES: dict[str, tuple[float, float]] = {
    "binding_score": (-400.0, 0.0),
    "ddg_proxy": (-4.0, 8.0),
    "solubility": (-4.0, 8.0),
    "aggregation": (0.0, 0.5),
    "immunogenicity": (0.0, 15.0),
    "instability_index": (0.0, 80.0),
}


@dataclass
class RankedCandidate:
    label: str
    rank: int
    composite: float
    confidence: float
    normalized: dict[str, float]
    scores: dict[str, float]  # raw proxy values, each in its own arbitrary units
    uncertainty: dict[str, float]
    passed_filters: bool
    failed_filters: list[str]
    pareto: bool
    why: str
    why_not_next: str = ""
    excluded_reason: str | None = None
    payload: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "rank": self.rank,
            "composite_score": self.composite,
            "composite_scale": "weighted mean of fixed-window normalized proxies, 0-1",
            "confidence": self.confidence,
            "confidence_meaning": (
                "1 - mean declared method uncertainty over each objective's fixed window; "
                "uncalibrated, not a probability of wet-lab success"
            ),
            "normalized": self.normalized,
            "missing_objectives": self.payload.get("missing_objectives", []),
            "geometry_usable": self.payload.get("geometry_usable", True),
            "scores": self.scores,
            "uncertainty": self.uncertainty,
            "passed_filters": self.passed_filters,
            "failed_filters": self.failed_filters,
            "pareto_optimal": self.pareto,
            "why": self.why,
            "why_not_next": self.why_not_next,
            "excluded_reason": self.excluded_reason,
        }


@dataclass
class Cluster:
    representative: RankedCandidate
    members: list[RankedCandidate] = field(default_factory=list)

    @property
    def rep(self) -> RankedCandidate:
        return self.representative


def _normalize_fixed(value: float, key: str, direction: int) -> float:
    """Map a raw score onto [0, 1] using this objective's fixed window (1 = better)."""
    lo, hi = SCALES.get(key, (0.0, 1.0))
    if hi - lo < 1e-9:
        return 0.5
    unit = min(1.0, max(0.0, (value - lo) / (hi - lo)))
    return unit if direction > 0 else 1.0 - unit


def scale_span(key: str) -> float:
    lo, hi = SCALES.get(key, (0.0, 1.0))
    return max(1e-9, hi - lo)


def _pareto_front(rows: list[dict], objectives: dict[str, tuple[float, int]]) -> set[str]:
    """Non-dominated set over the objectives both candidates actually scored.

    Comparing on an objective one candidate has no score for would let a missing value decide
    dominance, so those objectives are skipped for that pair.
    """
    front: set[str] = set()
    for a in rows:
        dominated = False
        for b in rows:
            if a is b:
                continue
            keys = [
                k for k in objectives if k in a["normalized"] and k in b["normalized"]
            ]
            if not keys:
                continue
            better_or_equal = all(b["normalized"][k] >= a["normalized"][k] for k in keys)
            strictly_better = any(
                b["normalized"][k] > a["normalized"][k] + 1e-9 for k in keys
            )
            if better_or_equal and strictly_better:
                dominated = True
                break
        if not dominated:
            front.add(a["label"])
    return front


def rank(
    evaluations: list,
    objectives: dict[str, tuple[float, int]] | None = None,
    exclusions: dict[str, str] | None = None,
) -> list[RankedCandidate]:
    """Rank evaluations (objects with .label/.scores/.uncertainty/.filters/.rationale).

    `exclusions` maps a mutation-set signature or label to the reason it was rejected in a prior
    cycle; excluded candidates are ranked last and carry the reason (continuous learning).
    """
    objs = objectives or DEFAULT_OBJECTIVES
    valid = [e for e in evaluations if e.scores]
    if not valid:
        return []

    active = {k: v for k, v in objs.items() if any(k in e.scores for e in valid)}

    rows: list[dict] = []
    for ev in valid:
        # Objectives this candidate has no score for are dropped and its remaining weights are
        # renormalized; substituting 0.0 would score a missing objective as the worst possible.
        present = [k for k in active if k in ev.scores]
        normalized = {
            k: round(_normalize_fixed(float(ev.scores[k]), k, active[k][1]), 4) for k in present
        }
        weight_total = sum(active[k][0] for k in present) or 1.0
        composite = sum(active[k][0] * normalized[k] for k in present) / weight_total
        # Uncertainty relative to the objective's fixed window, so it does not blow up for scores
        # that happen to sit near zero.
        rel_unc = [
            min(1.0, float(ev.uncertainty.get(k, 0.0)) / scale_span(k))
            for k in present
            if k in ev.uncertainty
        ]
        confidence = round(1.0 - (sum(rel_unc) / len(rel_unc)), 3) if rel_unc else 0.5
        rows.append(
            {
                "label": ev.label,
                "ev": ev,
                "normalized": normalized,
                "missing_objectives": sorted(set(active) - set(present)),
                "composite": round(composite, 4),
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )

    front = _pareto_front(rows, active)
    excl = exclusions or {}

    def sort_key(row: dict):
        ev = row["ev"]
        excluded = row["label"] in excl or _signature(ev) in excl
        return (
            0 if (ev.filters.get("passed") and not excluded) else 1,
            -row["composite"],
            -row["confidence"],
        )

    rows.sort(key=sort_key)

    ranked: list[RankedCandidate] = []
    for position, row in enumerate(rows, start=1):
        ev = row["ev"]
        excluded_reason = excl.get(row["label"]) or excl.get(_signature(ev))
        ranked.append(
            RankedCandidate(
                label=row["label"],
                rank=position,
                composite=row["composite"],
                confidence=row["confidence"],
                normalized=row["normalized"],
                scores=ev.scores,
                uncertainty=ev.uncertainty,
                passed_filters=bool(ev.filters.get("passed")),
                failed_filters=list(ev.filters.get("failed", [])),
                pareto=row["label"] in front,
                why=_why(ev, row, active),
                excluded_reason=excluded_reason,
                payload={
                    "missing_objectives": row["missing_objectives"],
                    "geometry_usable": bool(ev.geometry_usable),
                    "sequence": ev.sequence,
                },
            )
        )

    for i, cand in enumerate(ranked[:-1]):
        cand.why_not_next = _why_not(cand, ranked[i + 1], active)
    if ranked:
        ranked[-1].why_not_next = "lowest-ranked candidate in this cycle"
    return ranked


def _signature(ev) -> str:
    muts = ",".join(sorted(m.get("mutation") or "" for m in getattr(ev, "mutations", [])))
    return muts or getattr(ev, "sequence", "")


def _candidate_sort_key(candidate: RankedCandidate):
    return (
        0 if candidate.passed_filters and not candidate.excluded_reason else 1,
        -candidate.composite,
        -candidate.confidence,
        candidate.rank,
    )


def cluster_candidates(
    candidates: list[RankedCandidate], threshold: float = 0.90
) -> list[Cluster]:
    """Greedily cluster ranked candidates by global-alignment identity.

    Identity is matches divided by alignment length, including internal gaps, so near-identical
    variants share a diversity bet while a short exact prefix of a long sequence does not.
    """
    clusters: list[Cluster] = []
    for candidate in sorted(candidates, key=_candidate_sort_key):
        sequence = candidate.payload.get("sequence") or ""
        if not sequence:
            sequence = getattr(candidate, "sequence", "") or ""
        joined = False
        for cluster in clusters:
            representative = cluster.representative
            rep_sequence = representative.payload.get("sequence") or ""
            if not rep_sequence:
                rep_sequence = getattr(representative, "sequence", "") or ""
            if not sequence or not rep_sequence:
                continue
            alignment = seqlib.align(sequence, rep_sequence)
            alignment_length = len(alignment.aligned_a)
            matches = sum(
                a == b for a, b in zip(alignment.aligned_a, alignment.aligned_b, strict=False)
            )
            identity = matches / alignment_length if alignment_length else 0.0
            if identity >= threshold:
                cluster.members.append(candidate)
                joined = True
                break
        if not joined:
            clusters.append(Cluster(candidate, [candidate]))
    return clusters


def round_robin_clusters(clusters: list[Cluster], top_n: int) -> list[tuple[RankedCandidate, int]]:
    """Select one candidate from each cluster per pass through the ranked clusters."""
    selected: list[tuple[RankedCandidate, int]] = []
    for offset in range(max((len(c.members) for c in clusters), default=0)):
        for cluster_id, cluster in enumerate(clusters):
            if offset < len(cluster.members) and len(selected) < top_n:
                selected.append((cluster.members[offset], cluster_id))
    return selected


def _why(ev, row: dict, active: dict) -> str:
    strengths = sorted(row["normalized"].items(), key=lambda kv: -kv[1])[:2]
    weaknesses = sorted(row["normalized"].items(), key=lambda kv: kv[1])[:1]
    parts = [
        f"strong {FRIENDLY.get(k, k)} ({ev.scores.get(k)})" for k, _ in strengths if k in ev.scores
    ]
    weak = [
        f"weak {FRIENDLY.get(k, k)} ({ev.scores.get(k)})" for k, _ in weaknesses if k in ev.scores
    ]
    base = ", ".join(parts) or "balanced profile"
    if weak:
        base += f"; trade-off: {weak[0]}"
    if ev.filters.get("failed"):
        base += f"; fails hard filters: {', '.join(ev.filters['failed'])}"
    if ev.rationale:
        base += f". Agent rationale: {ev.rationale}"
    return base


def why_not(better: RankedCandidate, worse: RankedCandidate, active: dict | None = None) -> str:
    """Explain why `better` outranks `worse`; used when an agent re-orders the shortlist."""
    return _why_not(better, worse, active or DEFAULT_OBJECTIVES)


def _why_not(better: RankedCandidate, worse: RankedCandidate, active: dict) -> str:
    diffs: list[tuple[str, float]] = []
    for key in active:
        a = better.normalized.get(key)
        b = worse.normalized.get(key)
        if a is None or b is None:
            continue
        diffs.append((key, a - b))
    diffs.sort(key=lambda kv: -abs(kv[1]))
    if not diffs:
        return f"ranked above {worse.label} on the aggregate score"
    key, delta = diffs[0]
    direction = "better" if delta > 0 else "worse"
    return (
        f"ranked above {worse.label} mainly on {FRIENDLY.get(key, key)} "
        f"({better.scores.get(key)} vs {worse.scores.get(key)}, {direction} by "
        f"{abs(delta):.2f} normalized units)"
        + (
            f"; {worse.label} also fails {', '.join(worse.failed_filters)}"
            if worse.failed_filters
            else ""
        )
    )
