"""Molecular descriptors: MW, TPSA, cLogP, H-bond counts, QED-style score, solubility."""

from __future__ import annotations

import math

from app.chem.constants import (
    ELEMENT_TPSA_FALLBACK,
    HALOGENS,
    LOGP_CONTRIB,
    LOGP_INTERCEPT,
    POLAR_ELEMENTS,
    TPSA_TABLE,
)
from app.chem.groups import find_groups, is_amide_nitrogen, is_carbonyl
from app.chem.smiles import Molecule, parse_smiles


def _mol(target: Molecule | str) -> Molecule:
    return target if isinstance(target, Molecule) else parse_smiles(target)


# --------------------------------------------------------------------------- TPSA


def tpsa(target: Molecule | str) -> float:
    """Topological polar surface area (Ertl 2000 fragment contributions)."""
    mol = _mol(target)
    total = 0.0
    for atom in mol.atoms:
        if atom.element not in POLAR_ELEMENTS:
            continue
        key = (atom.element, mol.degree(atom.index), atom.total_h, atom.aromatic, atom.charge)
        total += TPSA_TABLE.get(key, ELEMENT_TPSA_FALLBACK[atom.element])
    return round(total, 2)


# --------------------------------------------------------------------------- logP


def _logp_atom_type(mol: Molecule, idx: int) -> str:
    atom = mol.atoms[idx]
    el = atom.element
    hetero_neighbor = any(mol.atoms[n].is_hetero for n in mol.neighbors(idx))
    if el == "C":
        if atom.aromatic:
            return "C_ar_hetero" if hetero_neighbor else "C_ar"
        max_order = max((b.order for b in mol.bonds_of(idx)), default=1.0)
        if max_order >= 3.0:
            return "C_sp"
        if max_order >= 2.0:
            return "C_sp2_hetero" if hetero_neighbor else "C_sp2"
        return "C_sp3_hetero" if hetero_neighbor else "C_sp3"
    if el == "N":
        if atom.charge != 0:
            return "N_charged"
        if any(b.order >= 3 for b in mol.bonds_of(idx)):
            return "N_nitrile"
        if atom.aromatic:
            return "N_aromatic"
        if is_amide_nitrogen(mol, idx):
            return "N_amide"
        degree = mol.degree(idx)
        return {1: "N_amine_pri", 2: "N_amine_sec"}.get(degree, "N_amine_ter")
    if el == "O":
        if atom.charge != 0:
            return "O_charged"
        if atom.aromatic:
            return "O_aromatic"
        if any(b.order >= 2 for b in mol.bonds_of(idx)):
            return "O_carbonyl"
        if atom.total_h:
            return "O_hydroxyl"
        return "O_ether"
    if el == "S":
        if len([b for b in mol.bonds_of(idx) if b.order >= 2]) >= 1 and any(
            mol.atoms[b.other(idx)].element == "O" for b in mol.bonds_of(idx)
        ):
            return "S_oxidized"
        return "S_aromatic" if atom.aromatic else "S_thio"
    if el in HALOGENS:
        return el
    return el if el in LOGP_CONTRIB else "other"


def clogp(target: Molecule | str) -> float:
    """Additive Wildman-Crippen-style logP estimate (approximate, see CITATIONS.md)."""
    mol = _mol(target)
    total = LOGP_INTERCEPT
    for atom in mol.atoms:
        total += LOGP_CONTRIB.get(_logp_atom_type(mol, atom.index), 0.0)
        if atom.total_h:
            key = "H_C" if atom.element == "C" else "H_hetero"
            total += atom.total_h * LOGP_CONTRIB[key]
    return round(total, 2)


# --------------------------------------------------------------------------- counts


def hbd_count(target: Molecule | str) -> int:
    mol = _mol(target)
    return sum(1 for a in mol.atoms if a.element in ("N", "O") and a.total_h > 0)


def hba_count(target: Molecule | str) -> int:
    """Acceptor count excluding amide/aniline N, protonated N and hydroxyl-bridging O."""
    mol = _mol(target)
    total = 0
    for atom in mol.atoms:
        if atom.element == "O":
            total += 1
        elif atom.element == "N":
            if atom.charge > 0 or is_amide_nitrogen(mol, atom.index):
                continue
            if any(mol.atoms[n].aromatic for n in mol.neighbors(atom.index)) and not atom.aromatic:
                continue
            total += 1
    return total


def rotatable_bonds(target: Molecule | str) -> int:
    """Veber definition: acyclic single bonds between heavy atoms, amides excluded."""
    mol = _mol(target)
    count = 0
    for bond in mol.bonds:
        if bond.order != 1.0 or bond.aromatic or mol.bond_in_ring(bond):
            continue
        a, b = mol.atoms[bond.a], mol.atoms[bond.b]
        if mol.degree(a.index) < 2 or mol.degree(b.index) < 2:
            continue
        if {a.element, b.element} == {"C", "N"}:
            n_idx = a.index if a.element == "N" else b.index
            c_idx = b.index if a.element == "N" else a.index
            if is_carbonyl(mol, c_idx) and is_amide_nitrogen(mol, n_idx):
                continue
        count += 1
    return count


def fraction_sp3(target: Molecule | str) -> float:
    mol = _mol(target)
    carbons = [a for a in mol.atoms if a.element == "C"]
    if not carbons:
        return 0.0
    sp3 = sum(
        1
        for a in carbons
        if not a.aromatic and max((b.order for b in mol.bonds_of(a.index)), default=1.0) < 2.0
    )
    return round(sp3 / len(carbons), 3)


def heteroatom_fraction(target: Molecule | str) -> float:
    mol = _mol(target)
    heavy = mol.heavy_atom_count or 1
    return round(sum(1 for a in mol.atoms if a.is_hetero) / heavy, 3)


def aromatic_atom_fraction(target: Molecule | str) -> float:
    mol = _mol(target)
    heavy = mol.heavy_atom_count or 1
    return round(sum(1 for a in mol.atoms if a.aromatic) / heavy, 3)


# --------------------------------------------------------------------------- solubility


def esol_logs(target: Molecule | str) -> float:
    """Delaney (2004) ESOL: intrinsic aqueous solubility log10(mol/L)."""
    mol = _mol(target)
    logp = clogp(mol)
    mw = mol.molecular_weight
    rb = rotatable_bonds(mol)
    ap = aromatic_atom_fraction(mol)
    return round(0.16 - 0.63 * logp - 0.0062 * mw + 0.066 * rb - 0.74 * ap, 2)


def solubility_mg_per_ml(target: Molecule | str) -> float:
    mol = _mol(target)
    return round(10 ** esol_logs(mol) * mol.molecular_weight * 1000 / 1000, 4)


# --------------------------------------------------------------------------- drug-likeness


def _desirability(value: float, low: float, high: float, soft: float) -> float:
    """Trapezoidal desirability: 1.0 inside [low, high], decaying over `soft` outside."""
    if low <= value <= high:
        return 1.0
    if value < low:
        return max(0.0, 1.0 - (low - value) / soft)
    return max(0.0, 1.0 - (value - high) / soft)


QED_WEIGHTS: dict[str, float] = {
    "mw": 0.66,
    "logp": 0.94,
    "hbd": 0.61,
    "hba": 0.62,
    "tpsa": 0.94,
    "rotb": 0.72,
    "arom": 0.55,
    "alerts": 0.24,
}


def qed_like(target: Molecule | str, alert_count: int = 0) -> float:
    """QED-style weighted geometric mean of property desirabilities (Bickerton 2012 spirit)."""
    mol = _mol(target)
    parts = {
        "mw": _desirability(mol.molecular_weight, 250.0, 480.0, 150.0),
        "logp": _desirability(clogp(mol), 0.5, 4.0, 2.5),
        "hbd": _desirability(hbd_count(mol), 0.0, 3.0, 3.0),
        "hba": _desirability(hba_count(mol), 1.0, 8.0, 4.0),
        "tpsa": _desirability(tpsa(mol), 40.0, 120.0, 60.0),
        "rotb": _desirability(rotatable_bonds(mol), 0.0, 7.0, 5.0),
        "arom": _desirability(len(mol.aromatic_rings()), 1.0, 3.0, 2.0),
        "alerts": _desirability(alert_count, 0.0, 0.0, 3.0),
    }
    num = sum(QED_WEIGHTS[k] * math.log(max(parts[k], 1e-3)) for k in parts)
    den = sum(QED_WEIGHTS.values())
    return round(math.exp(num / den), 3)


# --------------------------------------------------------------------------- rule sets


def rule_of_five(target: Molecule | str) -> dict:
    mol = _mol(target)
    mw, logp = mol.molecular_weight, clogp(mol)
    hbd, hba = hbd_count(mol), sum(1 for a in mol.atoms if a.element in ("N", "O"))
    violations = [
        name
        for name, bad in (
            ("MW>500", mw > 500),
            ("cLogP>5", logp > 5),
            ("HBD>5", hbd > 5),
            ("N+O>10", hba > 10),
        )
        if bad
    ]
    return {"violations": violations, "pass": len(violations) <= 1}


def veber(target: Molecule | str) -> dict:
    mol = _mol(target)
    rb, ps = rotatable_bonds(mol), tpsa(mol)
    violations = [name for name, bad in (("RotB>10", rb > 10), ("TPSA>140", ps > 140)) if bad]
    return {"violations": violations, "pass": not violations}


def lead_likeness(target: Molecule | str) -> dict:
    mol = _mol(target)
    mw, logp = mol.molecular_weight, clogp(mol)
    violations = [
        name
        for name, bad in (
            ("MW<200", mw < 200),
            ("MW>350", mw > 350),
            ("cLogP>3.5", logp > 3.5),
            ("RotB>7", rotatable_bonds(mol) > 7),
        )
        if bad
    ]
    return {"violations": violations, "pass": not violations}


def cns_mpo(target: Molecule | str) -> float:
    """Pfizer CNS MPO-style 0-6 desirability sum (Wager 2010, simplified monotone ramps)."""
    mol = _mol(target)
    logp = clogp(mol)
    logd = logp - 1.0
    mw = mol.molecular_weight
    ps = tpsa(mol)
    hbd = hbd_count(mol)
    basic = 1 if find_groups(mol).get("basic_center") else 0
    pka = 9.0 if basic else 5.0

    def ramp(value: float, best: float, worst: float) -> float:
        if best == worst:
            return 1.0
        if best < worst:
            return min(1.0, max(0.0, (worst - value) / (worst - best)))
        return min(1.0, max(0.0, (value - worst) / (best - worst)))

    score = (
        ramp(logp, 3.0, 5.0)
        + ramp(logd, 2.0, 4.0)
        + ramp(mw, 360.0, 500.0)
        + (1.0 if 40 <= ps <= 90 else max(0.0, 1.0 - abs(ps - 65) / 60))
        + ramp(hbd, 0.5, 3.5)
        + ramp(pka, 8.0, 10.0)
    )
    return round(score, 2)


def ligand_efficiency(potency_pki: float, heavy_atoms: int) -> float:
    """LE = 1.37 * pKi / heavy atom count (kcal/mol per heavy atom)."""
    if heavy_atoms <= 0:
        return 0.0
    return round(1.37 * potency_pki / heavy_atoms, 3)


def lipophilic_efficiency(potency_pki: float, logp: float) -> float:
    """LLE = pKi - cLogP."""
    return round(potency_pki - logp, 3)


# --------------------------------------------------------------------------- aggregate


def descriptors(target: Molecule | str, alert_count: int = 0) -> dict:
    """Full descriptor payload for a molecule."""
    mol = _mol(target)
    groups = find_groups(mol)
    return {
        "smiles": mol.smiles,
        "formula": mol.formula,
        "molecular_weight": mol.molecular_weight,
        "heavy_atoms": mol.heavy_atom_count,
        "clogp": clogp(mol),
        "tpsa": tpsa(mol),
        "hbd": hbd_count(mol),
        "hba": hba_count(mol),
        "rotatable_bonds": rotatable_bonds(mol),
        "rings": mol.ring_count,
        "aromatic_rings": len(mol.aromatic_rings()),
        "fraction_sp3": fraction_sp3(mol),
        "heteroatom_fraction": heteroatom_fraction(mol),
        "aromatic_fraction": aromatic_atom_fraction(mol),
        "stereocenters": mol.stereocenters,
        "formal_charge": mol.formal_charge,
        "halogens": sum(1 for a in mol.atoms if a.element in HALOGENS),
        "basic_centers": len(groups.get("basic_center", [])),
        "acidic_centers": len(groups.get("acidic_center", [])),
        "esol_logs": esol_logs(mol),
        "qed_like": qed_like(mol, alert_count=alert_count),
        "cns_mpo": cns_mpo(mol),
        "rule_of_five": rule_of_five(mol),
        "veber": veber(mol),
        "lead_like": lead_likeness(mol),
        "functional_groups": {k: len(v) for k, v in sorted(groups.items())},
    }
