# skill.wetlab_metrics (v1.0.0)

Canonical schema for expression, solubility, Tm/DSF/CD, BLI/SPR KD, activity and aggregation: unit
normalisation, documented pass/fail rules and published assay noise.

Module: `backend/app/skills/wetlab_metrics.py` - Tests: `backend/tests/test_skills.py`,
`backend/tests/test_ranking_wetlab.py`, `backend/tests/test_evidence.py`

## Input

| Field | Type | Notes |
| --- | --- | --- |
| `measurements` | object[] | `metric`, `value`, `unit`, optional `assay`; aliases such as `expressed`, `tm`, `kd_nm` normalise to canonical names |
| `strict` | bool | raise instead of skipping unknown metrics |

## Output

`measurements` (canonical name, normalised value, unit, assay, `assay_sd`, `sd_source`,
`sd_calibration`, `rule_basis`, `passed`), `unknown` (aliases the schema does not recognise),
`all_passed`, `metrics`, `citations`.

## Canonical metrics

`expression`, `expression_yield`, `soluble_fraction`, `melting_temperature`, `delta_tm`, `kd`, `kon`,
`koff`, `activity`, `aggregation_hmw`, `purity`, `purification_recovery`. Each carries a unit, a
pass/fail rule, an assay noise sd, and - mandatory - the evidence key behind that sd
(`sd_evidence`) and the basis of the gate (`pass_basis`).

## Two kinds of number

**Assay noise** is anchored on published repeatability where it exists. Intrinsic DSF repeats hen
egg-white lysozyme to Tm = 74.6 C with sd 0.17 C over 6096 microwell measurements; a 22-user
biosensor study reproduced one interaction to ~15% on each rate constant. Both are *best-case,
one-protocol* figures, so what we publish (0.5 C for Tm, 0.3 log10 for KD) is deliberately wider -
a policy widening, and labelled as such.

**Pass/fail rules are Foldsmith gates, not literature cut-offs.** No paper says a design must reach
45 C, 1 mg/L, 30% soluble, <=5% HMW, >=90% purity or <=1000 nM; every rule carries
`pass_basis = "project-policy"` and is overridable per campaign. Two sds (`expression_yield` 30%
relative, `soluble_fraction` 10 points) have no published calibration at all and are labelled
`uncalibrated-proxy`.

## Why it matters

This schema is the contract shared by the predictor, the simulator, pasted CSV/JSON results from a
real lab, and the drift model. Because prediction and measurement land in the same units with the
same assay noise, a residual is meaningful and drift is comparable across versions - and because each
sd states its provenance, a reviewer can tell which residuals are trustworthy.

## Evidence

Machine-readable in `app.skills.wetlab_metrics.EVIDENCE`; `sd_evidence(metric)` returns the record
behind any metric's sd.

| Key | Claim | DOI | Applicability | Known error / reference value | Status |
| --- | --- | --- | --- | --- | --- |
| `wetlab.dsf_repeatability` | DSF Tm is highly repeatable within a plate | [10.1021/acs.molpharmaceut.4c01496](https://doi.org/10.1021/acs.molpharmaceut.4c01496) | hen egg-white lysozyme, 0.5 mg/mL, pH 5.7, intrinsic DSF in microwell plates, 6096 measurements | Tm 74.6 C, sd 0.17 C; 10th-90th percentile spread < 0.5 C; 0.6% outliers | calibrated |
| `wetlab.tm_assay_sd` | `melting_temperature` sd 0.5 C (`delta_tm` 0.7 C) | [10.1021/acs.molpharmaceut.4c01496](https://doi.org/10.1021/acs.molpharmaceut.4c01496) | heterogeneous submissions: different days, instruments, buffers, operators | repeatability floor is 0.17 C; the widening to 0.5 C is our choice, so that drift is not called on an artefact | policy |
| `wetlab.dsf_protocol` | the DSF assay definition follows the standard protocol | [10.1038/nprot.2007.321](https://doi.org/10.1038/nprot.2007.321) | purified protein in a thermal-shift plate format | Tm survives orthogonal comparison; unfolding enthalpy differs 5-10% and heat capacity 30-50% between nanoDSF and DSC ([10.1002/open.202400340](https://doi.org/10.1002/open.202400340)), which is why only Tm enters the schema | anchored |
| `wetlab.biosensor_kinetics` | KD/kon/koff sd = 0.3 log10 | [10.1016/j.ab.2006.01.034](https://doi.org/10.1016/j.ab.2006.01.034) | 1:1 kinetic titration by SPR or BLI; reference study: 22 participants, identical reagents and protocol | ka = (4.1 ± 0.6)e4 M^-1 s^-1, kd = (4.5 ± 0.6)e-5 s^-1, i.e. < 0.1 log10 in KD. 0.3 log10 is our envelope for mixed platforms, surfaces and fits | policy |
| `wetlab.aggregate_quantitation` | aggregation = HMW peak area by analytical SEC | [10.1007/s11095-010-0297-1](https://doi.org/10.1007/s11095-010-0297-1) | protein therapeutics; review of SEC, AUC, light scattering and their biases | method reference only: SEC under-reports large or reversible aggregates, so the 1% sd is a working figure and the 5% gate is ours | anchored |
| `wetlab.production_pipeline` | the expression/solubility/purification stages mirror a standard pipeline | [10.1038/nmeth.f.202](https://doi.org/10.1038/nmeth.f.202) | consensus *E. coli* production strategy distilled from >10,000 structural genomics targets | source of the *stages*, not of any number; it publishes no cross-laboratory sd for yield or densitometric soluble fraction | anchored |
| `wetlab.expression_noise` | `expression_yield` sd 30% relative, `soluble_fraction` 10 points | — | shake-flask expression + IMAC with A280; SDS-PAGE densitometry | **no empirical calibration**: we found no published cross-laboratory repeatability study for either readout; both are order-of-magnitude placeholders | proxy |
| `wetlab.pass_gates` | every pass/fail rule is a Foldsmith gate | — | pre-wetlab triage inside this platform | no literature cut-off exists for "a design worth making"; all gates are policy and overridable, and must never be presented as published acceptance criteria | policy |

## Citations

- Cohrs et al. 2025, *Intrinsic Differential Scanning Fluorimetry for Protein Stability Assessment in
  Microwell Plates* (Mol Pharm) - doi:10.1021/acs.molpharmaceut.4c01496 - lysozyme Tm 74.6 C, sd
  0.17 C over 6096 measurements
- Malicka et al. 2025, *Measuring the Thermal Unfolding of Lysozyme: A Critical Comparison of
  Differential Scanning Fluorimetry and Differential Scanning Calorimetry* (ChemistryOpen) -
  doi:10.1002/open.202400340 - Tm robust; enthalpy 5-10%, heat capacity 30-50% method dependence
- Niesen, Berglund & Vedadi 2007, *The use of differential scanning fluorimetry to detect ligand
  interactions that promote protein stability* (Nat Protoc 2:2212) - doi:10.1038/nprot.2007.321
- Katsamba et al. 2006, *Kinetic analysis of a high-affinity antibody/antigen interaction performed by
  multiple Biacore users* (Anal Biochem 352:208) - doi:10.1016/j.ab.2006.01.034
- den Engelsman et al. 2011, *Strategies for the assessment of protein aggregates in pharmaceutical
  biotech product development* (Pharm Res 28:920) - doi:10.1007/s11095-010-0297-1
- Structural Genomics Consortium et al. 2008, *Protein production and purification* (Nat Methods
  5:135) - doi:10.1038/nmeth.f.202
