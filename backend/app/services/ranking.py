"""Multi-objective ranking with uncertainty and 'why this, not that' explanations."""

from __future__ import annotations

from dataclasses import dataclass, field

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
    "binding_score": "predicted binding",
    "ddg_proxy": "fold stability",
    "solubility": "solubility",
    "aggregation": "aggregation risk",
    "immunogenicity": "immunogenicity risk",
    "instability_index": "in-vivo stability",
}


@dataclass
class RankedCandidate:
    label: str
    rank: int
    composite: float
    confidence: float
    normalized: dict[str, float]
    scores: dict[str, float]
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
            "confidence": self.confidence,
            "normalized": self.normalized,
            "scores": self.scores,
            "uncertainty": self.uncertainty,
            "passed_filters": self.passed_filters,
            "failed_filters": self.failed_filters,
            "pareto_optimal": self.pareto,
            "why": self.why,
            "why_not_next": self.why_not_next,
            "excluded_reason": self.excluded_reason,
        }


def _normalize(values: list[float], direction: int) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5] * len(values)
    if direction > 0:
        return [(v - lo) / (hi - lo) for v in values]
    return [(hi - v) / (hi - lo) for v in values]


def _pareto_front(rows: list[dict], objectives: dict[str, tuple[float, int]]) -> set[str]:
    keys = list(objectives)
    front: set[str] = set()
    for a in rows:
        dominated = False
        for b in rows:
            if a is b:
                continue
            better_or_equal = all(
                b["normalized"].get(k, 0.0) >= a["normalized"].get(k, 0.0) for k in keys
            )
            strictly_better = any(
                b["normalized"].get(k, 0.0) > a["normalized"].get(k, 0.0) + 1e-9 for k in keys
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
    columns: dict[str, list[float]] = {}
    for key, (_, direction) in active.items():
        raw = [float(e.scores.get(key, 0.0)) for e in valid]
        columns[key] = _normalize(raw, direction)

    rows: list[dict] = []
    for idx, ev in enumerate(valid):
        normalized = {k: round(columns[k][idx], 4) for k in active}
        weight_total = sum(w for w, _ in active.values()) or 1.0
        composite = sum(active[k][0] * normalized[k] for k in active) / weight_total
        rel_unc = []
        for k in active:
            unc = float(ev.uncertainty.get(k, 0.0))
            scale = max(1e-6, abs(float(ev.scores.get(k, 0.0))))
            rel_unc.append(min(1.0, unc / scale))
        confidence = round(1.0 - (sum(rel_unc) / max(1, len(rel_unc))), 3)
        rows.append(
            {
                "label": ev.label,
                "ev": ev,
                "normalized": normalized,
                "composite": round(composite, 4),
                "confidence": max(0.0, confidence),
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
            )
        )

    for i, cand in enumerate(ranked[:-1]):
        cand.why_not_next = _why_not(cand, ranked[i + 1], active)
    if ranked:
        ranked[-1].why_not_next = "lowest-ranked candidate in this cycle"
    return ranked


def _signature(ev) -> str:
    muts = ",".join(sorted(m.get("mutation") or "" for m in getattr(ev, "mutations", [])))
    return muts or getattr(ev, "sequence", "")[:32]


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
