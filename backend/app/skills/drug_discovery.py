"""skill.drug_discovery — developability, immunogenicity and the affinity trade-off.

A design that binds beautifully but cannot be made is not a candidate. This skill scores
developability, flags immunogenicity risk, and makes the trade-off explicit: it reports where a
candidate sits on the affinity/developability Pareto front and what it would cost to accept it.

Provenance (full records in :data:`EVIDENCE`):

* **the flag-based framing** is taken from the clinical-stage antibody literature: Jain et al. 2017
  (doi:10.1073/pnas.1616408114) measured 12 biophysical assays on 137 clinical-stage antibodies and
  Raybould et al. 2019 (doi:10.1073/pnas.1810576116) turned those distributions into five
  computational guidelines. Both are *antibody* populations; our inputs are arbitrary designed
  proteins, so their thresholds are used as framing, not as transferred cutoffs.
* **the composite index and its weights are uncalibrated project policy.** No paper publishes this
  composite. The weights are flat-by-design and never fitted, and the index is dimensionless: it
  ranks designs of the same protein and nothing else.
* **developability_sd = 0.18** is a policy ranking band, not a measured sd. It is chosen wide
  because the strongest input - sequence-only solubility - is itself weak: SoluProt reaches 58.5%
  accuracy and AUC 0.62 on its independent test set (doi:10.1093/bioinformatics/btaa1102).
* **immunogenicity** is an allele-agnostic MHC-II anchor-motif count per 100 aa with no calibration.
  A real epitope call needs an allele-aware predictor such as NetMHCIIpan-4.0
  (doi:10.1093/nar/gkaa379); the default limit of 6 cores/100 aa is a project screening policy.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.skills import evidence
from app.skills.base import Metric, SkillSpec, collect, register
from app.toolkit import developability as dev
from app.toolkit import sequence as seqlib

VERSION = "1.0.0"

EVIDENCE = evidence.register(
    "skill.drug_discovery",
    (
        evidence.Evidence(
            key="drug_discovery.developability_flags",
            claim="developability is scored as a set of roughly equally disqualifying flags",
            applicability="137 clinical-stage therapeutic antibodies profiled in 12 biophysical assays; "
                          "the five computational guidelines derived from those distributions",
            error="the source distributions are antibody-specific and the guidelines are pass/fail "
                  "percentile flags, not a scored index; transferring them to non-antibody designs has "
                  "no published validation",
            calibration=evidence.ANCHORED,
            source="Jain et al. 2017, Biophysical properties of the clinical-stage antibody landscape "
                   "(PNAS 114:944); Raybould et al. 2019, Five computational developability guidelines "
                   "for therapeutic antibody profiling (PNAS 116:4025, doi:10.1073/pnas.1810576116)",
            doi="10.1073/pnas.1616408114",
            reference_value="137 clinical-stage antibodies, 12 assays",
        ),
        evidence.Evidence(
            key="drug_discovery.composite_weights",
            claim="the 0-1 developability index is a weighted mean of five sub-scores",
            applicability="ranking designs of the same protein within one campaign",
            error="uncalibrated: the weights were chosen by hand, never fitted against expression or "
                  "manufacturability outcomes, and the index has no physical unit. It must not be read "
                  "as a probability of success",
            calibration=evidence.PROXY,
        ),
        evidence.Evidence(
            key="drug_discovery.developability_sd",
            claim="the composite index is published with sd 0.18 and the ranking layer respects it",
            applicability="E. coli soluble-expression prediction from sequence, as an upper bound on how "
                          "good the composite's strongest input can be",
            error="SoluProt reaches 58.5% accuracy and AUC 0.62 on its independent test set, so a "
                  "sequence-only solubility term is barely better than a coin flip; 0.18 on a 0-1 scale "
                  "is a deliberately wide policy band and not a measured standard deviation of this "
                  "composite (no such measurement exists)",
            calibration=evidence.POLICY,
            source="Hon et al. 2021, SoluProt: prediction of soluble protein expression in Escherichia "
                   "coli (Bioinformatics 37:23)",
            doi="10.1093/bioinformatics/btaa1102",
            reference_value="58.5% accuracy, AUC 0.62 on the independent test set",
        ),
        evidence.Evidence(
            key="drug_discovery.solubility_ingredients",
            claim="the solubility sub-score combines charge density, hydropathy and aggregation load",
            applicability="the ingredients follow a published solubility-design method; the published "
                          "method predicts *relative* solubility changes on mutation",
            error="uncalibrated: our weights are not CamSol's fitted coefficients, so the sub-score "
                  "is in arbitrary units, cannot be compared with a CamSol score and predicts no "
                  "mg/mL",
            calibration=evidence.PROXY,
            source="Sormanni, Aprile & Vendruscolo 2015, The CamSol method of rational design of protein "
                   "mutants with enhanced solubility (J Mol Biol 427:478)",
            doi="10.1016/j.jmb.2014.09.026",
        ),
        evidence.Evidence(
            key="drug_discovery.immunogenicity_limit",
            claim="candidates are flagged above 6 MHC-II-like binding cores per 100 aa",
            applicability="internal screening of designs of the same protein",
            error="uncalibrated in both directions: the count is allele-agnostic with no binding "
                  "affinity or HLA frequency weighting, and the 6/100aa limit is a project policy. An "
                  "allele-aware predictor (NetMHCIIpan-4.0, trained on binding affinity and eluted "
                  "ligand data) is required before any (non-)immunogenicity claim",
            calibration=evidence.POLICY,
            source="Reynisson et al. 2020, NetMHCpan-4.1 and NetMHCIIpan-4.0 (Nucleic Acids Res 48:W449)",
            doi="10.1093/nar/gkaa379",
        ),
    ),
)

CITATIONS = evidence.citations("skill.drug_discovery")


def cite(*keys: str) -> tuple[str, ...]:
    return tuple(evidence.record("skill.drug_discovery", key).citation() for key in keys)


# Weights of the composite developability index. Deliberately flat and *never fitted*: published
# guideline sets treat their flags as roughly equally disqualifying rather than finely weighted.
# See EVIDENCE["drug_discovery.composite_weights"] - uncalibrated proxy.
WEIGHTS = {
    "solubility": 0.30,
    "aggregation": 0.25,
    "immunogenicity": 0.20,
    "stability": 0.15,
    "liability": 0.10,
}
# Policy ranking band, not a measured sd (drug_discovery.developability_sd). Kept wide because the
# composite's strongest input is sequence-only solubility, whose published AUC is 0.62.
DEVELOPABILITY_SD = 0.18
# Literature reference values pinned by tests/test_evidence.py.
SOLUPROT_ACCURACY = 0.585
SOLUPROT_AUC = 0.62
JAIN_CLINICAL_ANTIBODIES = 137


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
    """0-1 composite where 1 is 'boringly easy to make'. Uncalibrated by construction.

    The five sub-scores follow the flag families used by the clinical-stage antibody developability
    literature (Jain et al. 2017, doi:10.1073/pnas.1616408114; Raybould et al. 2019,
    doi:10.1073/pnas.1810576116), but the normalisation constants and weights here were chosen by
    hand and never fitted to expression or manufacturability outcomes. The number ranks designs of
    the same protein; it is not a probability of developability and has no unit.
    """
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
                   citations=cite("drug_discovery.developability_flags",
                                  "drug_discovery.composite_weights",
                                  "drug_discovery.developability_sd"),
                   notes="unitless, uncalibrated composite with hand-chosen weights; the sd is a policy "
                         "band set by the AUC 0.62 ceiling of sequence-only solubility prediction"),
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
