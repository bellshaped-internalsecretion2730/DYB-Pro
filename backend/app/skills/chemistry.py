"""skill.chemistry — solution behaviour of the molecule you are about to order.

Answers the questions that decide whether a construct survives expression and purification:
what is its pI, is the working buffer too close to it, is there an unpaired cysteine, which PTM
motifs will bite, and can it even be quantified by A280.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.skills.base import Metric, SkillSpec, collect, register
from app.toolkit import developability as dev
from app.toolkit import sequence as seqlib

VERSION = "1.0.0"

CITATIONS = [
    "Bjellqvist et al. 1993, The focusing positions of polypeptides in immobilized pH gradients "
    "(Electrophoresis 14:1023) — pI calculation and the pI/solubility minimum",
    "Kyte & Doolittle 1982, A simple method for displaying the hydropathic character of a protein "
    "(J Mol Biol 157:105) — GRAVY",
    "Pace et al. 1995, How to measure and predict the molar absorption coefficient of a protein "
    "(Protein Sci 4:2411) — A280 quantification limits",
    "Gill & von Hippel 1989, Calculation of protein extinction coefficients from amino acid "
    "sequence data (Anal Biochem 182:319)",
    "Robinson & Robinson 2001, Molecular clocks (PNAS 98:944) — Asn deamidation sequence context",
]

# PTM / chemical-liability motifs beyond the toolkit's default scan.
PTM_MOTIFS = {
    "n_glycosylation": r"N[^P][ST]",
    "deamidation_ng": r"NG",
    "deamidation_ns": r"N[SGH]",
    "isomerization_dg": r"D[GSTP]",
    "oxidation_met": r"M",
    "tryptic_kr": r"[KR][^P]",
}
# A280 is unreliable when the aromatic content is too low to give a usable epsilon.
MIN_EXTINCTION_FOR_A280 = 1500


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
    """Keep the formulation at least one pH unit away from the isoelectric point."""
    offset = round(abs(pi - working_ph), 2)
    if offset >= 1.0:
        return {
            "verdict": "ok",
            "buffer": buffer,
            "working_ph": working_ph,
            "pi_offset": offset,
            "rationale": "working pH is >= 1 unit from pI, so net charge keeps the protein dispersed",
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
    if abs(pi - payload.working_ph) < 1.0:
        flags.append(f"pI {pi} is within 1 unit of pH {payload.working_ph}: solubility minimum")

    metrics = collect(
        [
            Metric("isoelectric_point", pi, "pH", "Bjellqvist pKa set on the full sequence",
                   "skill.chemistry", sd=0.5, citations=(CITATIONS[0],)),
            Metric("net_charge", round(charge, 2), "e",
                   f"Henderson-Hasselbalch summation at pH {payload.working_ph}", "skill.chemistry"),
            Metric("gravy", desc["gravy"], "kcal/mol/residue",
                   "Kyte-Doolittle grand average of hydropathy", "skill.chemistry",
                   citations=(CITATIONS[1],)),
            Metric("molecular_weight", desc["molecular_weight"], "Da",
                   "average isotopic residue masses plus one water", "skill.chemistry"),
            Metric("extinction_coefficient_280", float(epsilon), "M-1 cm-1",
                   "Gill & von Hippel Trp/Tyr/Cys summation", "skill.chemistry",
                   citations=(CITATIONS[3],)),
            Metric("cysteine_count", float(cys), "count", "residue count", "skill.chemistry"),
            Metric("instability_index", desc["instability_index"], "index",
                   "Guruprasad dipeptide instability index", "skill.chemistry"),
            Metric("ptm_site_count", float(sum(len(v) for v in sites.values())), "count",
                   "regex scan for glycosylation, deamidation, isomerisation and oxidation motifs",
                   "skill.chemistry", citations=(CITATIONS[4],)),
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
