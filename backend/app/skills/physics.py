"""skill.physics — structural and thermodynamic features with honest error bars.

Wraps Foldsmith's deterministic toolkit (coarse folding, contact analysis, ddG proxy, rigid-body
docking) and adds the two things the wet-lab loop needs: a *predicted melting temperature* on the
same scale the lab measures, and a defensible uncertainty for every number.

What is published, and what is ours (full records in :data:`EVIDENCE`):

* **ddG proxy sd = 1.2 kcal/mol** — literature-anchored on Potapov, Cohen & Schreiber 2009
  (doi:10.1093/protein/gzp030): across a 2000+ mutation benchmark, published ddG predictors reach
  only r = 0.26-0.59 against experiment while experimental replicates correlate at r = 0.86.
  Applies to single point mutations in small globular proteins; our own proxy has never been
  benchmarked, so this is the scale of the *class* of methods, not of this implementation.
* **dTm = -ddG / dS_m** — the Becktel-Schellman relation (doi:10.1002/bip.360261104), valid for
  two-state monomeric domains near Tm. ``dS_m`` per residue is a rounded typical value and is
  *not* calibrated for a specific protein, so the converted dTm is a scale, not a measurement.
* **IVYWREL composition prior** — Zeldovich, Berezovsky & Shakhnovich 2007
  (doi:10.1371/journal.pcbi.0030005) report r = 0.93 between proteome IVYWREL fraction and
  optimal growth temperature over 204 prokaryotic proteomes (~-10 to 110 C). That is a
  *proteome-vs-organism* relation: the intercept and slope used here are uncalibrated at the level
  of one protein, and Lopez et al. 2025 (doi:10.1002/prot.70019) show cross-species Tm
  correlations overstate within-protein performance.
* **predicted_tm sd = 9 C** — deliberately conservative project policy: more than twice the best
  reported sequence-only validation RMSE of 4.11 C (doi:10.1038/s41598-025-98667-9), because that
  number belongs to a trained language model, not to a composition prior.
* **binding proxy sd = 1 log10** — Kastritis & Bonvin 2010 (doi:10.1021/pr9009854) found docking
  scoring functions poorly correlated with affinity over an 81-complex benchmark.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.skills import evidence
from app.skills.base import Metric, SkillSpec, collect, register
from app.toolkit import developability as dev
from app.toolkit import docking as dock
from app.toolkit import folding
from app.toolkit import sequence as seqlib
from app.toolkit import structure as structlib

VERSION = "1.0.0"

EVIDENCE = evidence.register(
    "skill.physics",
    (
        evidence.Evidence(
            key="physics.ddg_proxy_sd",
            claim="ddG proxy is reported with a 1.2 kcal/mol standard deviation",
            applicability="single point mutations in small globular proteins (benchmark of >2000 mutations)",
            error="published ddG predictors reach r=0.26-0.59 vs experiment; experimental replicates "
                  "correlate at r=0.86, so ~1 kcal/mol is the floor for any predictor of this class",
            calibration=evidence.ANCHORED,
            source="Potapov, Cohen & Schreiber 2009, Assessing computational methods for predicting "
                   "protein stability upon mutation (Protein Eng Des Sel 22:553)",
            doi="10.1093/protein/gzp030",
            reference_value="predictor-experiment correlation 0.26-0.59; replicate correlation 0.86",
        ),
        evidence.Evidence(
            key="physics.becktel_schellman",
            claim="dTm = -ddG / dS_m converts a stability change into a melting-temperature shift",
            applicability="two-state, monomeric domains evaluated near Tm",
            error="the relation is exact under two-state assumptions, but dS_m per residue here is a "
                  "rounded typical value (4.5 cal/mol/K/residue) with no per-protein calibration, so "
                  "the dTm magnitude should be read as a scale, not a prediction",
            calibration=evidence.ANCHORED,
            source="Becktel & Schellman 1987, Protein stability curves (Biopolymers 26:1859)",
            doi="10.1002/bip.360261104",
        ),
        evidence.Evidence(
            key="physics.ivywrel_prior",
            claim="baseline Tm prior moves with the IVYWREL fraction of the sequence",
            applicability="204 complete prokaryotic proteomes with optimal growth temperature ~-10 to "
                          "110 C; the relation is proteome-vs-organism, not residue-vs-protein",
            error="r=0.93 between proteome IVYWREL content and growth temperature, but no protein-level "
                  "Tm calibration exists: the 55 C intercept and 95 C-per-unit-fraction slope are "
                  "uncalibrated, and cross-species correlations overstate within-protein accuracy "
                  "(Lopez et al. 2025, doi:10.1002/prot.70019)",
            calibration=evidence.PROXY,
            source="Zeldovich, Berezovsky & Shakhnovich 2007, Protein and DNA sequence determinants of "
                   "thermophilic adaptation (PLoS Comput Biol 3:e5)",
            doi="10.1371/journal.pcbi.0030005",
            reference_value="IVYWREL vs optimal growth temperature r=0.93 across 204 proteomes",
        ),
        evidence.Evidence(
            key="physics.predicted_tm_sd",
            claim="predicted_tm carries a 9 C standard deviation",
            applicability="any sequence; the sd is a platform-wide floor, not a per-protein estimate",
            error="the best reported sequence-only Tm model achieves 4.11 C validation RMSE (MAE 3.00 C, "
                  "PCC 0.89) with a pretrained protein language model; a composition-only prior such as "
                  "ours is strictly weaker, so we widen to >2x that RMSE by policy",
            calibration=evidence.POLICY,
            source="Tijare et al. 2025, Prediction and design of thermostable proteins with a desired "
                   "melting temperature (Sci Rep 15)",
            doi="10.1038/s41598-025-98667-9",
            reference_value="best sequence-only validation RMSE 4.11 C",
        ),
        evidence.Evidence(
            key="physics.binding_proxy_sd",
            claim="docking-derived binding proxy and log10 KD shift carry ~1 log10 uncertainty",
            applicability="rigid-body protein-protein complexes; 81-complex affinity benchmark",
            error="docking scoring functions showed poor correlation with measured affinity across the "
                  "benchmark, hence a full order of magnitude in KD and relative use only. The paper "
                  "reports the poor correlation; the 1 log10 envelope itself is our choice",
            calibration=evidence.POLICY,
            source="Kastritis & Bonvin 2010, Are scoring functions in protein-protein docking ready to "
                   "predict interactomes? (J Proteome Res 9:2216)",
            doi="10.1021/pr9009854",
        ),
        evidence.Evidence(
            key="physics.relative_contact_order",
            claim="relative contact order is reported as a topology descriptor",
            applicability="small single-domain two-state folders, where contact order correlates with "
                          "refolding rate",
            error="reported without an error bar because it is a geometric descriptor of the model we "
                  "built, not a predicted folding rate",
            calibration=evidence.ANCHORED,
            source="Plaxco, Simons & Baker 1998, Contact order, transition state placement and the "
                   "refolding rates of single domain proteins (J Mol Biol 277:985)",
            doi="10.1006/jmbi.1998.1645",
        ),
    ),
)

CITATIONS = evidence.citations("skill.physics")


def cite(*keys: str) -> tuple[str, ...]:
    """Citation lines for the named evidence records, so metrics never index into a list."""
    return tuple(evidence.record("skill.physics", key).citation() for key in keys)


# Becktel-Schellman dS_m per residue for small single domains, kcal/(mol*K). Rounded typical value:
# see EVIDENCE["physics.becktel_schellman"] — not calibrated for any particular protein.
ENTROPY_OF_UNFOLDING_PER_RESIDUE = 0.0045
# Mesophilic reference point for a small soluble domain; the IVYWREL term moves it. Uncalibrated.
BASELINE_TM_C = 55.0
IVYWREL = set("IVYWREL")
# Slope of Tm on IVYWREL fraction. Uncalibrated at protein level (physics.ivywrel_prior).
TM_PER_IVYWREL_FRACTION = 95.0
# Literature reference values pinned by tests/test_evidence.py.
DDG_PREDICTOR_R_RANGE = (0.26, 0.59)
DDG_EXPERIMENTAL_REPLICATE_R = 0.86
BEST_SEQUENCE_ONLY_TM_RMSE_C = 4.11
IVYWREL_PROTEOME_R = 0.93

DDG_UNCERTAINTY_KCAL = 1.2
# Conservative: >2x BEST_SEQUENCE_ONLY_TM_RMSE_C (physics.predicted_tm_sd).
TM_UNCERTAINTY_C = 9.0
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
    """Composition-only Tm prior: thermophilic proteomes are enriched in IVYWREL residues.

    Anchored on Zeldovich et al. 2007 (doi:10.1371/journal.pcbi.0030005), where proteome IVYWREL
    fraction tracks optimal growth temperature at r=0.93 across 204 prokaryotic proteomes spanning
    ~-10 to 110 C. The intercept and slope are *uncalibrated* for a single protein: use this to
    compare designs of the same protein, never as a measured Tm.
    """
    fraction = ivywrel_fraction(sequence)
    return round(BASELINE_TM_C + TM_PER_IVYWREL_FRACTION * (fraction - 0.30), 2)


def ddg_to_dtm(ddg_kcal: float, length: int) -> float:
    """Becktel-Schellman conversion. ddG here is destabilising-positive, so dTm flips sign.

    ``dTm = -ddG / dS_m`` with ``dS_m = 4.5 cal/(mol*K)`` per residue
    (Becktel & Schellman 1987, doi:10.1002/bip.360261104). Valid for two-state monomeric domains
    near Tm; the per-residue entropy is a rounded typical value, so treat the result as a scale.
    """
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
               citations=cite("physics.relative_contact_order")),
        Metric("clash_count", float(geometry["clashes"]), "count",
               "CA pairs closer than 3.4 A after clash relief", "skill.physics"),
        Metric("ddg_proxy", ddg.value, "kcal/mol", ddg.method, "skill.physics",
               sd=DDG_UNCERTAINTY_KCAL, citations=cite("physics.ddg_proxy_sd"),
               notes="destabilising-positive; benchmark-scale uncertainty (predictors of this class "
                     "reach r=0.26-0.59), not a FoldX substitute"),
        Metric("predicted_tm", tm, "C",
               "IVYWREL composition prior plus Becktel-Schellman conversion of the ddG proxy",
               "skill.physics", sd=TM_UNCERTAINTY_C,
               citations=cite("physics.becktel_schellman", "physics.ivywrel_prior",
                              "physics.predicted_tm_sd"),
               notes="composition prior is uncalibrated at protein level; sd is a conservative policy "
                     "floor above the best published sequence-only RMSE of 4.11 C"),
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
                       sd=binding.uncertainty, citations=cite("physics.binding_proxy_sd")),
                Metric("interface_contacts", float(binding.contacts), "count",
                       "residue pairs within the docking interface cutoff", "skill.physics"),
                Metric("predicted_log10_kd_shift", round(binding.binding_score / 10.0, 3), "log10",
                       "binding proxy mapped to a relative log10 KD shift", "skill.physics",
                       sd=BINDING_LOG10_UNCERTAINTY, citations=cite("physics.binding_proxy_sd"),
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
