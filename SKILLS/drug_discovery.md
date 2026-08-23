# skill.drug_discovery (v1.0.0)

Developability index with immunogenicity flags and an explicit affinity-vs-developability Pareto
trade-off per candidate.

Module: `backend/app/skills/drug_discovery.py` - Tests: `backend/tests/test_skills.py`,
`backend/tests/test_evidence.py`

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

`developability_sd = 0.18` makes the ranking layer honest - a candidate is only promoted over another
when the difference survives its own error bar - but it is a **policy band**, not a measured standard
deviation: no calibration of this composite against expression or manufacturability outcomes exists.
The index itself is a hand-weighted mean of five sub-scores on an arbitrary 0-1 scale, so it ranks
designs of the same protein and must never be read as a probability of clinical success.
`pareto_optimal` and `tradeoff` are what let the UI answer *why this, not that* - the losing
candidate is named together with the axis it lost on.

The immunogenicity term is an allele-agnostic 9-mer motif count. It has no allele coverage, no
binding affinity and no HLA frequency weighting, and the 6 cores/100 aa limit is a project gate; an
allele-aware predictor is required before any (non-)immunogenicity claim leaves the platform.

## Evidence

Machine-readable in `app.skills.drug_discovery.EVIDENCE`. Status: *calibrated* = published method
**and** published error; *anchored* = published method, our constants are not the source's fitted
values; *policy* = a Foldsmith choice; *proxy* = uncalibrated screening heuristic.

| Key | Claim | DOI | Applicability | Known error | Status |
| --- | --- | --- | --- | --- | --- |
| `drug_discovery.developability_flags` | developability is a set of roughly equally disqualifying flags | [10.1073/pnas.1616408114](https://doi.org/10.1073/pnas.1616408114), [10.1073/pnas.1810576116](https://doi.org/10.1073/pnas.1810576116) | 137 clinical-stage therapeutic antibodies profiled in 12 biophysical assays, and the five computational guidelines derived from those distributions | antibody-specific percentile flags, not a scored index; transferring them to non-antibody designs has no published validation | anchored |
| `drug_discovery.composite_weights` | the 0-1 index is a weighted mean of five sub-scores | — | ranking designs of the same protein within one campaign | uncalibrated: weights chosen by hand, never fitted against expression or manufacturability outcomes; no physical unit and not a probability | proxy |
| `drug_discovery.developability_sd` | the index is published with sd 0.18 | [10.1093/bioinformatics/btaa1102](https://doi.org/10.1093/bioinformatics/btaa1102) | *E. coli* soluble-expression prediction from sequence, as an upper bound on the composite's strongest input | SoluProt reaches 58.5% accuracy / AUC 0.62 on its independent test set, so a sequence-only solubility term is barely better than a coin flip. 0.18 on a 0-1 scale is a deliberately wide policy band, not a measured sd of this composite | policy |
| `drug_discovery.solubility_ingredients` | the solubility sub-score mixes charge density, hydropathy and aggregation load | [10.1016/j.jmb.2014.09.026](https://doi.org/10.1016/j.jmb.2014.09.026) | the published method predicts *relative* solubility change on mutation | our weights are not CamSol's fitted coefficients, so the sub-score is in arbitrary units, is not comparable with a CamSol score and predicts no mg/mL | proxy |
| `drug_discovery.immunogenicity_limit` | flagged above 6 MHC-II-like cores per 100 aa | [10.1093/nar/gkaa379](https://doi.org/10.1093/nar/gkaa379) | internal screening of designs of the same protein | uncalibrated in both directions: the count is allele-agnostic with no affinity or HLA weighting, and 6/100 aa is a project gate. NetMHCIIpan-4.0 (trained on binding-affinity and eluted-ligand data) is required for an epitope claim | policy |

Screening filters (`app.toolkit.developability.DEFAULT_FILTERS`: instability <= 45, aggregation
<= 0.30, ddG proxy <= 3.0, pI keep-out band, even Cys count) are project gates in the same sense -
see `backend/app/toolkit/CITATIONS.md` for their per-method status.

## Citations

- Jain et al. 2017, *Biophysical properties of the clinical-stage antibody landscape* (PNAS 114:944)
  - doi:10.1073/pnas.1616408114
- Raybould et al. 2019, *Five computational developability guidelines for therapeutic antibody
  profiling* (PNAS 116:4025) - doi:10.1073/pnas.1810576116
- Hon et al. 2021, *SoluProt: prediction of soluble protein expression in Escherichia coli*
  (Bioinformatics 37:23) - doi:10.1093/bioinformatics/btaa1102 - 58.5% accuracy, AUC 0.62
- Sormanni, Aprile & Vendruscolo 2015, *The CamSol method of rational design of protein mutants with
  enhanced solubility* (J Mol Biol 427:478) - doi:10.1016/j.jmb.2014.09.026
- Reynisson et al. 2020, *NetMHCpan-4.1 and NetMHCIIpan-4.0* (Nucleic Acids Res 48:W449) -
  doi:10.1093/nar/gkaa379
