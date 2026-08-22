"""Ligand-pocket complementarity proxy for protein-small-molecule pairs.

This is *not* docking: there are no coordinates and no force field. It is a deterministic,
explainable complementarity model (shape fit, hydrophobic burial, H-bond pairing,
electrostatics, aromatic stacking, desolvation) that produces a bounded affinity estimate
with an uncertainty band, so ranking is reproducible and never presented as a docked pose.
"""

from __future__ import annotations

import math

from app.chem.constants import (
    RESIDUE_ACCEPTOR,
    RESIDUE_AROMATIC,
    RESIDUE_DONOR,
    RESIDUE_HYDROPHOBIC,
    RESIDUE_NEGATIVE,
    RESIDUE_POSITIVE,
    RESIDUE_VOLUME,
)
from app.chem.descriptors import clogp, hba_count, hbd_count, rotatable_bonds, tpsa
from app.chem.groups import find_groups
from app.chem.smiles import Molecule, parse_smiles

HEAVY_ATOM_VOLUME = 20.4  # A^3 per heavy atom (van der Waals average)
POCKET_WINDOW = 22

WEIGHTS = {
    "hydrophobic": 1.25,
    "shape": 1.15,
    "hbond": 0.35,
    "electrostatic": 0.55,
    "stacking": 0.38,
    "desolvation": 0.9,
    "entropy": 0.22,
}
BASELINE_PKD = 2.6  # a generic organic molecule against a generic pocket

# Saturation caps. Counting every formally possible interaction makes large molecules look
# unbeatable; in practice only a handful of contacts are geometrically productive at once, and
# without coordinates this proxy cannot tell which. Capping keeps the ranking honest about that.
MAX_HBOND_PAIRS = 4
MAX_STACKS = 3
MAX_ELECTROSTATIC = 1.0


def pocket_from_sequence(sequence: str, pocket_residues: list[int] | None = None) -> dict:
    """Derive a pocket fingerprint from sequence (optionally restricted to given residues).

    Without explicit pocket residues the most hydrophobic/aromatic window is used as a
    putative ligand-binding site, which is deterministic and good enough for ranking.
    """
    seq = "".join(ch for ch in (sequence or "").upper() if ch.isalpha())
    if not seq:
        raise ValueError("empty sequence")
    if pocket_residues:
        indices = [i - 1 for i in pocket_residues if 1 <= i <= len(seq)]
        residues = "".join(seq[i] for i in indices)
        source = "explicit residues"
    else:
        window = min(POCKET_WINDOW, len(seq))
        best_start, best_score = 0, -1.0
        for start in range(0, len(seq) - window + 1):
            sub = seq[start : start + window]
            score = sum(1.0 for c in sub if c in RESIDUE_HYDROPHOBIC) + 0.5 * sum(
                1.0 for c in sub if c in RESIDUE_AROMATIC
            )
            if score > best_score:
                best_start, best_score = start, score
        indices = list(range(best_start, best_start + window))
        residues = seq[best_start : best_start + window]
        source = f"putative hydrophobic window {best_start + 1}-{best_start + window}"
    n = max(1, len(residues))
    volume = sum(RESIDUE_VOLUME.get(c, 120.0) for c in residues) * 0.45  # accessible fraction
    return {
        "residues": residues,
        "residue_indices": [i + 1 for i in indices],
        "source": source,
        "hydrophobic_fraction": round(sum(1 for c in residues if c in RESIDUE_HYDROPHOBIC) / n, 3),
        "aromatic_residues": sum(1 for c in residues if c in RESIDUE_AROMATIC),
        "donors": sum(1 for c in residues if c in RESIDUE_DONOR),
        "acceptors": sum(1 for c in residues if c in RESIDUE_ACCEPTOR),
        "net_charge": sum(1 for c in residues if c in RESIDUE_POSITIVE)
        - sum(1 for c in residues if c in RESIDUE_NEGATIVE),
        "volume_a3": round(volume, 1),
    }


def _mol(target: Molecule | str) -> Molecule:
    return target if isinstance(target, Molecule) else parse_smiles(target)


def _terms(mol: Molecule, pocket: dict) -> dict[str, float]:
    logp = clogp(mol)
    heavy = mol.heavy_atom_count
    ligand_volume = heavy * HEAVY_ATOM_VOLUME
    fit = min(ligand_volume, pocket["volume_a3"]) / max(pocket["volume_a3"], 1.0)
    overfill = max(0.0, ligand_volume - pocket["volume_a3"]) / max(pocket["volume_a3"], 1.0)
    groups = find_groups(mol)
    ligand_charge = mol.formal_charge or (
        len(groups.get("basic_center", [])) - len(groups.get("acidic_center", []))
    )
    pairs = min(hbd_count(mol), pocket["acceptors"]) + min(hba_count(mol), pocket["donors"])
    electrostatic = -0.5 * ligand_charge * pocket["net_charge"]
    return {
        "hydrophobic": min(logp, 5.0) * pocket["hydrophobic_fraction"],
        "shape": fit - 1.6 * overfill,
        "hbond": float(min(pairs, MAX_HBOND_PAIRS)),
        "electrostatic": max(-MAX_ELECTROSTATIC, min(MAX_ELECTROSTATIC, electrostatic)),
        "stacking": float(min(len(mol.aromatic_rings()), pocket["aromatic_residues"], MAX_STACKS)),
        "desolvation": -tpsa(mol) / 100.0,
        "entropy": -0.15 * rotatable_bonds(mol),
    }


def score_ligand_pocket(target: Molecule | str, pocket: dict) -> dict:
    """Predicted affinity (pKd), free energy and uncertainty for a ligand-pocket pair."""
    mol = _mol(target)
    terms = _terms(mol, pocket)
    contributions = {k: round(WEIGHTS[k] * v, 3) for k, v in terms.items()}
    pkd = BASELINE_PKD + sum(contributions.values())
    pkd = round(min(11.0, max(2.0, pkd)), 2)
    # Deterministic sensitivity ensemble: +/-15% on each weight, spread = uncertainty.
    ensemble: list[float] = []
    for key in WEIGHTS:
        for delta in (0.85, 1.15):
            scaled = dict(WEIGHTS)
            scaled[key] = WEIGHTS[key] * delta
            ensemble.append(BASELINE_PKD + sum(scaled[k] * v for k, v in terms.items()))
    spread = round((max(ensemble) - min(ensemble)) / 2.0, 2)
    kd_nm = round(10 ** (9 - pkd), 2)
    heavy = mol.heavy_atom_count or 1
    return {
        "pkd": pkd,
        "kd_nm": kd_nm,
        "delta_g_kcal_per_mol": round(-1.364 * pkd, 2),
        "uncertainty_pkd": spread,
        "confidence": round(max(0.05, min(0.95, 1.0 - spread / 2.0)), 3),
        "ligand_efficiency": round(1.364 * pkd / heavy, 3),
        "lipophilic_efficiency": round(pkd - clogp(mol), 2),
        "contributions": contributions,
        "pocket": {k: v for k, v in pocket.items() if k != "residue_indices"},
        "method": "complementarity proxy (no docked pose, no force field)",
    }


def selectivity(target: Molecule | str, on_target: dict, off_targets: dict[str, dict]) -> dict:
    """Fold-selectivity of a ligand for the on-target pocket versus named off-targets."""
    mol = _mol(target)
    on = score_ligand_pocket(mol, on_target)
    offs = {name: score_ligand_pocket(mol, pocket) for name, pocket in sorted(off_targets.items())}
    folds = {name: round(10 ** (on["pkd"] - result["pkd"]), 1) for name, result in offs.items()}
    worst = min(folds.values(), default=float("inf"))
    return {
        "on_target_pkd": on["pkd"],
        "off_target_pkd": {name: result["pkd"] for name, result in offs.items()},
        "fold_selectivity": folds,
        "min_fold_selectivity": None if math.isinf(worst) else worst,
        "selective": bool(worst >= 30),
    }
