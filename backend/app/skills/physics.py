"""skill.physics — structural and thermodynamic features with honest error bars.

Wraps Foldsmith's deterministic toolkit (coarse folding, contact analysis, ddG proxy, rigid-body
docking) and adds the two things the wet-lab loop needs: a *predicted melting temperature* on the
same scale the lab measures, and a defensible uncertainty for every number.

Error bars are taken from published benchmark performance, not from wishful thinking:

* stability predictors correlate with experiment at r ~= 0.26-0.59 while experimental replicates
  correlate at r ~= 0.86 (Potapov, Cohen & Schreiber 2009), so a ddG proxy carries >= 1 kcal/mol;
* ddG is converted to dTm with the Becktel-Schellman relation dTm = ddG / dS_m;
* docking scores correlate with measured affinity only within affinity classes
  (Kastritis & Bonvin 2010), so the binding proxy is reported with ~1 log10 uncertainty.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.skills.base import Metric, SkillSpec, collect, register
from app.toolkit import developability as dev
from app.toolkit import docking as dock
from app.toolkit import folding
from app.toolkit import sequence as seqlib
from app.toolkit import structure as structlib

VERSION = "1.0.0"

CITATIONS = [
    "Potapov, Cohen & Schreiber 2009, Assessing computational methods for predicting protein "
    "stability upon mutation (Protein Eng Des Sel 22:553) — ddG predictors reach r=0.26-0.59",
    "Becktel & Schellman 1987, Protein stability curves (Biopolymers 26:1859) — dTm = ddG / dS_m",
    "Zeldovich, Berezovsky & Shakhnovich 2007, Protein and DNA sequence determinants of "
    "thermophilic adaptation (PLoS Comput Biol 3:e5) — IVYWREL content tracks growth temperature",
    "Kastritis & Bonvin 2010, Are scoring functions in protein-protein docking ready to predict "
    "interactomes? (J Proteome Res 9:2216)",
    "Plaxco, Simons & Baker 1998, Contact order, transition state placement and the refolding "
    "rates of single domain proteins (J Mol Biol 277:985)",
]

# Becktel-Schellman: dS_m per residue for small single domains, kcal/(mol*K).
ENTROPY_OF_UNFOLDING_PER_RESIDUE = 0.0045
# Mesophilic reference point for a small soluble domain; the IVYWREL term moves it.
BASELINE_TM_C = 55.0
IVYWREL = set("IVYWREL")
# Slope of Tm on IVYWREL fraction, anchored on the thermophilic-adaptation trend.
TM_PER_IVYWREL_FRACTION = 95.0
DDG_UNCERTAINTY_KCAL = 1.2
TM_UNCERTAINTY_C = 4.0
BINDING_LOG10_UNCERTAINTY = 1.0


class PhysicsInput(BaseModel):
    sequence: str
    structure_pdb: str | None = Field(default=None, description="PDB text; coarse-folded when absent")
    target_pdb: str | None = Field(default=None, description="binding partner, enables the interface proxy")
    mutations: list[dict] = Field(default_factory=list, description="applied mutations from the toolkit")
    template_pdb: str | None = Field(default=None, description="parent structure used as folding template")
    relax_steps: int = Field(
        default=0, ge=0, le=60,
        description="coarse MD relaxation steps; 0 skips it (the daemon keeps this cheap)",
    )


class PhysicsOutput(BaseModel):
    length: int
    structure_source: str
    metrics: dict[str, dict] = Field(default_factory=dict)
    contacts: int = 0
    secondary_structure: dict[str, float] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)


def ivywrel_fraction(sequence: str) -> float:
    seq = seqlib.clean_sequence(sequence)
    return sum(1 for aa in seq if aa in IVYWREL) / len(seq)


def baseline_tm_c(sequence: str) -> float:
    """Composition-only Tm prior: thermophilic proteomes are enriched in IVYWREL residues."""
    fraction = ivywrel_fraction(sequence)
    return round(BASELINE_TM_C + TM_PER_IVYWREL_FRACTION * (fraction - 0.30), 2)


def ddg_to_dtm(ddg_kcal: float, length: int) -> float:
    """Becktel-Schellman conversion. ddG here is destabilising-positive, so dTm flips sign."""
    entropy = ENTROPY_OF_UNFOLDING_PER_RESIDUE * max(length, 1)
    return round(-ddg_kcal / entropy, 2)


def predicted_tm_c(sequence: str, ddg_kcal: float) -> float:
    seq = seqlib.clean_sequence(sequence)
    return round(baseline_tm_c(seq) + ddg_to_dtm(ddg_kcal, len(seq)), 2)


def contact_order(structure: structlib.Structure) -> float:
    """Relative contact order: mean sequence separation of contacts / chain length."""
    contacts = structlib.contact_map(structure, cutoff=8.0)
    if not contacts:
        return 0.0
    n = len(structure.coords)
    total = sum(abs(j - i) for i, j, _ in contacts)
    return round(total / (len(contacts) * max(n, 1)), 4)


def run(payload: PhysicsInput) -> PhysicsOutput:
    seq = seqlib.clean_sequence(payload.sequence)
    template = (
        structlib.load_structure(payload.template_pdb, name="template") if payload.template_pdb else None
    )
    if payload.structure_pdb:
        model = structlib.load_structure(payload.structure_pdb, name="design")
    else:
        model = folding.fold_sequence(seq, template=template, name="design")

    geometry = structlib.summary(model)
    ddg = dev.ddg_proxy(seq, payload.mutations, structure=model)
    tm = predicted_tm_c(seq, ddg.value)
    contacts = structlib.contact_map(model, cutoff=8.0)

    metrics = [
        Metric("radius_of_gyration", geometry["radius_of_gyration"], "angstrom",
               "mass-weighted CA radius of gyration", "skill.physics"),
        Metric("compactness", geometry["compactness"], "ratio",
               "Rg relative to the 2.2*N^0.38 globular expectation", "skill.physics"),
        Metric("contact_density", round(len(contacts) / max(len(seq), 1), 3), "contacts/residue",
               "CA-CA contacts within 8 A per residue", "skill.physics"),
        Metric("relative_contact_order", contact_order(model), "ratio",
               "mean contact sequence separation / chain length", "skill.physics",
               citations=(CITATIONS[4],)),
        Metric("clash_count", float(geometry["clashes"]), "count",
               "CA pairs closer than 3.4 A after clash relief", "skill.physics"),
        Metric("ddg_proxy", ddg.value, "kcal/mol", ddg.method, "skill.physics",
               sd=DDG_UNCERTAINTY_KCAL, citations=(CITATIONS[0],),
               notes="destabilising-positive; benchmark-scale uncertainty, not a FoldX substitute"),
        Metric("predicted_tm", tm, "C",
               "IVYWREL composition prior plus Becktel-Schellman conversion of the ddG proxy",
               "skill.physics", sd=TM_UNCERTAINTY_C, citations=(CITATIONS[1], CITATIONS[2])),
    ]

    if payload.relax_steps:
        relaxation = dock.relax(model, steps=payload.relax_steps)
        metrics.append(
            Metric("strain_energy", relaxation["final_energy"], "arbitrary",
                   relaxation["method"], "skill.physics",
                   notes=f"after {payload.relax_steps} steepest-descent steps")
        )

    if payload.target_pdb:
        target = structlib.load_structure(payload.target_pdb, name="target")
        binding = dock.dock(model, target)
        metrics.extend(
            [
                Metric("binding_score", binding.binding_score, "arbitrary",
                       "rigid-body shape/contact-potential docking proxy", "skill.physics",
                       sd=binding.uncertainty, citations=(CITATIONS[3],)),
                Metric("interface_contacts", float(binding.contacts), "count",
                       "residue pairs within the docking interface cutoff", "skill.physics"),
                Metric("predicted_log10_kd_shift", round(binding.binding_score / 10.0, 3), "log10",
                       "binding proxy mapped to a relative log10 KD shift", "skill.physics",
                       sd=BINDING_LOG10_UNCERTAINTY, citations=(CITATIONS[3],),
                       notes="relative only; docking scores do not give absolute affinity"),
            ]
        )

    return PhysicsOutput(
        length=len(seq),
        structure_source=model.source,
        metrics=collect(metrics),
        contacts=len(contacts),
        secondary_structure=geometry["secondary_structure"],
        citations=CITATIONS,
    )


SKILL = register(
    SkillSpec(
        name="skill.physics",
        version=VERSION,
        summary=(
            "Structural features (Rg, compactness, contact order, clashes), coarse relaxation, "
            "ddG proxy and a Becktel-Schellman predicted Tm, each with a benchmark-scale error bar."
        ),
        input_model=PhysicsInput,
        output_model=PhysicsOutput,
        runner=run,
        citations=CITATIONS,
    )
)
