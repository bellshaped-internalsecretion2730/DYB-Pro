"""Developability: stability, solubility, aggregation, immunogenicity, liabilities, filters.

These are independent implementations of published heuristics (see CITATIONS.md for DOIs,
applicability and published error magnitudes). They are deliberately transparent and
deterministic: each returns the value, the method name and the inputs that drove it, so a
scientist can audit "why this score".

None of the scores in this module is calibrated against experiment. The published methods supply
the *ingredients* (which properties matter, in which direction); the window sizes, cut-offs and
weights here were chosen by hand, so every value is in arbitrary units and is only meaningful when
comparing designs of the same protein. Where a calibrated alternative exists it is named in the
docstring so the limitation is actionable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.toolkit import sequence as seqlib
from app.toolkit.constants import CHARGE_AT_PH7, HELIX_PROPENSITY, HYDROPATHY
from app.toolkit.structure import Structure, relative_exposure

# Liability motifs (regexes on the protein sequence). Names describe what the regex actually
# matches -- a motif hit is a site worth checking, not evidence that the modification occurs.
# Sequon definition: Gavel & von Heijne 1990 (doi:10.1093/protein/3.5.433); Asn deamidation
# context: Robinson & Robinson 2001 (doi:10.1073/pnas.98.3.944), 306 measured asparaginyl
# sequences at pH 7.4 / 37 C. Measured rates span orders of magnitude with context and
# conformation (doi:10.3390/ijms21197035), so no rate is implied by a hit.
LIABILITY_MOTIFS = {
    "n_glycosylation_sequon": r"N[^P][ST]",
    "deamidation_NG_NS": r"N[GS]",
    "isomerization_DG_DS": r"D[GS]",
    "oxidation_prone_MW": r"[MW]",
    "protease_dibasic": r"(KK|RR|KR|RK)",
    "cysteine_positions": r"C",
}

# Buried-hydrophobic tolerance: substituting a large hydrophobic in the core is destabilizing.
VOLUME = {
    "A": 88.6, "R": 173.4, "N": 114.1, "D": 111.1, "C": 108.5, "Q": 143.8, "E": 138.4,
    "G": 60.1, "H": 153.2, "I": 166.7, "L": 166.7, "K": 168.6, "M": 162.9, "F": 189.9,
    "P": 112.7, "S": 89.0, "T": 116.1, "W": 227.8, "Y": 193.6, "V": 140.0,
}


@dataclass
class Metric:
    name: str
    value: float
    unit: str = ""
    method: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "method": self.method,
            "detail": self.detail,
        }


def aggregation_propensity(seq: str, window: int = 7) -> Metric:
    """Hydrophobic-patch scan: fraction of windows whose mean hydropathy exceeds +1.5.

    Follows the aggregation-prone-region logic of window-based predictors (Tartaglia &
    Vendruscolo 2008, Zyggregator, doi:10.1039/b706784b): contiguous apolar stretches with low net
    charge drive aggregation. It is a sequence-only flag for where to look, not a predicted
    aggregation rate or % monomer.

    Applicability and error: the *direction* is well supported, the *numbers are not*. The
    7-residue window, the +1.5 mean-hydropathy cut-off and the |q| <= 1 charge condition are
    hand-chosen and have no published calibration; the returned fraction has no error bar because
    there is no measured quantity to compare it with. For a predictor trained on experimentally
    evaluated hexapeptides and benchmarked independently, use AggreProt (doi:10.1093/nar/gkae420);
    for solubility change on mutation, CamSol (doi:10.1016/j.jmb.2014.09.026).
    """
    seq = seqlib.clean_sequence(seq)
    if len(seq) < window:
        window = len(seq)
    hot: list[dict] = []
    for i in range(0, len(seq) - window + 1):
        sub = seq[i : i + window]
        mean_h = sum(HYDROPATHY[a] for a in sub) / window
        charge = sum(abs(CHARGE_AT_PH7.get(a, 0.0)) for a in sub)
        if mean_h > 1.5 and charge <= 1.0:
            hot.append(
                {
                    "start": i + 1,
                    "end": i + window,
                    "segment": sub,
                    "mean_hydropathy": round(mean_h, 2),
                }
            )
    score = round(len(hot) / max(1, len(seq) - window + 1), 4)
    return Metric(
        name="aggregation_propensity",
        value=score,
        method=f"hydrophobic-window scan (w={window}, h>1.5, |q|<=1)",
        detail={"hot_spots": hot[:12], "hot_spot_count": len(hot)},
    )


def solubility(seq: str) -> Metric:
    """Intrinsic solubility index from charge density, hydropathy and aggregation load.

    Higher = expected to be more soluble. CamSol-inspired in its ingredients only
    (doi:10.1016/j.jmb.2014.09.026): the weights are hand-chosen, not fitted, so the value is in
    arbitrary units and cannot be read as a predicted mg/mL or as CamSol's score.

    Applicability and error: uncalibrated, with no published error magnitude. For context on the
    ceiling of this family, the trained sequence-only predictor SoluProt reaches 58.5% accuracy and
    AUC 0.62 on its independent test set (doi:10.1093/bioinformatics/btaa1102) - even a fitted
    model barely separates soluble from insoluble, so an unfitted composite such as this one must
    only be used to rank variants of the same protein.
    """
    seq = seqlib.clean_sequence(seq)
    charge = abs(seqlib.net_charge(seq)) / max(1, len(seq)) * 100.0
    gravy = seqlib.gravy(seq)
    agg = aggregation_propensity(seq).value
    value = round(0.45 * charge - 0.55 * gravy - 1.6 * agg, 4)
    return Metric(
        name="solubility",
        value=value,
        unit="arbitrary units (uncalibrated)",
        method="charge/GRAVY/aggregation composite (CamSol-inspired, unfitted weights)",
        detail={"charge_density": round(charge, 3), "gravy": gravy, "aggregation": agg},
    )


def immunogenicity(seq: str) -> Metric:
    """Density of 9-mers matching a generic MHC-II P1/P4/P6/P9 anchor pattern, per 100 aa.

    This is an allele-agnostic motif count, not a T-cell epitope prediction: it has no allele
    coverage, no binding affinity and no HLA frequency weighting. Use it to compare designs of
    the same protein, never to claim a protein is (non-)immunogenic.

    Applicability and error: the P1/P4/P6/P9 anchor convention comes from the MHC class II binding
    literature, but this count has no empirical calibration and no reported sensitivity or
    specificity - there is nothing to report, because it was never benchmarked. An allele-aware
    predictor trained on binding-affinity and eluted-ligand data (NetMHCIIpan-4.0,
    doi:10.1093/nar/gkaa379) is required before any immunogenicity claim leaves the platform.
    """
    seq = seqlib.clean_sequence(seq)
    anchors_p1 = set("FWYLIVM")
    anchors_p4 = set("AILVMFWY")
    anchors_p6 = set("AGSTNQDE")
    anchors_p9 = set("AILVMFWY")
    cores: list[str] = []
    for i in range(0, max(0, len(seq) - 8)):
        core = seq[i : i + 9]
        if (
            core[0] in anchors_p1
            and core[3] in anchors_p4
            and core[5] in anchors_p6
            and core[8] in anchors_p9
        ):
            cores.append(core)
    risk = round(len(cores) / max(1, len(seq)) * 100.0, 3)
    return Metric(
        name="immunogenicity_risk",
        value=risk,
        unit="cores/100aa",
        method="MHC-II anchor-motif 9-mer scan (P1/P4/P6/P9)",
        detail={"core_count": len(cores), "example_cores": cores[:8]},
    )


# Weights of the per-residue terms of the destabilization score. They are shape parameters of a
# ranking heuristic, not fitted free-energy coefficients: the term decomposition follows empirical
# ddG estimators (Guerois, Nielsen & Serrano 2002, doi:10.1016/s0022-2836(02)00442-4) but none of
# these numbers is theirs, and none was fitted here.
DDG_WEIGHTS = {
    "packing": 0.9,  # buried volume increase -> strain; decrease -> relief (signed)
    "burial_polarity": 0.35,  # losing apolar character in the core
    "helix_propensity": 0.5,
    "proline": 1.2,
    "glycine": 0.4,
}


def ddg_proxy(
    wt_seq: str,
    mutations: list[dict],
    structure: Structure | None = None,
) -> Metric:
    """Destabilization risk score for a mutation set. Higher = more likely destabilizing.

    Antisymmetric by construction: every term is a burial-weighted difference of a per-residue
    property, so ``score(X->Y) == -score(Y->X)``. A free-energy difference has that property; the
    symmetric BLOSUM substitution score does not, so BLOSUM is reported as context only and does
    not enter the value.

    The value is in arbitrary units and is **not** calibrated against experimental ΔΔG: use it to
    order candidates, never to predict a ΔΔG or a Tm shift. Volume and helix propensities follow
    the ingredients of empirical ΔΔG estimators (Guerois, Nielsen & Serrano 2002,
    doi:10.1016/s0022-2836(02)00442-4, tested on 1088 point mutants) without their fitted weights.

    Applicability and error: single point mutations in folded globular domains, additive across
    sites (epistasis is not modelled). No error magnitude can be quoted for this score because it
    has never been benchmarked; for scale, *fitted* predictors of this class reach only
    r = 0.26-0.59 against experiment where experimental replicates reach r = 0.86
    (doi:10.1093/protein/gzp030) - that benchmark is what ``skill.physics`` publishes as the
    uncertainty when it converts this score onto a Tm scale.
    """
    wt_seq = seqlib.clean_sequence(wt_seq)
    exposure = relative_exposure(structure) if structure is not None else None
    total = 0.0
    per_mut: list[dict] = []
    for mut in mutations:
        wt, mt, pos = mut["wt"], mut["mt"], int(mut["position"])
        idx = pos - 1
        buried = 0.5
        if exposure is not None and 0 <= idx < len(exposure):
            buried = 1.0 - exposure[idx]
        dvol = (VOLUME[mt] - VOLUME[wt]) / 100.0
        volume_term = DDG_WEIGHTS["packing"] * dvol * buried
        # signed: burying a more polar residue costs, exposing it refunds the same amount
        polarity_term = (
            DDG_WEIGHTS["burial_polarity"] * buried * (HYDROPATHY[wt] - HYDROPATHY[mt])
        )
        helix_term = DDG_WEIGHTS["helix_propensity"] * (
            HELIX_PROPENSITY[wt] - HELIX_PROPENSITY[mt]
        )
        backbone_term = DDG_WEIGHTS["proline"] * (
            (1.0 if mt == "P" else 0.0) - (1.0 if wt == "P" else 0.0)
        ) + DDG_WEIGHTS["glycine"] * ((1.0 if mt == "G" else 0.0) - (1.0 if wt == "G" else 0.0))
        contrib = round(volume_term + polarity_term + helix_term + backbone_term, 3)
        total += contrib
        per_mut.append(
            {
                "mutation": f"{wt}{pos}{mt}",
                "ddg": contrib,
                "burial": round(buried, 3),
                "volume_term": round(volume_term, 3),
                "polarity_term": round(polarity_term, 3),
                "helix_term": round(helix_term, 3),
                "backbone_term": round(backbone_term, 3),
                "cavity_risk": round(max(0.0, -dvol) * buried, 3),
                "blosum62": seqlib.sub_score(wt, mt),
            }
        )
    return Metric(
        name="ddg_proxy",
        value=round(total, 3),
        unit="arbitrary units (uncalibrated)",
        method="antisymmetric burial-weighted volume/polarity/helix/backbone terms",
        detail={
            "per_mutation": per_mut,
            "structure_aware": structure is not None,
            "structure_source": structure.source if structure is not None else None,
            "cavity_risk": round(sum(m["cavity_risk"] for m in per_mut), 3),
            "interpretation": (
                "relative destabilization risk for ordering candidates; not kcal/mol, not a Tm "
                "prediction, additive across sites (epistasis is not modelled). Shrinking a "
                "buried side chain scores as relief, so read `cavity_risk` alongside the value."
            ),
        },
    )


def liabilities(seq: str) -> dict:
    """Sites worth checking, found by motif regex. A hit is a flag, not a measured liability."""
    seq = seqlib.clean_sequence(seq)
    found: dict[str, list[int]] = {}
    for label, pattern in LIABILITY_MOTIFS.items():
        hits = [m.start() + 1 for m in re.finditer(pattern, seq)]
        if hits:
            found[label] = hits[:20]
    odd_cys = seq.count("C") % 2 == 1
    return {
        "motifs": found,
        "odd_cysteine_count": odd_cys,
        "cysteine_count": seq.count("C"),
        "method": "motif regex scan (sequence only; solvent exposure and pH are not considered)",
        "caveat": (
            "N-glycosylation only occurs in eukaryotic hosts; oxidation/deamidation depend on "
            "exposure and formulation. Cysteine pairing is inferred from parity, not geometry."
        ),
    }


def profile(seq: str, structure: Structure | None = None, mutations: list[dict] | None = None) -> dict:
    """Full developability profile for one design."""
    seq = seqlib.clean_sequence(seq)
    desc = seqlib.descriptors(seq)
    agg = aggregation_propensity(seq)
    sol = solubility(seq)
    imm = immunogenicity(seq)
    ddg = ddg_proxy(seq, mutations or [], structure=structure)
    return {
        "descriptors": desc,
        "aggregation": agg.as_dict(),
        "solubility": sol.as_dict(),
        "immunogenicity": imm.as_dict(),
        "ddg": ddg.as_dict(),
        "liabilities": liabilities(seq),
    }


# --------------------------------------------------------------------------- hard filters

# Screening thresholds. Every number below is a *project policy* gate for triage, not a published
# cut-off: they are tuned so that one bad feature passes and a stack of them does not, and they are
# overridable per campaign. See SKILLS/drug_discovery.md for the evidence table.
DEFAULT_FILTERS = {
    "max_instability_index": 45.0,
    "min_solubility": -0.5,
    "max_aggregation": 0.30,
    "max_immunogenicity": 6.0,
    "reject_odd_cysteine_count": True,  # an odd Cys count leaves one unpaired
    # Solubility collapses when the formulation pH sits on the isoelectric point, so the filter
    # is a keep-out band around the working pH -- not a preference for basic proteins.
    "working_ph": 7.4,
    "min_pi_offset": 1.0,
    "pi_extremes": (3.5, 11.0),
    # Screening cut-off on the uncalibrated destabilization risk score, chosen so that a single
    # large core substitution passes and a stack of them does not. It is not a kcal/mol threshold.
    "max_ddg": 3.0,
}


def apply_filters(prof: dict, config: dict | None = None) -> dict:
    cfg = {**DEFAULT_FILTERS, **(config or {})}
    desc = prof["descriptors"]
    checks: list[dict] = []

    def add(name: str, ok: bool, observed, threshold, reason: str) -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(ok),
                "observed": observed,
                "threshold": threshold,
                "reason": reason,
            }
        )

    add(
        "instability",
        desc["instability_index"] <= cfg["max_instability_index"],
        desc["instability_index"],
        cfg["max_instability_index"],
        f"Guruprasad dipeptide instability index above {cfg['max_instability_index']} suggests a "
        "short in-vivo half-life. Policy gate: the commonly quoted value of 40 is ExPASy ProtParam "
        "convention and the source paper (doi:10.1093/protein/4.2.155) reports no accuracy for it",
    )
    add(
        "solubility",
        prof["solubility"]["value"] >= cfg["min_solubility"],
        prof["solubility"]["value"],
        cfg["min_solubility"],
        "low intrinsic solubility risks inclusion bodies during expression",
    )
    add(
        "aggregation",
        prof["aggregation"]["value"] <= cfg["max_aggregation"],
        prof["aggregation"]["value"],
        cfg["max_aggregation"],
        "too many apolar low-charge windows (aggregation-prone regions)",
    )
    add(
        "immunogenicity",
        prof["immunogenicity"]["value"] <= cfg["max_immunogenicity"],
        prof["immunogenicity"]["value"],
        cfg["max_immunogenicity"],
        "high density of MHC-II-like binding cores",
    )
    pi = desc["isoelectric_point"]
    lo, hi = cfg["pi_extremes"]
    offset = cfg["min_pi_offset"]
    add(
        "isoelectric_point",
        abs(pi - cfg["working_ph"]) >= offset and lo <= pi <= hi,
        pi,
        {"working_ph": cfg["working_ph"], "min_offset": offset, "extremes": [lo, hi]},
        f"pI within {offset} pH unit of the working pH ({cfg['working_ph']}) precipitates during "
        "purification; extreme pI needs a non-standard buffer",
    )
    add(
        "odd_cysteine_count",
        not (cfg["reject_odd_cysteine_count"] and prof["liabilities"]["odd_cysteine_count"]),
        prof["liabilities"]["cysteine_count"],
        "even count",
        "an odd cysteine count leaves a free thiol that drives scrambling and covalent dimers",
    )
    add(
        "stability",
        prof["ddg"]["value"] <= cfg["max_ddg"],
        prof["ddg"]["value"],
        cfg["max_ddg"],
        "destabilization risk score above the screening cut-off (arbitrary units, uncalibrated)",
    )
    failed = [c["name"] for c in checks if not c["passed"]]
    return {
        "passed": not failed,
        "failed": failed,
        "checks": checks,
        "config": dict(cfg.items()),
    }
