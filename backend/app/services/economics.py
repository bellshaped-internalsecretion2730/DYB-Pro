"""Planning-model economics for independent wet-lab bets and stop rules."""

from __future__ import annotations

from collections.abc import Iterable
from math import sqrt

from app.services.prices import (
    PRICE_CATALOG,
    TIERS,
    PriceItem,
    item_cost,
    item_cost_interval,
)

LITERATURE_HIT_BAND = (0.10, 0.46)
CROSS_LAB_SHRINKAGE = 0.5
DEFAULT_PRIOR = (0.10 * 0.5, 0.46 * 0.5)
PRIOR_SOURCE = (
    "https://www.nature.com/articles/s41586-025-09429-6 "
    "(BindCraft 10-100%, mean 46.3%); "
    "https://www.adaptyvbio.com/blog/po104/ "
    "(34% first-party vs 16.8% on third-party remeasurement)"
)


def validate_prior(
    prior_low: float | None = None, prior_high: float | None = None
) -> tuple[float, float, str]:
    """Validate a project prior override and return bounds plus its provenance."""
    if prior_low is None and prior_high is None:
        return (*DEFAULT_PRIOR, PRIOR_SOURCE)
    low = DEFAULT_PRIOR[0] if prior_low is None else float(prior_low)
    high = DEFAULT_PRIOR[1] if prior_high is None else float(prior_high)
    if not 0.0 <= low <= 1.0 or not 0.0 <= high <= 1.0 or low > high:
        raise ValueError("prior bounds must be within [0.0, 1.0] and low <= high")
    return low, high, "user-supplied"


def validate_tier_for_readout(tier: str, deciding_readout: str | None) -> None:
    if tier not in TIERS:
        raise ValueError(f"unknown assay tier: {tier}")
    if deciding_readout and deciding_readout not in TIERS[tier]["decides"]:
        raise ValueError(f"tier {tier} does not decide {deciding_readout}")


def p_at_least_one(p: float, n_eff: int) -> float:
    return 1.0 - (1.0 - p) ** n_eff


def expected_hits(p: float, n_eff: int) -> float:
    return p * n_eff


def cost_per_validated_hit(
    batch_cost: float, n_eff: int, prior: tuple[float, float] = DEFAULT_PRIOR
) -> tuple[float, float] | None:
    """Return optimistic then pessimistic cost per hit, or None for no independent bets."""
    low_hits = expected_hits(prior[0], n_eff)
    high_hits = expected_hits(prior[1], n_eff)
    if low_hits <= 0.0 or high_hits <= 0.0:
        return None
    return batch_cost / high_hits, batch_cost / low_hits


def _field(candidate, key: str, default=None):
    return candidate.get(key, default) if isinstance(candidate, dict) else getattr(candidate, key, default)


def _sequence_length(candidate) -> int:
    sequence = _field(candidate, "sequence", "") or ""
    if sequence:
        return len(sequence)
    construct = _field(candidate, "construct", {}) or {}
    return int(construct.get("orf_length_bp", 0) / 3) if construct else 0


def _orf_bp(candidate) -> int:
    construct = _field(candidate, "construct", {}) or {}
    return int(construct.get("orf_length_bp") or (_sequence_length(candidate) * 3))


def _mutation_count(candidate) -> int:
    return len(_field(candidate, "mutations", []) or [])


def line_items(tier: str, candidates: Iterable) -> list[tuple[PriceItem, float]]:
    """Build priced catalogue line items for a tier and a set of candidate records."""
    if tier not in TIERS:
        raise ValueError(f"unknown assay tier: {tier}")
    candidates = list(candidates)
    n = len(candidates)
    if tier == "T0":
        return [
            (PRICE_CATALOG["oligo_pool_library"], 1),
            (PRICE_CATALOG["display_enrichment_marginal"], n),
        ]
    if tier == "T1":
        return [
            (PRICE_CATALOG["gene_synthesis_express"], sum(_orf_bp(c) for c in candidates)),
            (PRICE_CATALOG["outsourced_expression"], n),
            (PRICE_CATALOG["outsourced_binding"], n),
        ]

    synth_bp = sum(_orf_bp(c) for c in candidates if _mutation_count(c) > 3 or not _mutation_count(c))
    mut_count = sum(_mutation_count(c) for c in candidates if 0 < _mutation_count(c) <= 3)
    return [
        (PRICE_CATALOG["gene_synthesis"], synth_bp),
        (PRICE_CATALOG["primer_base"], mut_count * 60),
        (PRICE_CATALOG["mutagenesis_reaction"], mut_count),
        (PRICE_CATALOG["transformation_plasmid_prep"], n),
        (PRICE_CATALOG["sequence_verification"], n),
        (PRICE_CATALOG["expression_purification"], n),
        (PRICE_CATALOG["spr"], n),
        (PRICE_CATALOG["nanodsf"], n),
        (PRICE_CATALOG["sec"], n),
        (PRICE_CATALOG["endotoxin_qc"], n),
    ]


def priced_line_items(tier: str, candidates: Iterable) -> list[dict]:
    return [
        {
            "sku": item.sku,
            "vendor": item.vendor,
            "quantity": quantity,
            "unit": item.unit,
            "cost_usd": round(item_cost(item, quantity), 2),
            "source": item.source,
        }
        for item, quantity in line_items(tier, candidates)
        if quantity > 0
    ]


def _batch_cost(tier: str, candidates: Iterable) -> tuple[float, tuple[float, float]]:
    point = interval_low = interval_high = 0.0
    for item, quantity in line_items(tier, candidates):
        point += item_cost(item, quantity)
        low, high = item_cost_interval(item, quantity)
        interval_low += low
        interval_high += high
    return round(point, 2), (round(interval_low, 2), round(interval_high, 2))


def batch_cost(tier: str, candidates: Iterable) -> tuple[float, tuple[float, float]]:
    """Return point and low/high planning-model cost for a tier and candidates."""
    return _batch_cost(tier, candidates)


def cheapest_tier_for_readout(readout: str, candidates: Iterable) -> str | None:
    """Return the least-cost tier that declares the requested readout."""
    candidates = list(candidates)
    priced: list[tuple[float, str]] = []
    for tier, metadata in TIERS.items():
        if readout not in metadata["decides"]:
            continue
        cost, _ = _batch_cost(tier, candidates)
        priced.append((cost, tier))
    return min(priced)[1] if priced else None


def recommended_n(
    clusters_ranked,
    tier: str,
    budget: float | None,
    target_confidence: float = 0.80,
    prior: tuple[float, float] = DEFAULT_PRIOR,
) -> dict:
    """Smallest independent bets reaching pessimistic target, subject to optional budget."""
    reps = [cluster.representative for cluster in clusters_ranked]
    target_n = None
    for n_eff in range(1, len(reps) + 1):
        if p_at_least_one(prior[0], n_eff) >= target_confidence:
            target_n = n_eff
            break
    desired = target_n or len(reps)
    selected = reps[:desired]
    point, _ = _batch_cost(tier, selected)
    budget_limited = False
    if budget is not None and point > budget:
        affordable = []
        for candidate in reps:
            trial = [*affordable, candidate]
            trial_cost, _ = _batch_cost(tier, trial)
            if trial_cost > budget:
                break
            affordable = trial
        selected = affordable
        desired = len(selected)
        budget_limited = True
    if target_n is not None and not budget_limited:
        stop_reason = "target_confidence_reached"
    elif budget_limited:
        stop_reason = "budget_exhausted"
    else:
        stop_reason = "candidates_exhausted"
    selected_cost, _ = _batch_cost(tier, selected)
    return {
        "n_eff": desired,
        "candidates": [_field(c, "label", "") for c in selected],
        "batch_cost": selected_cost,
        "achieved_confidence": p_at_least_one(prior[0], desired),
        "target_confidence": target_confidence,
        "budget_limited": budget_limited,
        "stop_reason": stop_reason,
    }


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def filter_performance(rows: Iterable[tuple[object, object]], spec=None) -> dict:
    """Build one-observation-per-design confusion counts and Wilson intervals."""
    from app.services.problem import classify

    grouped: dict[str, list[tuple[object, object]]] = {}
    for index, (commit, result) in enumerate(rows):
        commit_id = getattr(commit, "id", None) or f"row-{id(commit) if commit is not None else index}"
        grouped.setdefault(commit_id, []).append((commit, result))
    tp = fp = tn = fn = 0
    conflicting = undecidable = outcome_disagreements = 0
    for pairs in grouped.values():
        commit = pairs[0][0]
        results = [result for _, result in pairs]
        if spec is None:
            labels = {
                result.outcome for result in results if result.outcome in {"hit", "miss"}
            }
            disagreements = set()
        else:
            deciding_name = spec.deciding_objective
            deciding_results = [
                result
                for result in results
                if result.objective == deciding_name
            ]
            classified = [classify(spec, result) for result in deciding_results]
            labels = {item["decision"] for item in classified if item["decision"] in {"hit", "miss"}}
            disagreements = {
                result.outcome
                for result, item in zip(deciding_results, classified, strict=False)
                if result.outcome in {"hit", "miss"}
                and item["decision"] in {"hit", "miss"}
                and result.outcome != item["decision"]
            }
        outcome_disagreements += bool(disagreements)
        if len(labels) > 1:
            conflicting += 1
            continue
        if not labels:
            undecidable += 1
            continue
        actual = next(iter(labels))
        predicted = bool((commit.filters or {}).get("passed"))
        if predicted and actual == "hit":
            tp += 1
        elif predicted:
            fp += 1
        elif actual == "hit":
            fn += 1
        else:
            tn += 1
    n_paired = tp + fp + tn + fn
    n_designs = len(grouped)
    source = "reported_outcome" if spec is None else f"problem_spec_v{spec.version}"
    if n_paired < 10:
        return {
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "ppv": None,
            "npv": None,
            "sensitivity": None,
            "specificity": None,
            "fnr": None,
            "ppv_wilson_95": None,
            "npv_wilson_95": None,
            "sensitivity_wilson_95": None,
            "specificity_wilson_95": None,
            "fnr_wilson_95": None,
            "n_paired": n_paired,
            "classification_source": source,
            "n_designs": n_designs,
            "conflicting": conflicting,
            "undecidable": undecidable,
            "outcome_disagreements": outcome_disagreements,
            "status": "insufficient_data",
            "n_required": 10,
        }
    rates = {
        "ppv": (tp, tp + fp),
        "npv": (tn, tn + fn),
        "sensitivity": (tp, tp + fn),
        "specificity": (tn, tn + fp),
        "fnr": (fn, fn + tp),
    }
    out = {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "n_paired": n_paired,
        "classification_source": source,
        "n_designs": n_designs,
        "conflicting": conflicting,
        "undecidable": undecidable,
        "outcome_disagreements": outcome_disagreements,
        "status": "ok",
    }
    for name, (k, n) in rates.items():
        out[name] = k / n if n else None
        out[f"{name}_wilson_95"] = wilson(k, n)
    return out
