# The wet-lab loop

`fold -> version -> research -> proposed experiment -> (simulated or real) result -> re-ingest -> next version`

This is the path that makes in-silico work cheaper than the bench: every version carries a
*predicted distribution*, every measurement produces a residual, and the residuals recalibrate the
next prediction. Implementation: `backend/app/services/wetlab_loop.py`, driven by the always-on
Research Daemon (`backend/app/services/daemon.py`).

## 1 · Predict (`predict_metrics`)

Inputs: the committed sequence, the stored structure, the residue labels on that version, and the
expression host. Every number comes from a skill and carries a standard deviation taken from
published benchmark performance, not from intuition:

| Metric | sd | Where the sd comes from |
| --- | --- | --- |
| `melting_temperature` | 4.0 C | ddG predictors reach r = 0.26-0.59 (Potapov 2009) |
| `soluble_fraction` | 20 % | sequence-only solubility predictors, AUC ~0.62 (SoluProt 2021) |
| `expression_yield` | 0.6 rel. | host/temperature/media factor |
| `aggregation_hmw` | 3 % | SEC HMW practice |
| `purification_recovery` | 15 % | ~45 % stage-wise structural-genomics success (Todd 2005) |
| `kd` | 1.0 log10 | docking scoring functions do not rank affinity reliably (Kastritis 2010) |

Hosts are explicit presets (`E. coli BL21(DE3)`, `SHuffle T7`, `periplasm (pelB)`,
`HEK293-F transient`) each with temperature, media and a yield factor, because host choice moves
expression more than most in-silico reports admit.

`apply_calibration` then shifts each prediction by whatever the campaign's drift model has learned so
far, and marks the prediction `calibrated=true` while keeping `raw_value`. Nothing is silently
rewritten.

## 2 · Risk report (`risk_report`)

Named, defensible failure modes rather than a single score: expression (host / temperature / media),
solubility, purification, Tm and stability, aggregation, disulfide and PTM liabilities, binding or
activity assay choice, yield, and cost. Each risk names the metric that drives it, the residues
involved when relevant, and what the scientist should do about it.

## 3 · Propose the pack (`build_plan`)

A minimal pack that maximises **information per dollar**:

- **Constructs**: ORF, tags, route (site-directed mutagenesis when the version has mutations,
  otherwise gene synthesis), mutagenesis primers, and a build cost.
- **Assays**: only assays that can move a decision for *this* design (`recommended_assays`), ranked
  by `skill.math`'s upper-confidence utility per dollar. Information is measured as the prediction's
  uncertainty expressed in units of the assay's own noise - an assay that cannot resolve the
  question does not earn its cost.
- **Controls**: parent/wild-type on the same plate (delta_Tm is meaningless otherwise), buffer blank,
  and a known-expressing positive to separate host failure from design failure.
- **Accept/reject thresholds**: canonical pass rule per metric plus the decision rule *reject and
  re-design if the measurement misses the rule by more than two combined sigma*.
- **Totals**: cost and days, against the cost of testing everything.

## 4 · Measure

Two honest routes, both landing in the same canonical schema (`skill.wetlab_metrics`):

- **Real results**: paste CSV/JSON or post structured rows. Aliases (`expressed`, `tm`, `kd_nm`, ...)
  normalise to canonical names and units; unrecognised columns are returned in `unknown_metrics`
  rather than being guessed at.
- **Simulator** (`simulate_results`): samples each measurement from the predicted distribution
  widened by the published assay noise:
  `measurement = prediction + N(0, sqrt(pred_sd^2 + assay_sd^2))`. The exact error model, the seed
  and the per-metric noise are stored on the row, and `source="simulator"` is never rewritten. It
  optionally accepts a documented systematic per-metric offset (used by the seeded demo to represent
  in-silico optimism); when present, the offsets are recorded in `error_model.systematic_bias`.

The simulator exists so the demo runs with no lab attached. It is not evidence about any real
protein, and nothing in the UI or the API presents it as such.

## 5 · Learn (`compute_residuals`, `update_drift`)

Results commit back onto the same version and spawn a `learn` event:

- residual and z-score per metric (`measured - predicted`, over combined sigma);
- the campaign `DriftModel` per metric is updated by `skill.math` (`calibrate` + `bayes_update`):
  bias, slope/intercept, residual sd, RMSE, and the full residual history;
- hypotheses attached to the version are resolved as supported or refuted;
- an insight and a next-version proposal are written, with the ranked candidates, the target metric,
  the predicted wet-lab outcome for the proposal, and the citations behind it.

Because the drift model feeds `apply_calibration`, round *n+1* starts from a prediction that already
absorbed round *n*'s error. In the seeded demo this is visible: the simulator injects a fixed
optimism offset (for example -3.4 C on Tm), and the absolute Tm residual shrinks from v1 to v3 as the
campaign learns that offset.

## 6 · Next version

The daemon re-researches the new version *and* the whole path v1..vN, merges cached and fresh
knowledge, and proposes vN+1 with the metric it is trying to move and why. That proposal is the
starting point of the next fold, which closes the loop.

## API surface

| Step | Endpoint |
| --- | --- |
| Risk | `GET /api/commits/{commit_id}/wetlab/risk` |
| Plan | `GET` / `POST /api/commits/{commit_id}/wetlab/plan` |
| Simulate | `POST /api/wetlab/plans/{plan_id}/simulate` |
| Results | `GET` / `POST /api/commits/{commit_id}/wetlab/results` |
| Assay catalogue | `GET /api/wetlab/assays` |
| Drift | `GET /api/projects/{project_id}/research/drift` |
| Learned v1 -> latest | `GET /api/projects/{project_id}/research/learned` |
| Next proposal | `GET /api/projects/{project_id}/research/proposal` |

Tests: `backend/tests/test_research_loop.py`, `backend/tests/test_ranking_wetlab.py`,
`backend/tests/test_research_api.py`, `backend/tests/test_demo_campaign.py`.
