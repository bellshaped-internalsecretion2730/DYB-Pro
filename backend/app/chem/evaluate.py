"""Composite molecule evaluation and ranking used by gates, agents and the API."""

from __future__ import annotations

from app.chem.admet import admet_panel
from app.chem.alerts import alert_report
from app.chem.binding import pocket_from_sequence, score_ligand_pocket, selectivity
from app.chem.descriptors import descriptors, ligand_efficiency, lipophilic_efficiency
from app.chem.pk import dose_projection
from app.chem.smiles import SmilesError, molecule_hash, parse_smiles, scaffold_key
from app.chem.synthesis import synthesis_report

# Stage-aware weights: early stages reward potency/efficiency, late stages reward safety
# and developability. Keys must sum to 1.0 per stage.
STAGE_WEIGHTS: dict[str, dict[str, float]] = {
    "hit_finding": {"potency": 0.42, "drug_likeness": 0.2, "admet": 0.14, "safety": 0.14, "synthesis": 0.1},
    "hit_to_lead": {"potency": 0.34, "drug_likeness": 0.18, "admet": 0.2, "safety": 0.18, "synthesis": 0.1},
    "lead_optimization": {
        "potency": 0.26,
        "drug_likeness": 0.14,
        "admet": 0.26,
        "safety": 0.24,
        "synthesis": 0.1,
    },
    "candidate_selection": {
        "potency": 0.2,
        "drug_likeness": 0.12,
        "admet": 0.28,
        "safety": 0.32,
        "synthesis": 0.08,
    },
}
DEFAULT_STAGE = "hit_to_lead"


def evaluate_molecule(
    smiles: str,
    target_sequence: str | None = None,
    pocket: dict | None = None,
    stage: str = DEFAULT_STAGE,
    pocket_residues: list[int] | None = None,
    off_target_pockets: dict[str, dict] | None = None,
) -> dict:
    """Deterministic full evaluation of one molecule against an optional protein target."""
    mol = parse_smiles(smiles)
    alerts = alert_report(mol)
    desc = descriptors(mol, alert_count=alerts["count"])
    panel = admet_panel(mol)
    synth = synthesis_report(mol)
    binding: dict | None = None
    if pocket is None and target_sequence:
        pocket = pocket_from_sequence(target_sequence, pocket_residues=pocket_residues)
    if pocket:
        binding = score_ligand_pocket(mol, pocket)
    potency_nm = binding["kd_nm"] if binding else None
    dose = (
        dose_projection(mol, potency_nm=potency_nm, panel=panel)
        if potency_nm is not None and potency_nm < 1e6
        else None
    )
    scores = _score_components(desc, panel, alerts, synth, binding)
    weights = STAGE_WEIGHTS.get(stage, STAGE_WEIGHTS[DEFAULT_STAGE])
    composite = sum(weights[k] * scores[k] for k in weights)
    if alerts["blocking"]:
        composite *= 0.3
    return {
        "smiles": mol.smiles,
        "molecule_hash": molecule_hash(mol),
        "scaffold_key": scaffold_key(mol),
        "formula": mol.formula,
        "stage": stage,
        "descriptors": desc,
        "admet": panel,
        "liabilities": alerts,
        "synthesis": synth,
        "binding": binding,
        "selectivity": (
            selectivity(mol, pocket, off_target_pockets)
            if pocket and off_target_pockets
            else None
        ),
        "dose_projection": dose,
        "component_scores": {k: round(v, 3) for k, v in scores.items()},
        "composite_score": round(composite, 4),
        "uncertainty": round(binding["uncertainty_pkd"] / 10.0 if binding else 0.25, 3),
        "efficiency": (
            {
                "ligand_efficiency": ligand_efficiency(binding["pkd"], desc["heavy_atoms"]),
                "lipophilic_efficiency": lipophilic_efficiency(binding["pkd"], desc["clogp"]),
            }
            if binding
            else None
        ),
        "verdict": _verdict(composite, alerts, panel),
    }


def _score_components(desc: dict, panel: dict, alerts: dict, synth: dict, binding: dict | None) -> dict:
    potency = min(1.0, max(0.0, ((binding["pkd"] if binding else 5.0) - 4.0) / 5.0))
    return {
        "potency": potency,
        "drug_likeness": desc["qed_like"],
        "admet": panel["admet_score"],
        "safety": max(0.0, 1.0 - alerts["penalty"]),
        "synthesis": max(0.0, min(1.0, (10.0 - synth["sa_score"]) / 8.0)),
    }


def _verdict(composite: float, alerts: dict, panel: dict) -> str:
    if alerts["blocking"]:
        return "reject: blocking structural alert"
    if composite >= 0.7:
        return "advance"
    if composite >= 0.5:
        return "optimise"
    if panel["toxicity"]["herg"]["band"] == "high":
        return "optimise: mitigate hERG before advancing"
    return "deprioritise"


def rank_molecules(
    smiles_list: list[str],
    target_sequence: str | None = None,
    pocket: dict | None = None,
    stage: str = DEFAULT_STAGE,
    top_k: int | None = None,
    off_target_pockets: dict[str, dict] | None = None,
) -> dict:
    """Evaluate and rank a set of molecules, reporting per-molecule failures explicitly."""
    if pocket is None and target_sequence:
        pocket = pocket_from_sequence(target_sequence)
    evaluations: list[dict] = []
    failures: list[dict] = []
    for smiles in smiles_list:
        try:
            evaluations.append(
                evaluate_molecule(
                    smiles, pocket=pocket, stage=stage, off_target_pockets=off_target_pockets
                )
            )
        except (SmilesError, ValueError) as exc:
            failures.append({"smiles": smiles, "error": str(exc)})
    evaluations.sort(key=lambda e: (-e["composite_score"], e["smiles"]))
    for rank, item in enumerate(evaluations, start=1):
        item["rank"] = rank
    ranked = evaluations[:top_k] if top_k else evaluations
    return {
        "stage": stage,
        "pocket": pocket,
        "ranked": ranked,
        "failures": failures,
        "count": len(evaluations),
        "top_score": ranked[0]["composite_score"] if ranked else 0.0,
    }
