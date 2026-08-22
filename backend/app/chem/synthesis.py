"""Synthetic accessibility, route depth and cost-of-goods heuristics."""

from __future__ import annotations

from app.chem.descriptors import fraction_sp3, heteroatom_fraction
from app.chem.groups import find_groups
from app.chem.smiles import Molecule, parse_smiles

# Rough per-step cost drivers (USD per gram at 10 g scale, fully loaded).
STEP_COST_USD = 180.0
CHIRAL_STEP_PREMIUM = 260.0
MACROCYCLISATION_PREMIUM = 900.0

# Reaction families the planner can assume are available; used to explain the route.
DISCONNECTIONS: tuple[tuple[str, str], ...] = (
    ("amide", "amide coupling (HATU/EDC) from acid + amine"),
    ("ester", "esterification / Steglich coupling"),
    ("sulfonamide", "sulfonyl chloride + amine"),
    ("aryl_halide", "Suzuki/Buchwald cross-coupling at the halide"),
    ("ether", "Williamson ether synthesis or Mitsunobu"),
    ("secondary_amine", "reductive amination"),
    ("tertiary_amine", "reductive amination / alkylation"),
    ("nitrile", "cyanation or nitrile retained from building block"),
    ("alkene", "Wittig/HWE olefination"),
    ("macrocycle", "macrocyclisation (RCM or macrolactamisation)"),
    ("epoxide", "epoxidation of the alkene precursor"),
)


def _mol(target: Molecule | str) -> Molecule:
    return target if isinstance(target, Molecule) else parse_smiles(target)


def sa_score(target: Molecule | str) -> float:
    """1 (trivial) - 10 (very hard) synthetic accessibility, Ertl/Schuffenhauer in spirit."""
    mol = _mol(target)
    heavy = mol.heavy_atom_count
    rings = mol.ring_count
    groups = find_groups(mol)
    score = 1.0
    score += min(3.0, heavy / 20.0)
    score += 0.35 * rings
    score += 0.8 * mol.stereocenters
    score += 2.0 * len(groups.get("macrocycle", []))
    score += 1.2 * len(groups.get("epoxide", [])) + 1.0 * len(groups.get("aziridine", []))
    score += 1.5 * heteroatom_fraction(mol)
    score += 0.8 * fraction_sp3(mol)
    fused = sum(1 for i, a in enumerate(mol.rings) for b in mol.rings[i + 1 :] if len(a & b) >= 2)
    score += 0.5 * fused
    return round(min(10.0, max(1.0, score)), 2)


def route_plan(target: Molecule | str) -> list[dict]:
    """Deterministic retrosynthetic sketch: which bonds a chemist would cut, in order."""
    mol = _mol(target)
    groups = find_groups(mol)
    steps: list[dict] = []
    for name, description in DISCONNECTIONS:
        matches = groups.get(name, [])
        if not matches:
            continue
        steps.append(
            {
                "step": len(steps) + 1,
                "disconnection": name,
                "reaction": description,
                "sites": [list(m) for m in matches[:3]],
                "count": len(matches),
            }
        )
    if not steps:
        steps.append(
            {
                "step": 1,
                "disconnection": "core",
                "reaction": "single-step functionalisation of a commercial core",
                "sites": [],
                "count": 1,
            }
        )
    return steps


def synthesis_report(target: Molecule | str) -> dict:
    """SA score, step estimate, cost of goods and building-block availability proxy."""
    mol = _mol(target)
    sa = sa_score(mol)
    steps = route_plan(mol)
    chiral = mol.stereocenters
    macro = len(find_groups(mol).get("macrocycle", []))
    step_count = max(1, min(14, len(steps) + int(mol.heavy_atom_count / 14) + chiral))
    yield_per_step = 0.72 if sa < 5 else 0.6 if sa < 7 else 0.45
    overall_yield = round(yield_per_step**step_count, 4)
    cost = STEP_COST_USD * step_count + CHIRAL_STEP_PREMIUM * chiral + MACROCYCLISATION_PREMIUM * macro
    cost_per_gram = round(cost / max(overall_yield, 0.01) / 10.0, 2)
    availability = round(max(0.05, min(0.98, 1.05 - sa / 10.0 - 0.08 * chiral)), 3)
    return {
        "sa_score": sa,
        "estimated_steps": step_count,
        "overall_yield": overall_yield,
        "cost_per_gram_usd": cost_per_gram,
        "cost_10g_batch_usd": round(cost_per_gram * 10, 2),
        "building_block_availability": availability,
        "route": steps,
        "scale_up_risk": "high" if sa >= 7 or macro else "moderate" if sa >= 5 else "low",
        "method": "fragment/complexity heuristic (no reaction database)",
    }
