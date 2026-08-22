"""skill.wetlab_metrics — the canonical measurement vocabulary of the wet-lab loop.

Everything the lab (or the simulator) reports enters Foldsmith through this skill: it normalises
units, applies documented pass/fail rules, and publishes the measurement-noise model that the
simulator samples from and that the drift calculation divides by.

Assay noise is taken from published repeatability data rather than invented:

* DSF melting temperature repeats to sd ~= 0.17 C within a plate (Hartmann et al. 2025, 6096
  replicates of lysozyme), so 0.5 C is a fair inter-day sd;
* the same protein-protein interaction measured by biosensor across laboratories spreads by
  roughly half a log unit in KD (ABRF-MIRG'02 study), so 0.3 log10 is a fair assay sd;
* shake-flask expression yield is medium/strain dependent at the tens-of-percent level, so a 30%
  coefficient of variation is used.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from app.skills.base import Metric, SkillError, SkillSpec, collect, register

VERSION = "1.0.0"

CITATIONS = [
    "Hartmann et al. 2025, Intrinsic differential scanning fluorimetry for protein stability "
    "assessment in microwell plates (Anal Chem; PMC11881137) — Tm sd 0.17 C over 6096 replicates",
    "ABRF-MIRG'02 study 2003, Assembly state, thermodynamic and kinetic analysis of an "
    "enzyme/inhibitor interaction (J Biomol Tech 14:247) — inter-laboratory KD spread",
    "Todd et al. 2005, The structural genomics experimental pipeline (J Mol Biol 348:1235) — "
    "~45% stage-wise success from cloning through purification",
    "Niesen, Berglund & Vedadi 2007, The use of differential scanning fluorimetry to detect "
    "ligand interactions that promote protein stability (Nat Protoc 2:2212)",
    "Ritchie et al. 2013, Analysis of size exclusion chromatography for aggregate quantitation "
    "in therapeutic proteins (Bioanalysis) — HMW species acceptance practice",
]

# name -> (canonical unit, higher_is_better, assay sd, sd kind, pass rule)
CANONICAL: dict[str, dict[str, Any]] = {
    "expression": {
        "unit": "bool", "higher_is_better": True, "assay_sd": 0.0, "sd_kind": "bernoulli",
        "pass": {"equals": True},
        "assay": "small-scale expression test (SDS-PAGE / dot blot)",
    },
    "expression_yield": {
        "unit": "mg/L", "higher_is_better": True, "assay_sd": 0.30, "sd_kind": "relative",
        "pass": {"min": 1.0},
        "assay": "shake-flask expression and IMAC purification, A280 quantification",
    },
    "soluble_fraction": {
        "unit": "%", "higher_is_better": True, "assay_sd": 10.0, "sd_kind": "absolute",
        "pass": {"min": 30.0},
        "assay": "soluble vs total lysate fraction by SDS-PAGE densitometry",
    },
    "melting_temperature": {
        "unit": "C", "higher_is_better": True, "assay_sd": 0.5, "sd_kind": "absolute",
        "pass": {"min": 45.0},
        "assay": "differential scanning fluorimetry (SYPRO Orange or intrinsic)",
    },
    "delta_tm": {
        "unit": "C", "higher_is_better": True, "assay_sd": 0.7, "sd_kind": "absolute",
        "pass": {"min": 0.0},
        "assay": "DSF of design vs parent on the same plate",
    },
    "kd": {
        "unit": "nM", "higher_is_better": False, "assay_sd": 0.3, "sd_kind": "log10",
        "pass": {"max": 1000.0},
        "assay": "BLI or SPR kinetic titration, 1:1 Langmuir fit",
    },
    "kon": {
        "unit": "1/M/s", "higher_is_better": True, "assay_sd": 0.3, "sd_kind": "log10",
        "pass": {}, "assay": "BLI/SPR association phase fit",
    },
    "koff": {
        "unit": "1/s", "higher_is_better": False, "assay_sd": 0.3, "sd_kind": "log10",
        "pass": {}, "assay": "BLI/SPR dissociation phase fit",
    },
    "activity": {
        "unit": "% of control", "higher_is_better": True, "assay_sd": 15.0, "sd_kind": "absolute",
        "pass": {"min": 50.0},
        "assay": "functional assay normalised to the wild-type control",
    },
    "aggregation_hmw": {
        "unit": "%", "higher_is_better": False, "assay_sd": 1.0, "sd_kind": "absolute",
        "pass": {"max": 5.0},
        "assay": "analytical SEC, high-molecular-weight peak area",
    },
    "purity": {
        "unit": "%", "higher_is_better": True, "assay_sd": 3.0, "sd_kind": "absolute",
        "pass": {"min": 90.0},
        "assay": "SDS-PAGE or analytical SEC main-peak purity",
    },
    "purification_recovery": {
        "unit": "%", "higher_is_better": True, "assay_sd": 12.0, "sd_kind": "absolute",
        "pass": {"min": 25.0},
        "assay": "recovered mass / lysate mass across the purification train",
    },
}

# Aliases the lab actually types in a CSV.
ALIASES = {
    "tm": "melting_temperature", "tm_c": "melting_temperature", "melting_temp": "melting_temperature",
    "dtm": "delta_tm", "delta_tm_c": "delta_tm", "ddtm": "delta_tm",
    "kd_nm": "kd", "k_d": "kd", "affinity": "kd",
    "yield": "expression_yield", "yield_mg_l": "expression_yield", "titer": "expression_yield",
    "expressed": "expression", "expression_yes_no": "expression",
    "soluble": "soluble_fraction", "solubility": "soluble_fraction",
    "hmw": "aggregation_hmw", "aggregate": "aggregation_hmw", "aggregation": "aggregation_hmw",
    "specific_activity": "activity", "activity_pct": "activity",
    "recovery": "purification_recovery",
}

UNIT_FACTORS: dict[str, dict[str, float]] = {
    "mg/L": {"mg/l": 1.0, "mg/ml": 1000.0, "ug/ml": 1.0, "µg/ml": 1.0, "g/l": 1000.0,
             "ug/l": 0.001, "mg/liter": 1.0},
    "nM": {"m": 1e9, "mm": 1e6, "um": 1e3, "µm": 1e3, "μm": 1e3, "nm": 1.0, "pm": 1e-3, "fm": 1e-6},
    "%": {"%": 1.0, "percent": 1.0, "fraction": 100.0, "ratio": 100.0},
    "1/M/s": {"1/m/s": 1.0, "m-1s-1": 1.0, "1/ms": 1.0},
    "1/s": {"1/s": 1.0, "s-1": 1.0},
    "% of control": {"%": 1.0, "percent": 1.0, "fraction": 100.0, "% of control": 1.0},
}

TRUTHY = {"true", "yes", "y", "1", "soluble", "expressed", "pass", "positive"}
FALSY = {"false", "no", "n", "0", "insoluble", "not expressed", "fail", "negative", "none"}

# Planning-only cost/duration defaults. These are order-of-magnitude figures for ranking
# experiments, not quotes; every campaign can override them.
ASSAY_CATALOG: dict[str, dict[str, Any]] = {
    "expression_screen": {"measures": ["expression", "expression_yield", "soluble_fraction"],
                          "cost_usd": 180.0, "days": 4,
                          "readout": "SDS-PAGE band + A280 after IMAC"},
    "dsf": {"measures": ["melting_temperature", "delta_tm"], "cost_usd": 240.0, "days": 2,
            "readout": "first derivative of the unfolding curve"},
    "sec": {"measures": ["aggregation_hmw", "purity"], "cost_usd": 200.0, "days": 1,
            "readout": "HMW peak area fraction"},
    "bli": {"measures": ["kd", "kon", "koff"], "cost_usd": 750.0, "days": 3,
            "readout": "1:1 Langmuir kinetic fit"},
    "activity": {"measures": ["activity"], "cost_usd": 420.0, "days": 3,
                 "readout": "signal normalised to wild-type control"},
}


class RawMeasurement(BaseModel):
    metric: str
    value: float | bool | str | None
    unit: str | None = None
    label: str = Field(default="", description="which construct/design was measured")
    replicate_sd: float | None = None
    notes: str = ""


class WetlabMetricsInput(BaseModel):
    measurements: list[RawMeasurement] = Field(default_factory=list)
    strict: bool = Field(default=False, description="raise instead of skipping unknown metrics")


class NormalizedMeasurement(BaseModel):
    metric: str
    label: str = ""
    value: float | bool | None
    unit: str
    assay: str
    assay_sd: float
    sd_kind: str
    replicate_sd: float | None = None
    passed: bool | None = None
    rule: str = ""
    notes: str = ""
    original: dict = Field(default_factory=dict)


class WetlabMetricsOutput(BaseModel):
    measurements: list[NormalizedMeasurement] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    all_passed: bool = True
    metrics: dict[str, dict] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)


def canonical_name(metric: str) -> str | None:
    key = (metric or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in CANONICAL:
        return key
    return ALIASES.get(key)


def schema_for(metric: str) -> dict:
    name = canonical_name(metric)
    if not name:
        raise SkillError(f"'{metric}' is not part of the canonical wet-lab vocabulary")
    return {"name": name, **CANONICAL[name]}


def convert(metric: str, value: float | bool | str | None, unit: str | None) -> float | bool | None:
    """Convert a reported value into the canonical unit for that metric."""
    name = canonical_name(metric)
    if not name:
        raise SkillError(f"unknown metric '{metric}'")
    spec = CANONICAL[name]
    if spec["unit"] == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in TRUTHY:
            return True
        if text in FALSY:
            return False
        raise SkillError(f"cannot read '{value}' as a yes/no for {name}")
    if value is None or value == "":
        return None
    number = float(value)
    given = (unit or spec["unit"]).strip().lower()
    factors = UNIT_FACTORS.get(spec["unit"], {})
    if given in {spec["unit"].lower(), ""}:
        factor = 1.0
    elif given in factors:
        factor = factors[given]
    else:
        raise SkillError(f"unit '{unit}' is not convertible to {spec['unit']} for {name}")
    # a fraction reported as 0-1 for a percentage metric is a common paste error
    if spec["unit"] in {"%", "% of control"} and factor == 1.0 and 0.0 <= number <= 1.0:
        factor = 100.0
    return round(number * factor, 6)


def evaluate(metric: str, value: float | bool | None) -> tuple[bool | None, str]:
    spec = CANONICAL[canonical_name(metric)]
    rule = spec["pass"]
    if not rule or value is None:
        return None, "no acceptance rule defined" if not rule else "no value"
    if "equals" in rule:
        return bool(value) == rule["equals"], f"must equal {rule['equals']}"
    if "min" in rule:
        return float(value) >= rule["min"], f">= {rule['min']} {spec['unit']}"
    if "max" in rule:
        return float(value) <= rule["max"], f"<= {rule['max']} {spec['unit']}"
    return None, "no acceptance rule defined"


def noise_sd(metric: str, value: float | None = None) -> float:
    """Assay standard deviation in canonical units for this metric at this magnitude."""
    spec = CANONICAL[canonical_name(metric)]
    kind, sd = spec["sd_kind"], float(spec["assay_sd"])
    if kind == "absolute":
        return sd
    if kind == "relative":
        return abs(float(value or 0.0)) * sd
    if kind == "log10":
        magnitude = abs(float(value or 0.0))
        if magnitude <= 0:
            return 0.0
        # convert a log10 spread into a local linear sd
        return magnitude * (10 ** sd - 1.0) / 2.0
    return 0.0


def z_score(metric: str, predicted: float, measured: float, predicted_sd: float = 0.0) -> float:
    """Drift in units of combined prediction + assay uncertainty."""
    total = math.sqrt(predicted_sd**2 + noise_sd(metric, measured) ** 2)
    if total <= 1e-9:
        return 0.0
    return round((measured - predicted) / total, 4)


def run(payload: WetlabMetricsInput) -> WetlabMetricsOutput:
    rows: list[NormalizedMeasurement] = []
    unknown: list[str] = []
    for raw in payload.measurements:
        name = canonical_name(raw.metric)
        if not name:
            if payload.strict:
                raise SkillError(f"unknown metric '{raw.metric}'")
            unknown.append(raw.metric)
            continue
        spec = CANONICAL[name]
        value = convert(name, raw.value, raw.unit)
        passed, rule = evaluate(name, value)
        rows.append(
            NormalizedMeasurement(
                metric=name,
                label=raw.label,
                value=value,
                unit=spec["unit"],
                assay=spec["assay"],
                assay_sd=round(noise_sd(name, value if isinstance(value, int | float) else None), 4),
                sd_kind=spec["sd_kind"],
                replicate_sd=raw.replicate_sd,
                passed=passed,
                rule=rule,
                notes=raw.notes,
                original={"metric": raw.metric, "value": raw.value, "unit": raw.unit},
            )
        )
    decided = [r for r in rows if r.passed is not None]
    metrics = collect(
        [
            Metric("measurements_normalized", float(len(rows)), "count",
                   "canonical vocabulary mapping with unit conversion", "skill.wetlab_metrics"),
            Metric("acceptance_rate", round(sum(1 for r in decided if r.passed) / len(decided), 4)
                   if decided else 0.0, "fraction",
                   "documented pass/fail rules per metric", "skill.wetlab_metrics",
                   citations=(CITATIONS[2],)),
        ]
    )
    return WetlabMetricsOutput(
        measurements=rows,
        unknown=unknown,
        all_passed=all(r.passed for r in decided) if decided else True,
        metrics=metrics,
        citations=CITATIONS,
    )


SKILL = register(
    SkillSpec(
        name="skill.wetlab_metrics",
        version=VERSION,
        summary=(
            "Canonical schema for expression, solubility, Tm/DSF, BLI/SPR KD, activity and "
            "aggregation: unit normalisation, documented pass/fail rules and published assay noise."
        ),
        input_model=WetlabMetricsInput,
        output_model=WetlabMetricsOutput,
        runner=run,
        citations=CITATIONS,
    )
)
