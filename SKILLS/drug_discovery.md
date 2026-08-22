# skill.drug_discovery (v1.0.0)

Developability index with immunogenicity flags and an explicit affinity-vs-developability Pareto
trade-off per candidate.

Module: `backend/app/skills/drug_discovery.py` - Tests: `backend/tests/test_skills.py`

## Input

| Field | Type | Notes |
| --- | --- | --- |
| `candidates` | object[] (required) | `label`, `sequence`, `mutations`, `affinity_score` (higher is better, e.g. `-binding_score` from `skill.physics`), `affinity_sd` |
| `working_ph` | number | formulation pH handed to the chemistry filters |
| `max_immunogenicity` | number | MHC-II-like cores per 100 aa allowed before flagging |

## Output

Per candidate: `developability_index`, `developability_sd`, `affinity_score`, `passed_filters`,
`failed_filters`, `immunogenicity_flags`, `risk_flags`, `pareto_optimal`, `tradeoff`, `components`.
Plus campaign-level `metrics` and `citations`.

## Behaviour

`developability_sd` is a real uncertainty, and the ranking layer consumes it: a candidate is only
promoted over another when the difference survives its own error bar. `pareto_optimal` and
`tradeoff` are what let the UI answer *why this, not that* - the losing candidate is named together
with the axis it lost on.

## Citations

- Jain et al. 2017, *Biophysical properties of the clinical-stage antibody landscape* (PNAS 114:944)
- Raybould et al. 2019, *Five computational developability guidelines for therapeutic antibody
  profiling* (PNAS 116:4025)
- Hon et al. 2021, *SoluProt: prediction of soluble protein expression in Escherichia coli*
  (Bioinformatics 37:23) - sequence-only solubility prediction reaches ~58.5% accuracy, AUC 0.62
- Sormanni, Aprile & Vendruscolo 2015, *The CamSol method of rational design of protein mutants with
  enhanced solubility* (J Mol Biol 427:478)
