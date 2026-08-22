# Pharmakon program layer — sources and limitations

The program ladder, gate thresholds and economics are *engineering choices calibrated against
published industry benchmarks*. They are transparent and adjustable, not measured truths.

## Stage structure

The eight stages follow the conventional preclinical progression used across the industry
(target assessment → hit finding → hit-to-lead → lead optimisation → DMPK/safety → candidate
selection → IND-enabling → first-in-human design). Stage naming and exit deliverables follow
common practice as described in medicinal-chemistry program reviews; there is no single
normative definition, and organisations split these stages differently.

## Gate thresholds

- Potency bands (hit pKd ≥ 6, lead pKd ≥ 7, optimised lead pKd ≥ 7.8) are conventional
  screening/lead criteria, applied here to a **predicted complementarity proxy** rather than a
  measured affinity. They rank candidates; they do not assert measured potency.
- Ligand efficiency ≥ 0.28 kcal/mol per heavy atom follows the commonly used LE guidance for
  progressable hits (Hopkins et al., *Drug Discov. Today* 2004).
- Selectivity ≥ 10-fold at hit-to-lead and drug-likeness/QED ≥ 0.5 are pragmatic progression
  criteria, not regulatory requirements.
- Safety thresholds (hERG risk, Ames risk, hepatotoxicity risk, therapeutic margin ≥ 10–15×)
  are risk-ranking cut-offs on deterministic heuristics. A margin computed from predicted
  exposure is **not** a safety finding: only GLP toxicology can support that.

## Probability of success

Per-stage survival probabilities are order-of-magnitude values drawn from published
preclinical/clinical attrition analyses (e.g. Paul et al., *Nat. Rev. Drug Discov.* 2010;
Wong et al., *Biostatistics* 2019 for clinical phases). They are **portfolio statistics** and
carry no predictive weight for an individual program.

## Cost and duration benchmarks

Human-team stage costs and durations are order-of-magnitude benchmarks consistent with
published R&D cost models (Paul et al. 2010; DiMasi et al., *J. Health Econ.* 2016), adjusted
to a single-program view. They exclude capitalised cost of capital and portfolio overhead, so
the "cost saved" figure is a direct-spend comparison only.

CRO assay costs and turnarounds in `experiments.py` are list-price order-of-magnitude estimates
for planning; actual quotes vary by provider, species and compound count.

## What this layer does not do

- It does not run experiments, and no gate metric derived from prediction is treated as
  experimental evidence.
- It does not perform regulatory review. `dossier.py` produces a draft package for humans.
- It makes no claim about human safety or efficacy, and the IND-enabling and trial-design
  stages always require a human signature regardless of autonomy level.
