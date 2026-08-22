"""Explicitly-labelled offline fallback for the agent layer.

This is NOT Devin and never pretends to be. It only runs when `ALLOW_LOCAL_SIMULATION=true` **and**
no Devin credentials are configured, so the demo still works on a laptop with no API key. Every
cycle, agent run and commit produced this way records `provider="local-simulation"`, which the API
and UI surface prominently.

The proposals are deterministic, evidence-driven heuristics over the same toolkit the real agents
are shown — not a language model, and not a stand-in for one.
"""

from __future__ import annotations

from app.toolkit import sequence as seqlib
from app.toolkit.constants import HYDROPATHY

PROVIDER = "local-simulation"

# Substitution playbooks used by the heuristic proposer.
SOLUBILIZING = ["K", "E", "S", "Q", "D", "T", "N", "R"]
CORE_PACKING = {"A": "V", "V": "I", "G": "A", "S": "A", "T": "V", "I": "L", "L": "I", "M": "L"}
INTERFACE_POLAR = ["R", "K", "E", "D", "Y", "W", "N"]


def _exposed_hydrophobic_positions(seq: str, exposure: list[float] | None, limit: int) -> list[int]:
    scored: list[tuple[float, int]] = []
    for i, aa in enumerate(seq):
        hyd = HYDROPATHY.get(aa, 0.0)
        if hyd <= 1.0:
            continue
        exp = exposure[i] if exposure and i < len(exposure) else 0.6
        scored.append((hyd * (0.3 + exp), i + 1))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [pos for _, pos in scored[:limit]]


def _buried_positions(seq: str, exposure: list[float] | None, limit: int) -> list[int]:
    if not exposure:
        return [i + 1 for i, aa in enumerate(seq) if aa in CORE_PACKING][:limit]
    scored = sorted(range(min(len(seq), len(exposure))), key=lambda i: exposure[i])
    return [i + 1 for i in scored if seq[i] in CORE_PACKING][:limit]


def _mutation(seq: str, pos: int, new_aa: str) -> str | None:
    if not (1 <= pos <= len(seq)):
        return None
    wt = seq[pos - 1]
    if wt == new_aa:
        return None
    return f"{wt}{pos}{new_aa}"


def _dedupe(candidates: list[dict], exclusions: dict[str, str]) -> list[dict]:
    seen: set[str] = set()
    excluded = set(exclusions)
    out = []
    for cand in candidates:
        key = ",".join(sorted(cand["mutations"]))
        if not key or key in seen or key in excluded:
            continue
        seen.add(key)
        out.append(cand)
    return out


def simulate_plan(brief: str, evidence: dict, shortlist_size: int) -> dict:
    liabilities = evidence.get("developability", {})
    aggregation = (liabilities.get("aggregation") or {}).get("value", 0.0)
    solubility = (liabilities.get("solubility") or {}).get("value", 0.0)
    has_target = bool(evidence.get("docking"))
    roles = ["sequence", "structure"]
    if has_target:
        roles.append("docking")
    roles.append("literature")
    roles.append("ranking")

    agents = []
    for role in roles:
        if role == "sequence":
            task = (
                "Scan solvent-exposed hydrophobic positions and propose charge- or polar-introducing "
                f"substitutions (current aggregation propensity {aggregation}, solubility proxy "
                f"{solubility})."
            )
        elif role == "structure":
            task = (
                "Improve core packing at buried positions without changing residue volume by more "
                "than one shell; avoid introducing cavities."
            )
        elif role == "docking":
            task = "Improve interface complementarity at the coarse-docking interface residues."
        elif role == "literature":
            task = "Ground the cycle in published stabilizing/affinity substitutions for this family."
        else:
            task = "Triage the pooled candidates into an orthogonal wet-lab shortlist."
        agents.append({"role": role, "task": task, "acu_limit": 3})

    return {
        "strategy": (
            "Deterministic heuristic plan (local simulation): attack the dominant developability "
            "liability first, then packing, then interface, and triage for orthogonality. "
            f"Brief: {brief[:200]}"
        ),
        "hypotheses": [
            "Exposed apolar patches dominate aggregation risk and can be neutralized with charge.",
            "Conservative core packing substitutions improve the stability proxy without new cavities.",
        ],
        "agents": agents,
        "success_criteria": [
            "At least three candidates pass all hard developability filters.",
            "Best candidate improves the composite score over the parent.",
        ],
        "open_questions": ["Which liability limits expression in vitro for this scaffold?"],
        "shortlist_size": shortlist_size,
    }


def simulate_sequence_agent(seq: str, evidence: dict, exclusions: dict[str, str]) -> dict:
    exposure = (evidence.get("structure") or {}).get("relative_exposure")
    positions = _exposed_hydrophobic_positions(seq, exposure, 8)
    candidates = []
    for idx, pos in enumerate(positions):
        new_aa = SOLUBILIZING[idx % len(SOLUBILIZING)]
        token = _mutation(seq, pos, new_aa)
        if token is None:
            continue
        candidates.append(
            {
                "label": f"seq-{token}",
                "mutations": [token],
                "rationale": (
                    f"Position {pos} is an exposed apolar residue ({seq[pos - 1]}, hydropathy "
                    f"{HYDROPATHY.get(seq[pos - 1], 0.0)}); substituting {new_aa} lowers the "
                    "hydrophobic-patch score and raises net charge, reducing aggregation risk. "
                    "Trade-off: possible loss of local packing."
                ),
                "expected_effects": {"aggregation": "reduce", "solubility": "improve"},
                "citations": ["Kyte & Doolittle 1982, J Mol Biol 157:105", "Tartaglia & Vendruscolo 2008"],
                "confidence": 0.45,
            }
        )
    # one combination candidate
    if len(candidates) >= 2:
        combo = [candidates[0]["mutations"][0], candidates[1]["mutations"][0]]
        candidates.append(
            {
                "label": f"seq-{'+'.join(combo)}",
                "mutations": combo,
                "rationale": "Additive test of the two strongest exposed-apolar substitutions.",
                "expected_effects": {"aggregation": "reduce"},
                "citations": ["additivity assumption for distant surface substitutions"],
                "confidence": 0.35,
            }
        )
    return {
        "candidates": _dedupe(candidates, exclusions)[:6],
        "analysis": "Heuristic exposed-hydrophobic scan (local simulation, not a Devin agent).",
        "hotspots": positions[:5],
        "open_questions": [],
    }


def simulate_structure_agent(seq: str, evidence: dict, exclusions: dict[str, str]) -> dict:
    exposure = (evidence.get("structure") or {}).get("relative_exposure")
    positions = _buried_positions(seq, exposure, 6)
    source = (evidence.get("structure") or {}).get("source", "unknown")
    candidates = []
    for pos in positions:
        wt = seq[pos - 1]
        token = _mutation(seq, pos, CORE_PACKING.get(wt, "V"))
        if token is None:
            continue
        candidates.append(
            {
                "label": f"str-{token}",
                "mutations": [token],
                "rationale": (
                    f"Position {pos} is buried; the conservative volume-increasing substitution "
                    f"{token} improves core packing without introducing polarity. Structural "
                    f"evidence source: {source} — treat coarse models as low-confidence geometry."
                ),
                "expected_effects": {"ddg_proxy": "improve"},
                "citations": ["Zamyatnin 1972 residue volumes", "BLOSUM62 (Henikoff 1992)"],
                "confidence": 0.4 if source.startswith("model") else 0.6,
            }
        )
    return {
        "candidates": _dedupe(candidates, exclusions)[:5],
        "analysis": f"Heuristic burial-based packing scan over a {source} structure (local simulation).",
        "hotspots": positions[:5],
        "open_questions": [],
    }


def simulate_docking_agent(seq: str, evidence: dict, exclusions: dict[str, str]) -> dict:
    docking = evidence.get("docking") or {}
    interface = list(docking.get("interface_residues") or [])[:6]
    if not interface:
        interface = _exposed_hydrophobic_positions(seq, None, 4)
    candidates = []
    for idx, pos in enumerate(interface):
        token = _mutation(seq, pos, INTERFACE_POLAR[idx % len(INTERFACE_POLAR)])
        if token is None:
            continue
        candidates.append(
            {
                "label": f"dock-{token}",
                "mutations": [token],
                "rationale": (
                    f"Interface residue {pos} contributes to the coarse binding proxy "
                    f"({docking.get('binding_score')} ± {docking.get('uncertainty')}); "
                    f"{token} adds a complementary charge/aromatic contact across the interface. "
                    "Coarse CA-level proxy: use for rank ordering only."
                ),
                "expected_effects": {"binding_score": "improve"},
                "citations": ["Debye-screened electrostatics; soft LJ contact model (see CITATIONS.md)"],
                "confidence": 0.3,
            }
        )
    return {
        "candidates": _dedupe(candidates, exclusions)[:5],
        "analysis": "Heuristic interface substitution scan (local simulation).",
        "hotspots": interface,
        "open_questions": ["Is the coarse docking pose consistent with the known epitope?"],
    }


def simulate_literature_agent(seq: str, evidence: dict) -> dict:
    descriptors = evidence.get("descriptors", {})
    return {
        "findings": [
            {
                "claim": "Surface charge engineering reduces aggregation without destabilizing the fold.",
                "citation": "Lawrence et al. 2007, J Am Chem Soc 129:10110 (supercharging)",
                "implication": (
                    f"With GRAVY {descriptors.get('gravy')} and pI {descriptors.get('isoelectric_point')}, "
                    "charge substitutions on exposed apolar patches are the cheapest first move."
                ),
            },
            {
                "claim": "Consensus/ancestral substitutions are enriched in stabilizing mutations.",
                "citation": "Steipe et al. 1994, J Mol Biol 240:188",
                "implication": "Prefer substitutions matching the family consensus at variable positions.",
            },
            {
                "claim": "Instability index above 40 predicts a short intracellular half-life.",
                "citation": "Guruprasad et al. 1990, Protein Eng 4:155",
                "implication": (
                    f"Parent instability index is {descriptors.get('instability_index')}; keep it below 40."
                ),
            },
        ],
        "recommended_mutations": [],
        "analysis": (
            "Offline literature stub (local simulation). Real Devin literature agents search live "
            "sources; this fallback only restates well-known published heuristics with citations."
        ),
        "open_questions": ["Are there family-specific stabilizing substitutions in recent literature?"],
    }


def simulate_ranking_agent(candidates: list[dict], shortlist_size: int) -> dict:
    ordered = sorted(
        candidates,
        key=lambda c: (
            not c.get("passed_filters", True),
            -float(c.get("composite_score") or 0.0),
        ),
    )
    return {
        "ordering": [
            {
                "label": c.get("label", "?"),
                "reason": (
                    f"composite {c.get('composite_score')} with confidence {c.get('confidence')}; "
                    f"{'passes' if c.get('passed_filters') else 'fails'} hard developability filters"
                ),
                "risk": ", ".join(c.get("failed_filters", [])) or "no hard-filter risk flagged",
            }
            for c in ordered
        ],
        "exclusions": [
            {"target": c.get("label", "?"), "reason": ", ".join(c.get("failed_filters", []))}
            for c in ordered
            if not c.get("passed_filters", True)
        ],
        "shortlist_size": shortlist_size,
        "analysis": "Deterministic score ordering (local simulation).",
        "open_questions": [],
    }


def simulate(
    role: str,
    *,
    sequence: str,
    evidence: dict,
    exclusions: dict[str, str],
    candidates: list[dict] | None = None,
    shortlist_size: int = 5,
) -> dict:
    if role == "ranking":
        return simulate_ranking_agent(candidates or [], shortlist_size)
    sequence = seqlib.clean_sequence(sequence)
    if role == "sequence":
        return simulate_sequence_agent(sequence, evidence, exclusions)
    if role == "structure":
        return simulate_structure_agent(sequence, evidence, exclusions)
    if role == "docking":
        return simulate_docking_agent(sequence, evidence, exclusions)
    if role == "literature":
        return simulate_literature_agent(sequence, evidence)
    raise ValueError(f"unknown role {role}")
