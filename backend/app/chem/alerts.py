"""Structural alerts, assay-interference (PAINS-style) and toxicology heuristics.

Each alert carries a severity, the atoms that triggered it and a mitigation hint, so a
medicinal-chemistry agent can act on it instead of only reading a number. Severities:
``block`` (kills a candidate), ``high``, ``medium``, ``low``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.chem.descriptors import clogp, hbd_count, tpsa
from app.chem.groups import find_groups
from app.chem.smiles import Molecule, parse_smiles

SEVERITY_WEIGHT = {"block": 1.0, "high": 0.6, "medium": 0.3, "low": 0.12}


@dataclass(frozen=True)
class Alert:
    code: str
    label: str
    severity: str
    category: str
    atoms: tuple[int, ...]
    mitigation: str

    def as_dict(self) -> dict:
        data = asdict(self)
        data["atoms"] = list(self.atoms)
        return data


# (group name, code, label, severity, category, mitigation)
GROUP_ALERTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("acyl_halide", "REACT-ACYL-HALIDE", "acyl halide", "block", "reactive",
     "replace with amide/ester or install after the reactive step"),
    ("isocyanate", "REACT-ISOCYANATE", "isocyanate", "block", "reactive",
     "convert to carbamate/urea"),
    ("azide", "REACT-AZIDE", "organic azide", "block", "reactive",
     "reduce to amine or replace with triazole"),
    ("epoxide", "REACT-EPOXIDE", "epoxide", "high", "reactive",
     "open the ring or replace with an oxetane"),
    ("aziridine", "REACT-AZIRIDINE", "aziridine", "high", "reactive",
     "replace with azetidine/pyrrolidine"),
    ("michael_acceptor", "REACT-MICHAEL", "Michael acceptor", "medium", "covalent",
     "keep only if a covalent warhead is intended; otherwise saturate the alkene"),
    ("aldehyde", "REACT-ALDEHYDE", "aldehyde", "high", "reactive",
     "oxidise to acid, reduce to alcohol or mask as acetal"),
    ("hydrazine", "TOX-HYDRAZINE", "hydrazine/hydrazide", "high", "genotoxic",
     "swap for amide or aminopyrazole"),
    ("azo", "TOX-AZO", "azo linkage", "high", "genotoxic",
     "replace with amide or heteroaryl link"),
    ("nitroaromatic", "TOX-NITROAROMATIC", "nitroaromatic", "high", "genotoxic",
     "replace nitro with cyano/sulfonyl (Ames liability)"),
    ("quinone", "PAINS-QUINONE", "quinone", "high", "assay-interference",
     "remove the redox-cycling core"),
    ("catechol", "PAINS-CATECHOL", "catechol", "medium", "assay-interference",
     "cap one hydroxyl or move to a mono-phenol"),
    ("thiol", "PAINS-THIOL", "free thiol", "medium", "assay-interference",
     "cap as thioether or replace with alcohol"),
    ("disulfide", "PAINS-DISULFIDE", "disulfide", "high", "assay-interference",
     "replace with a stable linker"),
    ("thiocarbonyl", "TOX-THIOCARBONYL", "thiocarbonyl", "medium", "metabolic",
     "revert to the carbonyl analogue"),
    ("aniline", "TOX-ANILINE", "aniline", "medium", "reactive-metabolite",
     "block the para position or add ortho substitution"),
    ("nitro", "TOX-NITRO", "nitro group", "medium", "genotoxic",
     "replace with nitrile, sulfone or halogen"),
    ("quaternary_ammonium", "PK-PERMANENT-CHARGE", "quaternary ammonium", "medium", "exposure",
     "neutralise the charge for oral exposure"),
    ("alkyl_halide", "REACT-ALKYL-HALIDE", "alkyl halide", "medium", "genotoxic",
     "move the halogen to an aromatic position"),
    ("macrocycle", "SYN-MACROCYCLE", "macrocycle", "low", "synthesis",
     "expect a macrocyclisation step and lower yield"),
)


def find_alerts(target: Molecule | str) -> list[Alert]:
    mol = target if isinstance(target, Molecule) else parse_smiles(target)
    groups = find_groups(mol)
    alerts: list[Alert] = []
    for name, code, label, severity, category, mitigation in GROUP_ALERTS:
        for match in groups.get(name, []):
            alerts.append(
                Alert(
                    code=code,
                    label=label,
                    severity=severity,
                    category=category,
                    atoms=tuple(match),
                    mitigation=mitigation,
                )
            )
    alerts.extend(_property_alerts(mol, groups))
    alerts.sort(key=lambda a: (-SEVERITY_WEIGHT[a.severity], a.code, a.atoms))
    return alerts


def _property_alerts(mol: Molecule, groups: dict) -> list[Alert]:
    out: list[Alert] = []
    logp = clogp(mol)
    mw = mol.molecular_weight
    ps = tpsa(mol)
    aromatic_rings = len(mol.aromatic_rings())
    if logp > 5.0 and mw > 400:
        out.append(
            Alert(
                code="PK-GREASY",
                label=f"high lipophilicity (cLogP {logp}) with MW {mw:.0f}",
                severity="medium",
                category="exposure",
                atoms=(),
                mitigation="add polarity (heteroaryl swap, small polar group) to cut cLogP below 4",
            )
        )
    if ps > 140 and hbd_count(mol) > 5:
        out.append(
            Alert(
                code="PK-LOW-PERMEABILITY",
                label=f"TPSA {ps} with {hbd_count(mol)} donors",
                severity="medium",
                category="exposure",
                atoms=(),
                mitigation="cap donors (N-methylation, intramolecular H-bond) to raise permeability",
            )
        )
    if aromatic_rings >= 4:
        out.append(
            Alert(
                code="DEV-FLATLAND",
                label=f"{aromatic_rings} aromatic rings",
                severity="low",
                category="developability",
                atoms=(),
                mitigation="replace one aryl with a saturated ring to improve solubility",
            )
        )
    if groups.get("basic_center") and logp > 3.7 and aromatic_rings >= 2 and mw > 250:
        out.append(
            Alert(
                code="TOX-HERG",
                label="basic amine + lipophilic aromatic core (hERG pharmacophore)",
                severity="high",
                category="cardiac",
                atoms=tuple(i for match in groups["basic_center"] for i in match),
                mitigation="lower basicity (morpholine, fluorination adjacent to N) or cut cLogP",
            )
        )
    return out


def herg_risk(target: Molecule | str) -> dict:
    """Cardiac hERG liability heuristic (basic centre + lipophilicity + aromatic bulk)."""
    mol = target if isinstance(target, Molecule) else parse_smiles(target)
    groups = find_groups(mol)
    logp = clogp(mol)
    basic = len(groups.get("basic_center", []))
    aromatic_rings = len(mol.aromatic_rings())
    score = 0.0
    drivers: list[str] = []
    if basic:
        score += 0.35
        drivers.append("basic amine")
    if logp > 3.0:
        score += min(0.3, (logp - 3.0) * 0.15)
        drivers.append(f"cLogP {logp}")
    if aromatic_rings >= 2:
        score += 0.15
        drivers.append(f"{aromatic_rings} aromatic rings")
    if mol.molecular_weight > 400:
        score += 0.1
        drivers.append("MW > 400")
    if groups.get("acidic_center"):
        score -= 0.2
        drivers.append("acidic group (protective)")
    score = round(min(1.0, max(0.02, score)), 3)
    band = "high" if score >= 0.55 else "moderate" if score >= 0.3 else "low"
    return {"risk": score, "band": band, "drivers": drivers, "method": "pharmacophore heuristic"}


def cyp_profile(target: Molecule | str) -> dict:
    """Per-isoform CYP inhibition/substrate likelihood heuristics."""
    mol = target if isinstance(target, Molecule) else parse_smiles(target)
    groups = find_groups(mol)
    logp = clogp(mol)
    mw = mol.molecular_weight
    aromatic_rings = len(mol.aromatic_rings())
    basic = len(groups.get("basic_center", []))
    imidazole = sum(
        1
        for ring in mol.aromatic_rings()
        if sum(1 for i in ring if mol.atoms[i].element == "N") >= 2 and len(ring) == 5
    )

    def clamp(x: float) -> float:
        return round(min(0.95, max(0.03, x)), 3)

    return {
        "CYP3A4_inhibition": clamp(0.12 + 0.09 * max(0.0, logp - 2.0) + 0.0006 * max(0.0, mw - 350)),
        "CYP2D6_inhibition": clamp(0.08 + 0.18 * basic + 0.05 * max(0.0, logp - 3.0)),
        "CYP2C9_inhibition": clamp(0.08 + 0.12 * len(groups.get("acidic_center", [])) + 0.05 * logp),
        "CYP1A2_inhibition": clamp(0.06 + 0.09 * aromatic_rings),
        "CYP_mechanism_based_risk": clamp(0.05 + 0.25 * imidazole + 0.15 * len(groups.get("aniline", []))),
        "method": "physchem/pharmacophore heuristic",
    }


def genotoxicity_flags(target: Molecule | str) -> dict:
    """Structural-alert view of Ames/genotox risk (Kazius 2005 alert families)."""
    alerts = find_alerts(target)
    genotox = [a for a in alerts if a.category in ("genotoxic", "reactive", "reactive-metabolite")]
    score = min(1.0, sum(SEVERITY_WEIGHT[a.severity] for a in genotox) * 0.6)
    return {
        "ames_risk": round(score, 3)
        if genotox
        else 0.05,
        "band": "high" if score >= 0.6 else "moderate" if score >= 0.25 else "low",
        "alerts": [a.code for a in genotox],
    }


def alert_report(target: Molecule | str) -> dict:
    """Aggregate liability report used by ADMET scoring and gate decisions."""
    mol = target if isinstance(target, Molecule) else parse_smiles(target)
    alerts = find_alerts(mol)
    burden = sum(SEVERITY_WEIGHT[a.severity] for a in alerts)
    return {
        "count": len(alerts),
        "blocking": [a.code for a in alerts if a.severity == "block"],
        "burden": round(burden, 3),
        "penalty": round(min(1.0, burden / 3.0), 3),
        "alerts": [a.as_dict() for a in alerts],
        "herg": herg_risk(mol),
        "cyp": cyp_profile(mol),
        "genotoxicity": genotoxicity_flags(mol),
    }
