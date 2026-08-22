"""Developability: stability, solubility, aggregation, immunogenicity, liabilities, filters.

These are independent implementations of published heuristics (see CITATIONS.md). They are
deliberately transparent and deterministic: each returns the value, the method name and the
inputs that drove it, so a scientist can audit "why this score".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.toolkit import sequence as seqlib
from app.toolkit.constants import CHARGE_AT_PH7, HYDROPATHY
from app.toolkit.structure import Structure, relative_exposure

# Liability motifs (regexes on the protein sequence).
LIABILITY_MOTIFS = {
    "n_glycosylation": r"N[^P][ST]",
    "deamidation_NG": r"N[GS]",
    "isomerization_DG": r"D[GS]",
    "oxidation_MW": r"[MW]",
    "protease_KK_RR": r"(KK|RR|KR|RK)",
    "unpaired_cysteine": r"C",
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
    Vendruscolo, 2008): contiguous apolar stretches with low net charge drive aggregation.
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
    """CamSol-like intrinsic solubility proxy from charge, hydropathy and aggregation load.

    Positive = more soluble. Combines net charge magnitude (favourable), GRAVY (unfavourable
    when hydrophobic) and the aggregation-prone fraction.
    """
    seq = seqlib.clean_sequence(seq)
    charge = abs(seqlib.net_charge(seq)) / max(1, len(seq)) * 100.0
    gravy = seqlib.gravy(seq)
    agg = aggregation_propensity(seq).value
    value = round(0.45 * charge - 0.55 * gravy - 1.6 * agg, 4)
    return Metric(
        name="solubility",
        value=value,
        method="charge/GRAVY/aggregation composite (CamSol-like)",
        detail={"charge_density": round(charge, 3), "gravy": gravy, "aggregation": agg},
    )


def immunogenicity(seq: str) -> Metric:
    """MHC-II-like risk: count of 9-mers with hydrophobic P1/P4/P6/P9 anchor pattern.

    Anchor-motif scanning as in MHC-II binding-core heuristics; reported as risk per 100 aa.
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


def ddg_proxy(
    wt_seq: str,
    mutations: list[dict],
    structure: Structure | None = None,
) -> Metric:
    """Folding-stability change proxy (kcal/mol-like, negative = stabilizing).

    Combines a BLOSUM62 substitution penalty, side-chain volume change weighted by burial, and
    a helix-propensity term — the ingredients used by classical empirical ΔΔG estimators.
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
        sub_pen = -0.25 * seqlib.sub_score(wt, mt)
        vol_pen = abs(VOLUME[mt] - VOLUME[wt]) / 100.0 * (0.8 + 1.2 * buried)
        polarity_pen = 0.0
        if buried > 0.6 and HYDROPATHY[mt] < -1.0 <= HYDROPATHY[wt]:
            polarity_pen = 0.9  # burying a polar/charged residue
        contrib = round(sub_pen + vol_pen + polarity_pen, 3)
        total += contrib
        per_mut.append(
            {
                "mutation": f"{wt}{pos}{mt}",
                "ddg": contrib,
                "burial": round(buried, 3),
                "substitution_penalty": round(sub_pen, 3),
                "volume_penalty": round(vol_pen, 3),
                "polarity_penalty": polarity_pen,
            }
        )
    return Metric(
        name="ddg_proxy",
        value=round(total, 3),
        unit="kcal/mol (proxy)",
        method="BLOSUM62 + burial-weighted volume/polarity terms",
        detail={"per_mutation": per_mut, "structure_aware": structure is not None},
    )


def liabilities(seq: str) -> dict:
    seq = seqlib.clean_sequence(seq)
    found: dict[str, list[int]] = {}
    for label, pattern in LIABILITY_MOTIFS.items():
        hits = [m.start() + 1 for m in re.finditer(pattern, seq)]
        if hits:
            found[label] = hits[:20]
    free_cys = seq.count("C") % 2 == 1
    return {
        "motifs": found,
        "free_cysteine": free_cys,
        "cysteine_count": seq.count("C"),
        "method": "motif regex scan (N-glyc, deamidation, isomerization, oxidation, protease)",
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

DEFAULT_FILTERS = {
    "max_instability_index": 45.0,
    "min_solubility": -0.5,
    "max_aggregation": 0.30,
    "max_immunogenicity": 6.0,
    "max_free_cysteine": True,  # a single unpaired Cys is a fail
    # Solubility collapses when the formulation pH sits on the isoelectric point, so the filter
    # is a keep-out band around the working pH -- not a preference for basic proteins.
    "working_ph": 7.4,
    "min_pi_offset": 1.0,
    "pi_extremes": (3.5, 11.0),
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
        "Guruprasad instability index above 40 predicts a short in-vivo half-life",
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
        "free_cysteine",
        not (cfg["max_free_cysteine"] and prof["liabilities"]["free_cysteine"]),
        prof["liabilities"]["cysteine_count"],
        "even count",
        "an unpaired cysteine drives scrambling and covalent dimers",
    )
    add(
        "stability",
        prof["ddg"]["value"] <= cfg["max_ddg"],
        prof["ddg"]["value"],
        cfg["max_ddg"],
        "predicted destabilization beyond the tolerated window",
    )
    failed = [c["name"] for c in checks if not c["passed"]]
    return {
        "passed": not failed,
        "failed": failed,
        "checks": checks,
        "config": dict(cfg.items()),
    }
