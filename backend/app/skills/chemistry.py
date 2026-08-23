"""skill.chemistry — solution behaviour of the molecule you are about to order.

Answers the questions that decide whether a construct survives expression and purification:
what is its pI, is the working buffer too close to it, is there an unpaired cysteine, which PTM
motifs will bite, and can it even be quantified by A280.

Provenance of every number (full records in :data:`EVIDENCE`):

* **isoelectric point, sd 0.87 pH units** — computed with the Bjellqvist pKa set
  (doi:10.1002/elps.11501401163). The sd is the *measured* average error of sequence-based pI
  predictors on Kozlowski's benchmark (doi:10.1186/s13062-016-0159-9): 0.87 pH units for proteins,
  0.25 for peptides. This is the only genuinely calibrated number in the skill.
* **GRAVY** — Kyte & Doolittle hydropathy scale (doi:10.1016/0022-2836(82)90515-0). A descriptor;
  the paper publishes no error bar and it is not a solubility prediction.
* **epsilon280** — Gill & von Hippel summation (doi:10.1016/0003-2697(89)90602-7); the residue
  contributions (Trp 5500, Tyr 1490, cystine 125 M-1 cm-1) are the values Pace et al. 1995 fitted
  to 116 measured coefficients for 80 proteins (doi:10.1002/pro.5560041120), who state they are
  reliable for Trp-containing proteins and *less reliable* without Trp - and that measuring beats
  predicting. The 1500 M-1 cm-1 floor below which we refuse to trust A280 is a *project policy*
  (about one tyrosine's worth of absorbance), not a published cutoff.
* **PTM motifs** — sequon and deamidation-context definitions from Gavel & von Heijne 1990
  (doi:10.1093/protein/3.5.433) and Robinson & Robinson 2001 (doi:10.1073/pnas.98.3.944), which
  measured 306 asparaginyl pentapeptides at pH 7.4 / 37 C / 0.15 M Tris. We report *sites*, never
  rates: real rates span orders of magnitude with sequence and conformation.
* **the "stay 1 pH unit from the pI" rule** — project policy. The pI/solubility-minimum phenomenon
  is textbook, but the 1.0-unit margin has no empirical calibration and is labelled as such.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.skills import evidence
from app.skills.base import Metric, SkillSpec, collect, register
from app.toolkit import developability as dev
from app.toolkit import sequence as seqlib

VERSION = "1.0.0"

EVIDENCE = evidence.register(
    "skill.chemistry",
    (
        evidence.Evidence(
            key="chemistry.isoelectric_point",
            claim="isoelectric point from the Bjellqvist pKa set, reported with sd 0.87 pH units",
            applicability="denatured/unfolded sequence behaviour in immobilised pH gradients; folded "
                          "proteins with buried ionisable groups deviate further",
            error="average error of sequence-based pI prediction is 0.87 pH units for proteins and "
                  "0.25 pH units for peptides on the IPC benchmark",
            calibration=evidence.CALIBRATED,
            source="Bjellqvist et al. 1993, The focusing positions of polypeptides in immobilized pH "
                   "gradients (Electrophoresis 14:1023); error from Kozlowski 2016, IPC - Isoelectric "
                   "Point Calculator (Biol Direct 11:55, doi:10.1186/s13062-016-0159-9)",
            doi="10.1002/elps.11501401163",
            reference_value="average protein pI error 0.87 pH units; peptides 0.25 pH units",
        ),
        evidence.Evidence(
            key="chemistry.pi_error_benchmark",
            claim="the pI uncertainty we publish is taken from an independent benchmark, not guessed",
            applicability="large sequence benchmarks of pI predictors (proteins and peptides)",
            error="0.87 pH units mean absolute error for proteins; 0.25 for peptides; later methods "
                  "(IPC 2.0, doi:10.1093/nar/gkab295) improve on this but not by an order of magnitude",
            calibration=evidence.CALIBRATED,
            source="Kozlowski 2016, IPC - Isoelectric Point Calculator (Biol Direct 11:55)",
            doi="10.1186/s13062-016-0159-9",
            reference_value="0.87 pH units (proteins), 0.25 pH units (peptides)",
        ),
        evidence.Evidence(
            key="chemistry.gravy",
            claim="GRAVY is the mean Kyte-Doolittle hydropathy over the sequence",
            applicability="any sequence; the scale was derived for membrane/interior propensity plots",
            error="none published: it is a descriptor, not a predictor, and must not be read as a "
                  "predicted solubility or partition coefficient",
            calibration=evidence.ANCHORED,
            source="Kyte & Doolittle 1982, A simple method for displaying the hydropathic character of "
                   "a protein (J Mol Biol 157:105)",
            doi="10.1016/0022-2836(82)90515-0",
        ),
        evidence.Evidence(
            key="chemistry.extinction_coefficient",
            claim="epsilon280 is summed from Trp/Tyr/cystine contributions",
            applicability="folded proteins in water containing at least one Trp or Tyr; unfolded "
                          "proteins in 6 M guanidine are the reference state of the original work",
            error="fitted on 116 measured coefficients for 80 proteins; the authors report the estimate "
                  "is reliable for Trp-containing proteins and less reliable for Trp-free ones, and "
                  "recommend measuring epsilon rather than predicting it. No single error figure is "
                  "published, so we attach no sd to this metric",
            calibration=evidence.ANCHORED,
            source="Gill & von Hippel 1989, Calculation of protein extinction coefficients from amino "
                   "acid sequence data (Anal Biochem 182:319); Pace et al. 1995, How to measure and "
                   "predict the molar absorption coefficient of a protein (Protein Sci 4:2411, "
                   "doi:10.1002/pro.5560041120)",
            doi="10.1016/0003-2697(89)90602-7",
        ),
        evidence.Evidence(
            key="chemistry.a280_floor",
            claim="A280 quantification is flagged unreliable below epsilon280 = 1500 M-1 cm-1",
            applicability="our own quantification advice for constructs about to be ordered",
            error="no published threshold exists; 1500 is roughly one tyrosine (1490 M-1 cm-1) and is a "
                  "project policy chosen so that a single aromatic residue cannot carry the assay",
            calibration=evidence.POLICY,
            source="Pace et al. 1995, How to measure and predict the molar absorption coefficient of a "
                   "protein (Protein Sci 4:2411) - basis for the residue contributions only",
            doi="10.1002/pro.5560041120",
        ),
        evidence.Evidence(
            key="chemistry.ptm_motifs",
            claim="regex scan reports N-X-S/T sequons and Asn/Asp chemical-liability contexts as sites",
            applicability="N-glycosylation sequons compiled from glycosylated vs non-glycosylated sites; "
                          "deamidation contexts measured on 306 asparaginyl pentapeptides at pH 7.4, "
                          "37 C, 0.15 M Tris",
            error="site presence only. Measured deamidation half-times across those sequences span "
                  "orders of magnitude and depend on main-chain conformation "
                  "(doi:10.3390/ijms21197035), so no rate is emitted and none should be inferred",
            calibration=evidence.ANCHORED,
            source="Robinson & Robinson 2001, Molecular clocks (PNAS 98:944); sequon statistics from "
                   "Gavel & von Heijne 1990 (Protein Eng 3:433, doi:10.1093/protein/3.5.433)",
            doi="10.1073/pnas.98.3.944",
            reference_value="306 asparaginyl sequences measured at pH 7.4, 37 C",
        ),
        evidence.Evidence(
            key="chemistry.instability_index",
            claim="instability index is the Guruprasad dipeptide-composition score",
            applicability="statistical analysis of 12 unstable and 32 stable proteins; predicts in-vivo "
                          "metabolic stability, not thermal stability",
            error="uncalibrated: the source reports no prediction accuracy, and the widely quoted "
                  "cutoff of 40 comes from ExPASy ProtParam documentation rather than this paper, "
                  "so we emit the index with no pass/fail rule and treat it as a screening flag",
            calibration=evidence.PROXY,
            source="Guruprasad, Reddy & Pandit 1990, Correlation between stability of a protein and its "
                   "dipeptide composition (Protein Eng 4:155)",
            doi="10.1093/protein/4.2.155",
            reference_value="derived from 12 unstable and 32 stable proteins",
        ),
        evidence.Evidence(
            key="chemistry.pi_buffer_margin",
            claim="formulations are kept at least 1.0 pH unit away from the pI",
            applicability="our buffer recommendation for soluble constructs",
            error="uncalibrated: solubility minima near the pI are textbook, but the size of the safe "
                  "margin is protein- and salt-dependent and this 1.0-unit rule has no empirical "
                  "calibration behind it",
            calibration=evidence.POLICY,
        ),
    ),
)

CITATIONS = evidence.citations("skill.chemistry")


def cite(*keys: str) -> tuple[str, ...]:
    return tuple(evidence.record("skill.chemistry", key).citation() for key in keys)


# PTM / chemical-liability motifs beyond the toolkit's default scan. Motif definitions, not rates:
# see EVIDENCE["chemistry.ptm_motifs"].
PTM_MOTIFS = {
    "n_glycosylation": r"N[^P][ST]",
    "deamidation_ng": r"NG",
    "deamidation_ns": r"N[SGH]",
    "isomerization_dg": r"D[GSTP]",
    "oxidation_met": r"M",
    "tryptic_kr": r"[KR][^P]",
}
# Project policy, ~one tyrosine of absorbance (chemistry.a280_floor). No published cutoff exists.
MIN_EXTINCTION_FOR_A280 = 1500
# Project policy margin between working pH and pI (chemistry.pi_buffer_margin).
MIN_PI_OFFSET_PH = 1.0
# Literature reference values pinned by tests/test_evidence.py.
PI_PREDICTION_ERROR_PH = 0.87
PEPTIDE_PI_PREDICTION_ERROR_PH = 0.25
TRP_EXTINCTION_M1CM1 = 5500
TYROSINE_EXTINCTION_M1CM1 = 1490
CYSTINE_EXTINCTION_M1CM1 = 125
PACE_EPSILON_PROTEINS = 80


class ChemistryInput(BaseModel):
    sequence: str
    working_ph: float = Field(default=7.4, ge=2.0, le=12.0)
    buffer: str = Field(default="20 mM phosphate, 150 mM NaCl")
    reducing_environment: bool = Field(
        default=True, description="True for cytoplasmic E. coli expression"
    )


class ChemistryOutput(BaseModel):
    metrics: dict[str, dict] = Field(default_factory=dict)
    ptm_sites: dict[str, list[int]] = Field(default_factory=dict)
    liabilities: dict = Field(default_factory=dict)
    buffer_recommendation: dict = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


def ptm_sites(sequence: str) -> dict[str, list[int]]:
    seq = seqlib.clean_sequence(sequence)
    out: dict[str, list[int]] = {}
    for name, pattern in PTM_MOTIFS.items():
        hits = [m.start() + 1 for m in re.finditer(pattern, seq)]
        if hits:
            out[name] = hits[:24]
    return out


def buffer_recommendation(pi: float, working_ph: float, buffer: str) -> dict:
    """Keep the formulation at least :data:`MIN_PI_OFFSET_PH` away from the isoelectric point.

    Project policy, not a published threshold: proteins are least soluble near their pI, but the
    size of a safe margin is protein- and ionic-strength-dependent and this rule is uncalibrated
    (see ``EVIDENCE["chemistry.pi_buffer_margin"]``). Note the pI itself carries ~0.87 pH units of
    prediction error (doi:10.1186/s13062-016-0159-9), which is comparable to the margin.
    """
    offset = round(abs(pi - working_ph), 2)
    if offset >= MIN_PI_OFFSET_PH:
        return {
            "verdict": "ok",
            "buffer": buffer,
            "working_ph": working_ph,
            "pi_offset": offset,
            "rationale": "working pH is >= 1 unit from pI, so net charge keeps the protein dispersed "
                         "(uncalibrated policy margin; predicted pI itself has ~0.87 pH units of error)",
            "calibration": evidence.POLICY,
        }
    suggested = round(pi - 1.5, 1) if pi > working_ph else round(pi + 1.5, 1)
    suggested = min(max(suggested, 4.0), 9.5)
    return {
        "verdict": "change-buffer",
        "buffer": ("50 mM sodium acetate, 150 mM NaCl" if suggested < 6.0
                   else "25 mM Tris-HCl, 150 mM NaCl"),
        "working_ph": suggested,
        "pi_offset": offset,
        "rationale": (
            f"pH {working_ph} sits {offset} units from pI {pi}: solubility minimum, expect "
            f"precipitation during concentration. Move to pH {suggested}."
        ),
        "calibration": evidence.POLICY,
    }


def run(payload: ChemistryInput) -> ChemistryOutput:
    seq = seqlib.clean_sequence(payload.sequence)
    desc = seqlib.descriptors(seq)
    pi = desc["isoelectric_point"]
    charge = seqlib.net_charge(seq, payload.working_ph)
    epsilon = seqlib.extinction_coefficient(seq)
    liabilities = dev.liabilities(seq)
    sites = ptm_sites(seq)
    cys = seq.count("C")

    flags: list[str] = []
    if cys % 2 == 1:
        flags.append(
            f"unpaired cysteine ({cys} total): expect scrambling or covalent dimer; "
            "cap with Ser or add a partner"
        )
    if cys >= 2 and payload.reducing_environment:
        flags.append(
            "cysteine pair in a reducing cytoplasm: disulfide will not form — use periplasmic "
            "(pelB/DsbA) or an oxidising strain if the bond is structural"
        )
    if "n_glycosylation" in sites:
        flags.append(
            f"{len(sites['n_glycosylation'])} N-X-S/T sequon(s): silent in E. coli, "
            "heterogeneity in mammalian hosts"
        )
    if "deamidation_ng" in sites:
        flags.append(f"NG deamidation hotspot(s) at {sites['deamidation_ng'][:6]}")
    if epsilon < MIN_EXTINCTION_FOR_A280:
        flags.append(
            f"epsilon280 = {epsilon} M-1cm-1 is too low for reliable A280: quantify by BCA or Bradford"
        )
    if abs(pi - payload.working_ph) < MIN_PI_OFFSET_PH:
        flags.append(f"pI {pi} is within 1 unit of pH {payload.working_ph}: solubility minimum")

    metrics = collect(
        [
            Metric("isoelectric_point", pi, "pH", "Bjellqvist pKa set on the full sequence",
                   "skill.chemistry", sd=PI_PREDICTION_ERROR_PH,
                   citations=cite("chemistry.isoelectric_point", "chemistry.pi_error_benchmark"),
                   notes="sd is the published mean absolute error of sequence-based pI prediction "
                         "(0.87 pH units for proteins), not an instrument precision"),
            Metric("net_charge", round(charge, 2), "e",
                   f"Henderson-Hasselbalch summation at pH {payload.working_ph}", "skill.chemistry"),
            Metric("gravy", desc["gravy"], "kcal/mol/residue",
                   "Kyte-Doolittle grand average of hydropathy", "skill.chemistry",
                   citations=cite("chemistry.gravy"),
                   notes="hydropathy descriptor with no published error bar; not a solubility value"),
            Metric("molecular_weight", desc["molecular_weight"], "Da",
                   "average isotopic residue masses plus one water", "skill.chemistry"),
            Metric("extinction_coefficient_280", float(epsilon), "M-1 cm-1",
                   "Gill & von Hippel Trp/Tyr/Cys summation", "skill.chemistry",
                   citations=cite("chemistry.extinction_coefficient")),
            Metric("cysteine_count", float(cys), "count", "residue count", "skill.chemistry"),
            Metric("instability_index", desc["instability_index"], "index",
                   "Guruprasad dipeptide instability index", "skill.chemistry",
                   citations=cite("chemistry.instability_index"),
                   notes="in-vivo metabolic-stability index with no published accuracy and no cutoff "
                         "from the source paper; screening flag only"),
            Metric("ptm_site_count", float(sum(len(v) for v in sites.values())), "count",
                   "regex scan for glycosylation, deamidation, isomerisation and oxidation motifs",
                   "skill.chemistry", citations=cite("chemistry.ptm_motifs"),
                   notes="motif sites only; measured deamidation rates span orders of magnitude with "
                         "sequence context and conformation, so no rate is implied"),
        ]
    )
    return ChemistryOutput(
        metrics=metrics,
        ptm_sites=sites,
        liabilities=liabilities,
        buffer_recommendation=buffer_recommendation(pi, payload.working_ph, payload.buffer),
        flags=flags,
        citations=CITATIONS,
    )


SKILL = register(
    SkillSpec(
        name="skill.chemistry",
        version=VERSION,
        summary=(
            "pI, net charge, GRAVY, extinction coefficient, cysteine/PTM liabilities and a buffer "
            "recommendation that keeps the formulation off the solubility minimum."
        ),
        input_model=ChemistryInput,
        output_model=ChemistryOutput,
        runner=run,
        citations=CITATIONS,
    )
)
