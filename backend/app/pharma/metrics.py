"""Program metrics: the evidence dictionary every gate criterion is evaluated against.

Metrics come from three places and nowhere else:
  * deterministic chemistry — :func:`app.chem.evaluate.evaluate_molecule` output per molecule,
  * ingested experimental results — assay rows recorded against a molecule hash,
  * agent findings that a human would call judgement (target evidence, freedom to operate),
    which are stored as numbers with their provenance attached.

Keeping this in one place means a gate decision can always be replayed from stored data.
"""

from __future__ import annotations

import math

# Findings supplied by agents rather than computed from chemistry. Defaults are deliberately
# pessimistic: an unanswered question never passes a gate.
FINDING_KEYS = (
    "target_evidence_score",
    "druggability_score",
    "freedom_to_operate",
    "citation_count",
    "scale_up_feasibility",
    "dossier_completeness",
    "starting_dose_defined",
)

POTENCY_HIT_PKD = 6.0

# Assay metrics that replace the corresponding predicted gate metric outright. Only endpoints
# that measure the same quantity in the same direction are listed: a measured number is the
# ground truth the proxy was trying to approximate.
MEASURED_OVERRIDES: dict[str, tuple[str, str]] = {
    "pkd": ("best_pkd", "max"),
    "half_life_h": ("best_half_life_h", "max"),
    "selectivity_fold": ("best_selectivity_fold", "max"),
    "noael_mg_kg": ("measured_noael_mg_kg", "max"),
}


def _get(evaluation: dict, *path: str) -> float | None:
    node: object = evaluation
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, bool | int | float):
        return float(node)
    return None


def _best(values: list[float | None], *, mode: str = "max") -> float | None:
    real = [v for v in values if v is not None]
    if not real:
        return None
    return max(real) if mode == "max" else min(real)


def molecule_metrics(evaluation: dict) -> dict[str, float | None]:
    """Flatten one molecule evaluation into the scalar metrics gates care about."""
    return {
        "pkd": _get(evaluation, "binding", "pkd"),
        "kd_nm": _get(evaluation, "binding", "kd_nm"),
        "selectivity_fold": _get(evaluation, "selectivity", "min_fold_selectivity"),
        "ligand_efficiency": _get(evaluation, "efficiency", "ligand_efficiency"),
        "composite_score": _get(evaluation, "composite_score"),
        "qed_like": _get(evaluation, "descriptors", "qed_like"),
        "clogp": _get(evaluation, "descriptors", "clogp"),
        "molecular_weight": _get(evaluation, "descriptors", "molecular_weight"),
        "admet_score": _get(evaluation, "admet", "admet_score"),
        "hia": _get(evaluation, "admet", "absorption", "human_intestinal_absorption"),
        "half_life_h": _get(evaluation, "admet", "excretion", "half_life_h"),
        "herg_risk": _get(evaluation, "admet", "toxicity", "herg", "risk"),
        "ames_risk": _get(evaluation, "admet", "toxicity", "genotoxicity", "ames_risk"),
        "hepatotoxicity_risk": _get(evaluation, "admet", "toxicity", "hepatotoxicity_risk"),
        "cyp_risk": _worst_cyp(evaluation),
        "sa_score": _get(evaluation, "synthesis", "sa_score"),
        "cost_per_gram_usd": _get(evaluation, "synthesis", "cost_per_gram_usd"),
        "oral_dose_mg": _get(evaluation, "dose_projection", "projected_dose_mg"),
        "tox_margin": _get(evaluation, "dose_projection", "predicted_safety_margin"),
        "blocking_alerts": float(len((evaluation.get("liabilities") or {}).get("blocking") or [])),
    }


def _worst_cyp(evaluation: dict) -> float | None:
    cyp = ((evaluation.get("admet") or {}).get("metabolism") or {}).get("cyp") or {}
    values = [
        v for k, v in cyp.items() if k.endswith("_inhibition") and isinstance(v, int | float)
    ]
    return max(values) if values else None


def prediction_drift(evaluations: list[dict], assays: list[dict]) -> dict:
    """RMSE between the predicted binding proxy and ingested measured potency.

    Assay rows are ``{"molecule_hash", "metric", "value", "unit"}``. Only ``pkd`` and
    ``ic50_nm``/``kd_nm`` rows are comparable to the binding proxy; anything else is ignored
    here and surfaced separately, because silently averaging incomparable endpoints is how
    self-deceiving models get built.
    """
    predicted = {
        e.get("molecule_hash"): _get(e, "binding", "pkd")
        for e in evaluations
        if _get(e, "binding", "pkd") is not None
    }
    pairs: list[tuple[float, float]] = []
    for row in assays:
        observed = _observed_pkd(row)
        pred = predicted.get(row.get("molecule_hash"))
        if observed is None or pred is None:
            continue
        pairs.append((pred, observed))
    if not pairs:
        return {"n": 0, "rmse": None, "bias": None, "pairs": []}
    errors = [p - o for p, o in pairs]
    rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
    return {
        "n": len(pairs),
        "rmse": round(rmse, 3),
        "bias": round(sum(errors) / len(errors), 3),
        "pairs": [{"predicted_pkd": round(p, 2), "observed_pkd": round(o, 2)} for p, o in pairs],
        "method": "predicted complementarity-proxy pKd vs ingested measured pKd",
    }


def _observed_pkd(row: dict) -> float | None:
    metric = str(row.get("metric") or "").lower()
    value = row.get("value")
    if not isinstance(value, int | float):
        return None
    if metric in ("pkd", "pki", "pic50"):
        return float(value)
    if metric in ("kd_nm", "ki_nm", "ic50_nm", "ec50_nm") and value > 0:
        return -math.log10(float(value) * 1e-9)
    return None


def program_metrics(
    *,
    evaluations: list[dict],
    assays: list[dict] | None = None,
    findings: dict | None = None,
    stage_cycles: int = 0,
) -> dict[str, float]:
    """Aggregate the whole program into the flat metric dictionary gates consume."""
    assays = assays or []
    findings = findings or {}
    per_molecule = [molecule_metrics(e) for e in evaluations]
    clean = [m for m in per_molecule if not m["blocking_alerts"]]
    ranked = sorted(
        evaluations, key=lambda e: (-(e.get("composite_score") or 0.0), e.get("smiles") or "")
    )
    best = molecule_metrics(ranked[0]) if ranked else {}
    backup = molecule_metrics(ranked[1]) if len(ranked) > 1 else {}
    series = {e.get("scaffold_key") for e in evaluations if e.get("scaffold_key")}
    hit_hashes = {
        e.get("molecule_hash")
        for e, m in zip(evaluations, per_molecule, strict=False)
        if (m["pkd"] or 0.0) >= POTENCY_HIT_PKD
    }
    drift = prediction_drift(evaluations, assays)
    confirmed = {row.get("molecule_hash") for row in assays if row.get("molecule_hash")}

    metrics: dict[str, float] = {
        "molecule_count": float(len(evaluations)),
        "clean_molecule_count": float(len(clean)),
        "hit_count": float(len(hit_hashes)),
        "series_count": float(len(series)),
        "stage_cycles": float(stage_cycles),
        "assay_count": float(len(assays)),
        "assay_confirmed_count": float(len(confirmed)),
        "blocking_alert_free": float(1.0 if best and not best.get("blocking_alerts") else 0.0),
    }
    for key, mode in (
        ("pkd", "max"),
        ("composite_score", "max"),
        ("admet_score", "max"),
        ("qed_like", "max"),
        ("ligand_efficiency", "max"),
        ("selectivity_fold", "max"),
        ("half_life_h", "max"),
        ("hia", "max"),
        ("tox_margin", "max"),
        ("sa_score", "min"),
        ("cost_per_gram_usd", "min"),
        ("oral_dose_mg", "min"),
        ("herg_risk", "min"),
        ("ames_risk", "min"),
        ("hepatotoxicity_risk", "min"),
        ("cyp_risk", "min"),
    ):
        # "best" means best-in-portfolio for that property; the gate then reads it as the
        # property of the molecule the program would carry forward.
        value = _best([m[key] for m in (clean or per_molecule)], mode=mode)
        if value is not None:
            metrics[f"best_{key}"] = round(value, 4)
    # Measured data outranks prediction: where the lab has spoken, the gate reads the lab.
    for metric_key, (target_key, mode) in MEASURED_OVERRIDES.items():
        observed = [
            float(row["value"])
            for row in assays
            if str(row.get("metric") or "").lower() == metric_key
            and isinstance(row.get("value"), int | float)
        ]
        if metric_key == "pkd":
            observed = [v for v in (_observed_pkd(row) for row in assays) if v is not None]
        if not observed:
            continue
        value = max(observed) if mode == "max" else min(observed)
        metrics[target_key] = round(value, 4)
        metrics[f"measured_{target_key}"] = round(value, 4)
    if backup:
        backup_score = backup.get("composite_score")
        if backup_score is not None:
            metrics["backup_composite_score"] = round(backup_score, 4)
    if drift["rmse"] is not None:
        metrics["prediction_drift_rmse"] = drift["rmse"]
    for key in FINDING_KEYS:
        value = findings.get(key)
        if isinstance(value, bool | int | float):
            metrics[key] = float(value)
    return metrics
