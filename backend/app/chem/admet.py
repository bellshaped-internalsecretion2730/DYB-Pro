"""ADMET prediction: absorption, distribution, metabolism, excretion and toxicity.

All estimators are transparent physchem regressions/heuristics with the driving terms
kept visible in the payload; every block is tagged with its method so downstream
agents and gate decisions can weight them honestly.
"""

from __future__ import annotations

import math

from app.chem.alerts import alert_report
from app.chem.descriptors import clogp, fraction_sp3, hba_count, hbd_count, tpsa
from app.chem.groups import find_groups
from app.chem.smiles import Molecule, parse_smiles

HEPATIC_BLOOD_FLOW = 1.24  # L/h/kg
GFR = 0.11  # L/h/kg
MICROSOMAL_SCALE = 45 * 21 * 60 / 1_000_000  # uL/min/mg -> L/h/kg


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _mol(target: Molecule | str) -> Molecule:
    return target if isinstance(target, Molecule) else parse_smiles(target)


# --------------------------------------------------------------------------- absorption


def absorption(target: Molecule | str) -> dict:
    mol = _mol(target)
    logp, ps, hbd = clogp(mol), tpsa(mol), hbd_count(mol)
    mw = mol.molecular_weight
    log_papp = -5.1 + 0.26 * min(logp, 5.0) - 0.011 * ps - 0.12 * hbd - 0.0008 * max(0.0, mw - 400)
    log_papp = round(max(-7.5, min(-4.0, log_papp)), 2)
    perm_index = (
        2.6
        - 0.018 * ps
        - 0.35 * max(0, hbd - 3)
        - 0.004 * max(0.0, mw - 500)
        + 0.22 * min(logp, 4.0)
    )
    hia = round(_logistic(perm_index), 3)
    pgp = round(
        min(
            0.95,
            max(
                0.03,
                0.05
                + 0.0016 * max(0.0, mw - 300)
                + 0.004 * max(0.0, ps - 70)
                + 0.06 * max(0.0, min(logp, 5.0) - 1.0),
            ),
        ),
        3,
    )
    return {
        "caco2_log_papp_cm_s": log_papp,
        "human_intestinal_absorption": hia,
        "pgp_substrate_probability": pgp,
        "bcs_class_hint": _bcs_hint(mol, hia),
        "method": "physchem regression (TPSA/HBD/MW/logP)",
    }


def _bcs_hint(mol: Molecule, hia: float) -> str:
    from app.chem.descriptors import esol_logs

    soluble = esol_logs(mol) > -4.5
    permeable = hia > 0.6
    if soluble and permeable:
        return "I (high solubility / high permeability)"
    if not soluble and permeable:
        return "II (low solubility / high permeability)"
    if soluble and not permeable:
        return "III (high solubility / low permeability)"
    return "IV (low solubility / low permeability)"


# --------------------------------------------------------------------------- distribution


def distribution(target: Molecule | str) -> dict:
    mol = _mol(target)
    logp, ps = clogp(mol), tpsa(mol)
    groups = find_groups(mol)
    acidic = len(groups.get("acidic_center", []))
    basic = len(groups.get("basic_center", []))
    ppb = _logistic(-1.3 + 0.72 * logp + 0.55 * acidic - 0.012 * ps)
    ppb = round(min(0.995, max(0.05, ppb)), 3)
    fu = round(max(0.005, 1.0 - ppb), 4)
    vss = 0.35 + 0.28 * max(0.0, logp) + 0.9 * basic - 0.22 * acidic + 2.5 * fu
    vss = round(max(0.1, min(20.0, vss)), 2)
    log_bb = round(-0.0148 * ps + 0.152 * logp + 0.139, 2)  # Clark (1999)
    return {
        "plasma_protein_binding": ppb,
        "fraction_unbound": fu,
        "vss_l_per_kg": vss,
        "log_bb": log_bb,
        "cns_penetrant": bool(log_bb > -0.3 and ps < 90),
        "method": "physchem regression + Clark logBB",
    }


# --------------------------------------------------------------------------- metabolism


SOFT_SPOTS: tuple[tuple[str, str], ...] = (
    ("ether", "O-dealkylation of the alkyl ether"),
    ("aniline", "N-oxidation / reactive nitrene formation on the aniline"),
    ("primary_amine", "oxidative deamination"),
    ("tertiary_amine", "N-dealkylation"),
    ("ester", "esterase hydrolysis"),
    ("thioether", "S-oxidation to sulfoxide"),
    ("alcohol", "phase-II glucuronidation"),
    ("phenol", "phase-II sulfation/glucuronidation"),
    ("alkene", "epoxidation of the olefin"),
    ("thiophene_s", "thiophene ring oxidation (reactive metabolite)"),
)


def metabolism(target: Molecule | str) -> dict:
    mol = _mol(target)
    logp = clogp(mol)
    groups = find_groups(mol)
    aromatic_rings = len(mol.aromatic_rings())
    clint = 4.0 + 6.5 * max(0.0, logp) + 3.0 * aromatic_rings + 8.0 * len(groups.get("ether", []))
    clint -= 6.0 * len(groups.get("acidic_center", [])) + 10.0 * fraction_sp3(mol)
    clint = round(max(1.5, min(400.0, clint)), 1)
    t_half_microsomal = round(0.693 / (clint * MICROSOMAL_SCALE / 25.0 + 1e-6) * 60 / 60, 1)
    soft_spots = [hint for name, hint in SOFT_SPOTS if groups.get(name)]
    alerts = alert_report(mol)
    return {
        "clint_ul_min_mg": clint,
        "microsomal_half_life_min": min(240.0, t_half_microsomal),
        "soft_spots": soft_spots,
        "cyp": alerts["cyp"],
        "reactive_metabolite_risk": round(
            min(1.0, 0.05 + 0.25 * len(groups.get("aniline", [])) + 0.2 * len(groups.get("thiophene_s", []))),
            3,
        ),
        "method": "lipophilicity/soft-spot heuristic",
    }


# --------------------------------------------------------------------------- excretion


def excretion(target: Molecule | str) -> dict:
    mol = _mol(target)
    dist = distribution(mol)
    met = metabolism(mol)
    fu = dist["fraction_unbound"]
    clint_scaled = met["clint_ul_min_mg"] * MICROSOMAL_SCALE
    cl_hepatic = HEPATIC_BLOOD_FLOW * fu * clint_scaled / (HEPATIC_BLOOD_FLOW + fu * clint_scaled)
    cl_renal = fu * GFR * (1.0 if clogp(mol) < 1.5 else 0.35)
    cl_total = round(max(0.01, cl_hepatic + cl_renal), 3)
    t_half = round(0.693 * dist["vss_l_per_kg"] / cl_total, 2)
    return {
        "cl_total_l_per_h_per_kg": cl_total,
        "cl_hepatic_l_per_h_per_kg": round(cl_hepatic, 3),
        "cl_renal_l_per_h_per_kg": round(cl_renal, 3),
        "renal_fraction": round(cl_renal / cl_total, 3),
        "half_life_h": t_half,
        "dosing_interval_hint": "QD" if t_half >= 8 else "BID" if t_half >= 4 else "TID or MR formulation",
        "method": "well-stirred hepatic model + GFR filtration",
    }


# --------------------------------------------------------------------------- toxicity


def toxicity(target: Molecule | str) -> dict:
    mol = _mol(target)
    report = alert_report(mol)
    groups = find_groups(mol)
    logp = clogp(mol)
    hepatotox = min(
        1.0,
        0.05
        + 0.12 * max(0.0, logp - 3.0)
        + 0.18 * len(groups.get("aniline", []))
        + 0.15 * len(groups.get("nitroaromatic", []))
        + 0.1 * len(groups.get("thiophene_s", [])),
    )
    return {
        "structural_alerts": report["alerts"],
        "alert_burden": report["burden"],
        "blocking_alerts": report["blocking"],
        "herg": report["herg"],
        "genotoxicity": report["genotoxicity"],
        "hepatotoxicity_risk": round(hepatotox, 3),
        "phospholipidosis_risk": round(
            min(1.0, 0.02 + (0.4 if groups.get("basic_center") and logp > 3.0 else 0.0)), 3
        ),
        "method": "structural alerts + physchem heuristics",
    }


# --------------------------------------------------------------------------- aggregate


def admet_panel(target: Molecule | str) -> dict:
    """Full ADMET panel plus a single 0-1 developability score with visible drivers."""
    mol = _mol(target)
    absorb = absorption(mol)
    dist = distribution(mol)
    met = metabolism(mol)
    exc = excretion(mol)
    tox = toxicity(mol)

    drivers: dict[str, float] = {
        "absorption": absorb["human_intestinal_absorption"],
        "exposure": min(1.0, exc["half_life_h"] / 12.0),
        "metabolic_stability": min(1.0, met["microsomal_half_life_min"] / 60.0),
        "free_fraction": min(1.0, dist["fraction_unbound"] / 0.1),
        "safety": max(0.0, 1.0 - tox["alert_burden"] / 2.0),
        "cardiac": 1.0 - tox["herg"]["risk"],
        "genotox": 1.0 - tox["genotoxicity"]["ames_risk"],
        "liver": 1.0 - tox["hepatotoxicity_risk"],
    }
    weights = {
        "absorption": 0.18,
        "exposure": 0.16,
        "metabolic_stability": 0.14,
        "free_fraction": 0.06,
        "safety": 0.16,
        "cardiac": 0.12,
        "genotox": 0.10,
        "liver": 0.08,
    }
    score = sum(weights[k] * max(0.0, min(1.0, drivers[k])) for k in weights)
    if tox["blocking_alerts"]:
        score *= 0.35
    return {
        "absorption": absorb,
        "distribution": dist,
        "metabolism": met,
        "excretion": exc,
        "toxicity": tox,
        "hbd": hbd_count(mol),
        "hba": hba_count(mol),
        "admet_score": round(score, 3),
        "drivers": {k: round(v, 3) for k, v in drivers.items()},
        "flags": _flags(absorb, exc, tox),
    }


def _flags(absorb: dict, exc: dict, tox: dict) -> list[str]:
    flags: list[str] = []
    if absorb["human_intestinal_absorption"] < 0.4:
        flags.append("low predicted oral absorption")
    if absorb["pgp_substrate_probability"] > 0.6:
        flags.append("likely P-gp substrate (efflux)")
    if exc["half_life_h"] < 2:
        flags.append("short half-life; needs formulation or structural stabilisation")
    if tox["herg"]["band"] == "high":
        flags.append("hERG pharmacophore present")
    if tox["genotoxicity"]["band"] == "high":
        flags.append("genotoxic structural alerts")
    if tox["blocking_alerts"]:
        flags.append("blocking structural alert: " + ", ".join(tox["blocking_alerts"]))
    return flags
