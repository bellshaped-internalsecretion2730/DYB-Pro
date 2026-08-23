"""skill.wetlab_metrics — the canonical measurement vocabulary of the wet-lab loop.

Everything the lab (or the simulator) reports enters Foldsmith through this skill: it normalises
units, applies documented pass/fail rules, and publishes the measurement-noise model that the
simulator samples from and that the drift calculation divides by.

Two different kinds of number live in :data:`CANONICAL`, and every entry says which it is:

**Assay noise (``assay_sd``)** is anchored on published repeatability where such data exists:

* intrinsic DSF repeats hen egg-white lysozyme to Tm = 74.6 C with sd 0.17 C over 6096 microwell
  measurements at 0.5 mg/mL, pH 5.7, with a 10th-90th percentile spread below 0.5 C and 0.6%
  outliers (Cohrs et al. 2025, doi:10.1021/acs.molpharmaceut.4c01496). That is *within-plate*
  repeatability under one protocol, so the 0.5 C we publish is a deliberately wider inter-day,
  inter-instrument figure - a policy widening, not the paper's number;
* in a 22-participant biosensor study using identical reagents and protocol, ka and kd each spread
  by ~15% between users (ka = (4.1 +- 0.6)e4 M-1 s-1, kd = (4.5 +- 0.6)e-5 s-1;
  doi:10.1016/j.ab.2006.01.034), i.e. under 0.1 log10. Real submissions mix SPR and BLI, different
  surfaces and different fits, so 0.3 log10 is again a conservative policy envelope;
* shake-flask expression yield has no published cross-lab repeatability figure we could find, so
  its 30% coefficient of variation is an explicitly uncalibrated placeholder.

**Pass/fail rules (``pass``)** are *project gates*, not literature cutoffs. No paper states that a
design must reach 45 C or 1 mg/L; Foldsmith does, and each entry carries ``pass_basis`` saying so.
Where the practice is grounded in a review of methods - aggregate quantitation
(doi:10.1007/s11095-010-0297-1), the consensus production pipeline behind >10,000 structural
genomics targets (doi:10.1038/nmeth.f.202) - the review is cited for the *method*, never as the
source of the number.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from app.skills import evidence
from app.skills.base import Metric, SkillError, SkillSpec, collect, register

VERSION = "1.0.0"

EVIDENCE = evidence.register(
    "skill.wetlab_metrics",
    (
        evidence.Evidence(
            key="wetlab.dsf_repeatability",
            claim="DSF melting temperature is highly repeatable within one plate",
            applicability="hen egg-white lysozyme, 0.5 mg/mL, pH 5.7, intrinsic (label-free) DSF in "
                          "microwell plates; 6096 measurements",
            error="Tm = 74.6 C with sd 0.17 C; 10th-90th percentile spread below 0.5 C; 0.6% outliers",
            calibration=evidence.CALIBRATED,
            source="Cohrs et al. 2025, Intrinsic Differential Scanning Fluorimetry for Protein "
                   "Stability Assessment in Microwell Plates (Mol Pharm)",
            doi="10.1021/acs.molpharmaceut.4c01496",
            reference_value="lysozyme Tm 74.6 C, sd 0.17 C over 6096 measurements",
        ),
        evidence.Evidence(
            key="wetlab.tm_assay_sd",
            claim="melting_temperature is published with an assay sd of 0.5 C (delta_tm 0.7 C)",
            applicability="heterogeneous submissions: SYPRO Orange or intrinsic DSF, different days, "
                          "instruments, buffers and operators",
            error="within-plate repeatability is 0.17 C (wetlab.dsf_repeatability); we publish 0.5 C "
                  "because inter-day and inter-instrument reproducibility is worse than repeatability "
                  "and we would rather over-state noise than call drift on an artefact. The exact "
                  "widening factor is a project choice, not a measurement",
            calibration=evidence.POLICY,
            source="Cohrs et al. 2025 (Mol Pharm) for the repeatability floor; Niesen, Berglund & "
                   "Vedadi 2007, DSF protocol (Nat Protoc 2:2212, doi:10.1038/nprot.2007.321)",
            doi="10.1021/acs.molpharmaceut.4c01496",
        ),
        evidence.Evidence(
            key="wetlab.dsf_protocol",
            claim="the DSF assay definition (first derivative of the unfolding curve) follows the "
                  "standard protocol",
            applicability="purified protein in a thermal-shift plate format, with or without ligand",
            error="protocol reference; Tm is the parameter that survives orthogonal comparison, whereas "
                  "unfolding enthalpy differs by 5-10% and heat capacity by 30-50% between nanoDSF and "
                  "DSC (doi:10.1002/open.202400340) - which is why only Tm enters the schema",
            calibration=evidence.ANCHORED,
            source="Niesen, Berglund & Vedadi 2007, The use of differential scanning fluorimetry to "
                   "detect ligand interactions that promote protein stability (Nat Protoc 2:2212)",
            doi="10.1038/nprot.2007.321",
        ),
        evidence.Evidence(
            key="wetlab.biosensor_kinetics",
            claim="KD/kon/koff are reported with a 0.3 log10 assay sd",
            applicability="1:1 kinetic titration by SPR or BLI. The reference study had 22 participants "
                          "measuring one interaction with identical reagents and protocol",
            error="between-user spread in that standardised study was ~15% on each rate constant "
                  "(ka = (4.1 +- 0.6)e4 M-1 s-1, kd = (4.5 +- 0.6)e-5 s-1), i.e. below 0.1 log10 in KD. "
                  "0.3 log10 is a project envelope covering mixed platforms, surface chemistries and "
                  "fitting choices, and is therefore policy rather than a measured sd",
            calibration=evidence.POLICY,
            source="Katsamba et al. 2006, Kinetic analysis of a high-affinity antibody/antigen "
                   "interaction performed by multiple Biacore users (Anal Biochem 352:208)",
            doi="10.1016/j.ab.2006.01.034",
            reference_value="ka (4.1 +- 0.6)e4 M-1 s-1 and kd (4.5 +- 0.6)e-5 s-1 across 22 users "
                            "=> KD ~ 1.1 nM",
        ),
        evidence.Evidence(
            key="wetlab.aggregate_quantitation",
            claim="aggregation is quantified as the high-molecular-weight peak area by analytical SEC",
            applicability="protein therapeutics; the review surveys SEC, AUC, light scattering and their "
                          "biases",
            error="method reference only. SEC under-reports large or reversible aggregates, so the 1% "
                  "absolute sd we publish is a working figure and the 5% pass gate is a project "
                  "threshold, not a specification from this source",
            calibration=evidence.ANCHORED,
            source="den Engelsman et al. 2011, Strategies for the assessment of protein aggregates in "
                   "pharmaceutical biotech product development (Pharm Res 28:920)",
            doi="10.1007/s11095-010-0297-1",
        ),
        evidence.Evidence(
            key="wetlab.production_pipeline",
            claim="the expression/solubility/purification metric set mirrors a standard production "
                  "pipeline",
            applicability="consensus E. coli production strategy distilled from more than 10,000 "
                          "structural genomics targets",
            error="the pipeline is the source of the *stages we measure*, not of any number: no "
                  "cross-laboratory sd for shake-flask yield or densitometric soluble fraction is "
                  "published there, so those sds remain uncalibrated placeholders",
            calibration=evidence.ANCHORED,
            source="Structural Genomics Consortium et al. 2008, Protein production and purification "
                   "(Nat Methods 5:135)",
            doi="10.1038/nmeth.f.202",
            reference_value="consensus strategy derived from >10,000 proteins",
        ),
        evidence.Evidence(
            key="wetlab.expression_noise",
            claim="expression_yield carries a 30% relative sd and soluble_fraction 10 percentage points",
            applicability="shake-flask expression with IMAC purification and A280 quantification; "
                          "SDS-PAGE densitometry for the soluble fraction",
            error="no empirical calibration: we found no published cross-laboratory repeatability study "
                  "for either readout, so both figures are order-of-magnitude placeholders that make "
                  "the noise model usable and are flagged as uncalibrated wherever they are consumed",
            calibration=evidence.PROXY,
        ),
        evidence.Evidence(
            key="wetlab.pass_gates",
            claim="every pass/fail rule in the canonical schema is a Foldsmith gate",
            applicability="pre-wetlab triage of designed constructs inside this platform",
            error="no literature cutoff exists for 'a design worth making': the >=1 mg/L, >=30% soluble, "
                  ">=45 C, <=5% HMW, >=90% purity, >=25% recovery, >=50% activity and <=1000 nM gates "
                  "are project policy, are overridable per campaign, and must never be presented as "
                  "published acceptance criteria",
            calibration=evidence.POLICY,
        ),
    ),
)

CITATIONS = evidence.citations("skill.wetlab_metrics")


def cite(*keys: str) -> tuple[str, ...]:
    return tuple(evidence.record("skill.wetlab_metrics", key).citation() for key in keys)


# Literature reference values pinned by tests/test_evidence.py.
DSF_LYSOZYME_TM_C = 74.6
DSF_WITHIN_PLATE_TM_SD_C = 0.17
DSF_LYSOZYME_REPLICATES = 6096
SPR_REFERENCE_KON_M1S1 = 4.1e4
SPR_REFERENCE_KOFF_S1 = 4.5e-5
SPR_REFERENCE_USERS = 22

# name -> canonical unit, higher_is_better, assay sd + its evidence, pass rule + its basis.
# `sd_evidence` and `pass_basis` are mandatory: tests/test_evidence.py rejects a metric that cannot
# say where its noise figure came from or that its gate is a project policy.
CANONICAL: dict[str, dict[str, Any]] = {
    "expression": {
        "unit": "bool", "higher_is_better": True, "assay_sd": 0.0, "sd_kind": "bernoulli",
        "pass": {"equals": True}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.production_pipeline",
        "assay": "small-scale expression test (SDS-PAGE / dot blot)",
    },
    "expression_yield": {
        "unit": "mg/L", "higher_is_better": True, "assay_sd": 0.30, "sd_kind": "relative",
        "pass": {"min": 1.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.expression_noise",
        "assay": "shake-flask expression and IMAC purification, A280 quantification",
    },
    "soluble_fraction": {
        "unit": "%", "higher_is_better": True, "assay_sd": 10.0, "sd_kind": "absolute",
        "pass": {"min": 30.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.expression_noise",
        "assay": "soluble vs total lysate fraction by SDS-PAGE densitometry",
    },
    "melting_temperature": {
        "unit": "C", "higher_is_better": True, "assay_sd": 0.5, "sd_kind": "absolute",
        "pass": {"min": 45.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.tm_assay_sd",
        "assay": "differential scanning fluorimetry (SYPRO Orange or intrinsic)",
    },
    "delta_tm": {
        "unit": "C", "higher_is_better": True, "assay_sd": 0.7, "sd_kind": "absolute",
        "pass": {"min": 0.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.tm_assay_sd",
        "assay": "DSF of design vs parent on the same plate",
    },
    "kd": {
        "unit": "nM", "higher_is_better": False, "assay_sd": 0.3, "sd_kind": "log10",
        "pass": {"max": 1000.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.biosensor_kinetics",
        "assay": "BLI or SPR kinetic titration, 1:1 Langmuir fit",
    },
    "kon": {
        "unit": "1/M/s", "higher_is_better": True, "assay_sd": 0.3, "sd_kind": "log10",
        "pass": {}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.biosensor_kinetics",
        "assay": "BLI/SPR association phase fit",
    },
    "koff": {
        "unit": "1/s", "higher_is_better": False, "assay_sd": 0.3, "sd_kind": "log10",
        "pass": {}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.biosensor_kinetics",
        "assay": "BLI/SPR dissociation phase fit",
    },
    "activity": {
        "unit": "% of control", "higher_is_better": True, "assay_sd": 15.0, "sd_kind": "absolute",
        "pass": {"min": 50.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.expression_noise",
        "assay": "functional assay normalised to the wild-type control",
    },
    "aggregation_hmw": {
        "unit": "%", "higher_is_better": False, "assay_sd": 1.0, "sd_kind": "absolute",
        "pass": {"max": 5.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.aggregate_quantitation",
        "assay": "analytical SEC, high-molecular-weight peak area",
    },
    "purity": {
        "unit": "%", "higher_is_better": True, "assay_sd": 3.0, "sd_kind": "absolute",
        "pass": {"min": 90.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.aggregate_quantitation",
        "assay": "SDS-PAGE or analytical SEC main-peak purity",
    },
    "purification_recovery": {
        "unit": "%", "higher_is_better": True, "assay_sd": 12.0, "sd_kind": "absolute",
        "pass": {"min": 25.0}, "pass_basis": evidence.POLICY,
        "sd_evidence": "wetlab.production_pipeline",
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
    sd_source: str = Field(default="", description="evidence key behind assay_sd")
    sd_calibration: str = Field(default="", description="calibration status of assay_sd")
    rule_basis: str = Field(default="", description="basis of the pass/fail rule; gates are policy")
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


def sd_evidence(metric: str) -> evidence.Evidence:
    """The evidence record behind this metric's assay sd."""
    spec = CANONICAL[canonical_name(metric)]
    return evidence.record("skill.wetlab_metrics", spec["sd_evidence"])


def noise_sd(metric: str, value: float | None = None) -> float:
    """Assay standard deviation in canonical units for this metric at this magnitude.

    The figures come from :data:`CANONICAL`; use :func:`sd_evidence` to find out whether a given one
    is literature-calibrated, a conservative policy widening, or an uncalibrated placeholder.
    """
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
                sd_source=spec["sd_evidence"],
                sd_calibration=sd_evidence(name).calibration,
                rule_basis=spec["pass_basis"],
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
                   citations=cite("wetlab.pass_gates", "wetlab.production_pipeline"),
                   notes="the gates are Foldsmith policy, overridable per campaign; they are not "
                         "published acceptance criteria"),
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
