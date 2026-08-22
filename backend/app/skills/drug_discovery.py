"""skill.drug_discovery — developability, immunogenicity and the affinity trade-off.

A design that binds beautifully but cannot be made is not a candidate. This skill scores
developability, flags immunogenicity risk, and makes the trade-off explicit: it reports where a
candidate sits on the affinity/developability Pareto front and what it would cost to accept it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.skills.base import Metric, SkillSpec, collect, register
from app.toolkit import developability as dev
from app.toolkit import sequence as seqlib

VERSION = "1.0.0"

CITATIONS = [
    "Jain et al. 2017, Biophysical properties of the clinical-stage antibody landscape "
    "(PNAS 114:944) — developability flag thresholds from clinical-stage molecules",
    "Raybould et al. 2019, Five computational developability guidelines for therapeutic antibody "
    "profiling (PNAS 116:4025)",
    "Hon et al. 2021, SoluProt: prediction of soluble protein expression in Escherichia coli "
    "(Bioinformatics 37:23) — sequence-only solubility prediction reaches ~58.5% accuracy, AUC 0.62",
    "Sormanni, Aprile & Vendruscolo 2015, The CamSol method of rational design of protein mutants "
    "with enhanced solubility (J Mol Biol 427:478)",
]

# Weights of the composite developability index. Deliberately flat: published guideline sets treat
# their flags as roughly equally disqualifying rather than finely weighted.
WEIGHTS = {
    "solubility": 0.30,
    "aggregation": 0.25,
    "immunogenicity": 0.20,
    "stability": 0.15,
    "liability": 0.10,
}
# Sequence-only solubility prediction is weak (AUC ~0.62), so the index carries a wide error bar.
DEVELOPABILITY_SD = 0.18


class Candidate(BaseModel):
    label: str
    sequence: str
    mutations: list[dict] = Field(default_factory=list)
    affinity_score: float = Field(
        default=0.0, description="higher is better; e.g. -binding_score from skill.physics"
    )
    affinity_sd: float = Field(default=0.0, ge=0.0)


class DrugDiscoveryInput(BaseModel):
    candidates: list[Candidate]
    working_ph: float = Field(default=7.4, ge=2.0, le=12.0)
    max_immunogenicity: float = Field(default=6.0, description="MHC-II-like cores per 100 aa")


class Assessment(BaseModel):
    label: str
    developability_index: float
    developability_sd: float
    affinity_score: float
    passed_filters: bool
    failed_filters: list[str] = Field(default_factory=list)
    immunogenicity_flags: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    pareto_optimal: bool = False
    tradeoff: str = ""
    components: dict[str, float] = Field(default_factory=dict)


class DrugDiscoveryOutput(BaseModel):
    assessments: list[Assessment] = Field(default_factory=list)
    metrics: dict[str, dict] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def developability_index(profile: dict) -> tuple[float, dict[str, float]]:
    """0-1 composite where 1 is 'boringly easy to make'."""
    solubility = _clip((profile["solubility"]["value"] + 1.0) / 2.0)
    aggregation = _clip(1.0 - profile["aggregation"]["value"] / 0.4)
    immunogenicity = _clip(1.0 - profile["immunogenicity"]["value"] / 10.0)
    stability = _clip(1.0 - max(profile["ddg"]["value"], 0.0) / 4.0)
    motifs = profile["liabilities"]["motifs"]
    unpaired_cys = profile["liabilities"]["odd_cysteine_count"]
    liability = _clip(1.0 - (len(motifs) / 6.0) - (0.3 if unpaired_cys else 0.0))
    components = {
        "solubility": round(solubility, 4),
        "aggregation": round(aggregation, 4),
        "immunogenicity": round(immunogenicity, 4),
        "stability": round(stability, 4),
        "liability": round(liability, 4),
    }
    index = sum(WEIGHTS[k] * v for k, v in components.items())
    return round(index, 4), components


def run(payload: DrugDiscoveryInput) -> DrugDiscoveryOutput:
    rows: list[Assessment] = []
    for candidate in payload.candidates:
        seq = seqlib.clean_sequence(candidate.sequence)
        profile = dev.profile(seq, mutations=candidate.mutations)
        filters = dev.apply_filters(profile, {"working_ph": payload.working_ph,
                                              "max_immunogenicity": payload.max_immunogenicity})
        index, components = developability_index(profile)

        immuno: list[str] = []
        imm_value = profile["immunogenicity"]["value"]
        if imm_value > payload.max_immunogenicity:
            immuno.append(
                f"{imm_value} MHC-II-like cores/100aa exceeds the {payload.max_immunogenicity} guideline"
            )
        for core in profile["immunogenicity"]["detail"]["example_cores"][:3]:
            immuno.append(f"binding-core candidate {core}")

        risks = [f"failed {name}" for name in filters["failed"]]
        for motif, positions in profile["liabilities"]["motifs"].items():
            risks.append(f"{motif} at {positions[:4]}")

        rows.append(
            Assessment(
                label=candidate.label,
                developability_index=index,
                developability_sd=DEVELOPABILITY_SD,
                affinity_score=candidate.affinity_score,
                passed_filters=filters["passed"],
                failed_filters=filters["failed"],
                immunogenicity_flags=immuno,
                risk_flags=risks[:8],
                components=components,
            )
        )

    # Pareto front over (developability, affinity): a candidate is dominated only if another is
    # at least as good on both axes and strictly better on one.
    for row in rows:
        row.pareto_optimal = not any(
            other.label != row.label
            and other.developability_index >= row.developability_index
            and other.affinity_score >= row.affinity_score
            and (other.developability_index > row.developability_index
                 or other.affinity_score > row.affinity_score)
            for other in rows
        )
    best_dev = max((r.developability_index for r in rows), default=0.0)
    best_aff = max((r.affinity_score for r in rows), default=0.0)
    for row in rows:
        dev_gap = round(best_dev - row.developability_index, 4)
        aff_gap = round(best_aff - row.affinity_score, 4)
        if dev_gap <= 1e-9 and aff_gap <= 1e-9:
            row.tradeoff = "best on both axes: no trade-off to accept"
        elif row.pareto_optimal:
            row.tradeoff = (
                f"Pareto-optimal: buys {round(-aff_gap or 0.0, 4) if aff_gap < 0 else aff_gap} affinity "
                f"for {dev_gap} developability index"
            )
        else:
            row.tradeoff = (
                f"dominated: {dev_gap} developability and {aff_gap} affinity behind the front"
            )

    metrics = collect(
        [
            Metric("developability_index_best", best_dev, "index 0-1",
                   "weighted solubility/aggregation/immunogenicity/stability/liability composite",
                   "skill.drug_discovery", sd=DEVELOPABILITY_SD,
                   citations=(CITATIONS[0], CITATIONS[1], CITATIONS[2])),
            Metric("pareto_front_size", float(sum(1 for r in rows if r.pareto_optimal)), "count",
                   "non-dominated candidates on the affinity/developability plane",
                   "skill.drug_discovery"),
        ]
    )
    return DrugDiscoveryOutput(assessments=rows, metrics=metrics, citations=CITATIONS)


SKILL = register(
    SkillSpec(
        name="skill.drug_discovery",
        version=VERSION,
        summary=(
            "Developability index with immunogenicity flags and an explicit affinity-vs-"
            "developability Pareto trade-off per candidate."
        ),
        input_model=DrugDiscoveryInput,
        output_model=DrugDiscoveryOutput,
        runner=run,
        citations=CITATIONS,
    )
)
