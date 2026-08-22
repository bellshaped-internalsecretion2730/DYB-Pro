# skill.wetlab_metrics (v1.0.0)

Canonical schema for expression, solubility, Tm/DSF/CD, BLI/SPR KD, activity and aggregation: unit
normalisation, documented pass/fail rules and published assay noise.

Module: `backend/app/skills/wetlab_metrics.py` - Tests: `backend/tests/test_skills.py`

## Input

| Field | Type | Notes |
| --- | --- | --- |
| `measurements` | object[] | `metric`, `value`, `unit`, optional `assay`; aliases such as `expressed`, `tm`, `kd_nm` normalise to canonical names |
| `strict` | bool | raise instead of skipping unknown metrics |

## Output

`measurements` (canonical name, SI-normalised value, unit, assay, `passed`), `unknown` (aliases the
schema does not recognise), `all_passed`, `metrics`, `citations`.

## Canonical metrics

`expression`, `yield_mg_per_l`, `soluble_fraction`, `melting_temperature`, `kd`, `kon`, `koff`,
`activity`, `aggregation_hmw`, `purification_recovery`. Each carries a unit, a pass/fail rule and an
assay noise sd taken from the literature - e.g. DSF Tm sd 0.17 C across 6096 replicates, and the
inter-laboratory KD spread from the ABRF-MIRG'02 study.

## Why it matters

This schema is the contract shared by the predictor, the simulator, pasted CSV/JSON results from a
real lab, and the drift model. Because prediction and measurement land in the same units with the
same assay noise, a residual is meaningful and drift is comparable across versions.

## Citations

- Hartmann et al. 2025, *Intrinsic differential scanning fluorimetry for protein stability
  assessment in microwell plates* (Anal Chem; PMC11881137) - Tm sd 0.17 C over 6096 replicates
- ABRF-MIRG'02 study 2003, *Assembly state, thermodynamic and kinetic analysis of an
  enzyme/inhibitor interaction* (J Biomol Tech 14:247) - inter-laboratory KD spread
- Todd et al. 2005, *The structural genomics experimental pipeline* (J Mol Biol 348:1235) - ~45%
  stage-wise success from cloning through purification
- Niesen, Berglund & Vedadi 2007, *The use of differential scanning fluorimetry to detect ligand
  interactions that promote protein stability* (Nat Protoc 2:2212)
- Ritchie et al. 2013, *Analysis of size exclusion chromatography for aggregate quantitation in
  therapeutic proteins* (Bioanalysis) - HMW species acceptance practice
