"""Wet-lab experiment proposals derived from what the gate is actually missing.

Pharmakon cannot run experiments. What it can do is state precisely which measurement would
change a decision, why, what it costs and what result would falsify the current prediction.
Every proposal therefore carries the predicted value and the decision it would settle, so an
ingested result either confirms the model or measurably contradicts it (see
:func:`app.pharma.metrics.prediction_drift`).
"""

from __future__ import annotations

# Maps a gate metric to the cheapest assay that can settle it.
ASSAY_CATALOGUE: dict[str, dict] = {
    "best_pkd": {
        "assay": "Target binding affinity (SPR, dose-response)",
        "endpoint": "kd_nm",
        "unit": "nM",
        "turnaround_days": 7,
        "cost_usd": 1200,
        "n_compounds": 6,
    },
    "best_selectivity_fold": {
        "assay": "Off-target panel (closest family members, single concentration + follow-up)",
        "endpoint": "ic50_nm",
        "unit": "nM",
        "turnaround_days": 14,
        "cost_usd": 3400,
        "n_compounds": 3,
    },
    "best_herg_risk": {
        "assay": "hERG patch-clamp (manual, 5-point)",
        "endpoint": "ic50_nm",
        "unit": "nM",
        "turnaround_days": 10,
        "cost_usd": 2600,
        "n_compounds": 2,
    },
    "best_ames_risk": {
        "assay": "Mini-Ames bacterial reverse mutation (5 strains, +/- S9)",
        "endpoint": "ames_positive",
        "unit": "boolean",
        "turnaround_days": 14,
        "cost_usd": 4200,
        "n_compounds": 2,
    },
    "best_hepatotoxicity_risk": {
        "assay": "Primary human hepatocyte cytotoxicity + mitochondrial toxicity",
        "endpoint": "cc50_um",
        "unit": "uM",
        "turnaround_days": 12,
        "cost_usd": 3100,
        "n_compounds": 3,
    },
    "best_admet_score": {
        "assay": "In-vitro ADME cassette (Caco-2, microsomal stability, PPB, solubility)",
        "endpoint": "clint_ul_min_mg",
        "unit": "uL/min/mg",
        "turnaround_days": 12,
        "cost_usd": 2900,
        "n_compounds": 4,
    },
    "best_half_life_h": {
        "assay": "Rodent PK (IV/PO cassette, 6 timepoints)",
        "endpoint": "half_life_h",
        "unit": "h",
        "turnaround_days": 21,
        "cost_usd": 8600,
        "n_compounds": 2,
    },
    "best_hia": {
        "assay": "Caco-2 bidirectional permeability",
        "endpoint": "papp_1e6_cm_s",
        "unit": "1e-6 cm/s",
        "turnaround_days": 9,
        "cost_usd": 1500,
        "n_compounds": 4,
    },
    "best_tox_margin": {
        "assay": "14-day repeat-dose tolerability with exposure (non-GLP)",
        "endpoint": "noael_mg_kg",
        "unit": "mg/kg/day",
        "turnaround_days": 45,
        "cost_usd": 42000,
        "n_compounds": 1,
    },
    "best_cyp_risk": {
        "assay": "CYP inhibition panel (5 isoforms, IC50)",
        "endpoint": "ic50_um",
        "unit": "uM",
        "turnaround_days": 10,
        "cost_usd": 2200,
        "n_compounds": 3,
    },
    "best_sa_score": {
        "assay": "Route confirmation: synthesise the proposed analog set at 50 mg scale",
        "endpoint": "isolated_yield_pct",
        "unit": "%",
        "turnaround_days": 28,
        "cost_usd": 9500,
        "n_compounds": 6,
    },
    "assay_confirmed_count": {
        "assay": "Target binding affinity (SPR, dose-response)",
        "endpoint": "kd_nm",
        "unit": "nM",
        "turnaround_days": 7,
        "cost_usd": 1200,
        "n_compounds": 6,
    },
}

# Predictions in the same unit as the assay endpoint, so a measured result can be subtracted
# from them and feed prediction drift.
PREDICTED_ENDPOINT: dict[str, tuple[str, ...]] = {
    "best_pkd": ("binding", "kd_nm"),
    "best_half_life_h": ("admet", "excretion", "half_life_h"),
}

# Dimensionless risk/quality scores: reported for context, never compared numerically with the
# assay readout, because subtracting a score from a concentration is meaningless.
PREDICTED_RISK: dict[str, tuple[str, ...]] = {
    "best_herg_risk": ("admet", "toxicity", "herg", "risk"),
    "best_hia": ("admet", "absorption", "human_intestinal_absorption"),
    "best_tox_margin": ("dose_projection", "predicted_safety_margin"),
    "best_admet_score": ("admet", "admet_score"),
    "best_ames_risk": ("admet", "toxicity", "genotoxicity", "ames_risk"),
    "best_hepatotoxicity_risk": ("admet", "toxicity", "hepatotoxicity_risk"),
}


def _dig(node: object, path: tuple[str, ...]) -> object:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def propose_experiments(
    gate_result: dict,
    candidates: list[dict],
    *,
    max_proposals: int = 4,
    budget_usd: float | None = None,
) -> list[dict]:
    """Rank the experiments that would most change the current gate decision."""
    failing = [c for c in gate_result.get("criteria", []) if not c.get("passed")]
    failing.sort(key=lambda c: (not c.get("blocking"), -float(c.get("weight") or 0.0)))
    lead = candidates[0] if candidates else None
    proposals: list[dict] = []
    spent = 0.0
    for criterion in failing:
        template = ASSAY_CATALOGUE.get(criterion["key"])
        if template is None:
            continue
        cost = float(template["cost_usd"])
        if budget_usd is not None and spent + cost > budget_usd:
            continue
        n = min(int(template["n_compounds"]), max(1, len(candidates)))
        subjects = [
            {"smiles": c.get("smiles"), "molecule_hash": c.get("molecule_hash")}
            for c in candidates[:n]
        ]
        endpoint_path = PREDICTED_ENDPOINT.get(criterion["key"])
        risk_path = PREDICTED_RISK.get(criterion["key"])
        predicted = _dig(lead, endpoint_path) if (lead and endpoint_path) else None
        predicted_risk = _dig(lead, risk_path) if (lead and risk_path) else None
        proposals.append(
            {
                "metric": criterion["key"],
                "assay": template["assay"],
                "endpoint": template["endpoint"],
                "unit": template["unit"],
                "compounds": subjects,
                "cost_usd": cost,
                "turnaround_days": template["turnaround_days"],
                "blocking_for_gate": bool(criterion.get("blocking")),
                "decision_it_settles": (
                    f"{criterion['label']} must reach {criterion['requirement']} for the "
                    f"{gate_result.get('stage')} gate to pass."
                ),
                # Only set when the model predicts the very quantity the assay measures.
                "predicted_value": predicted,
                "predicted_risk_score": predicted_risk,
                "falsifies_if": (
                    f"a measured {template['endpoint']} inconsistent with the predicted "
                    f"{template['endpoint']} raises prediction drift and re-anchors the model"
                    if predicted is not None
                    else (
                        "the model only produces a dimensionless risk score here, so a measured "
                        f"{template['endpoint']} replaces the estimate outright rather than "
                        "scoring it"
                    )
                ),
                "priority": len(proposals) + 1,
            }
        )
        spent += cost
        if len(proposals) >= max_proposals:
            break
    return proposals


def evidence_tasks(gate_result: dict) -> list[dict]:
    """Failing criteria that no wet-lab assay can settle.

    Target validation, freedom to operate or a documented starting dose are answered by reading
    and by judgement, not by running a plate. Naming them keeps a blocked gate legible instead of
    looking like an empty experiment plan.
    """
    tasks: list[dict] = []
    for criterion in gate_result.get("criteria", []):
        if criterion.get("passed") or criterion["key"] in ASSAY_CATALOGUE:
            continue
        tasks.append(
            {
                "metric": criterion["key"],
                "label": criterion.get("label"),
                "requirement": criterion.get("requirement"),
                "blocking_for_gate": bool(criterion.get("blocking")),
                "settled_by": (
                    "cited literature, patent or regulatory evidence recorded against this "
                    "program - not by an assay"
                ),
            }
        )
    return tasks


def experiment_plan(
    gate_result: dict, candidates: list[dict], *, budget_usd: float | None = None
) -> dict:
    proposals = propose_experiments(gate_result, candidates, budget_usd=budget_usd)
    return {
        "stage": gate_result.get("stage"),
        "decision_context": gate_result.get("decision"),
        "proposals": proposals,
        "evidence_tasks": evidence_tasks(gate_result),
        "total_cost_usd": round(sum(p["cost_usd"] for p in proposals), 2),
        "critical_path_days": max((p["turnaround_days"] for p in proposals), default=0),
        "note": (
            "Costs and turnarounds are CRO list-price order-of-magnitude estimates for planning; "
            "confirm with the chosen provider before committing spend."
        ),
    }
