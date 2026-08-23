# skill.math (v1.0.0)

Uncertainty propagation, normal-normal Bayesian recalibration, OLS drift calibration, residual
summaries and upper-confidence ranking per dollar.

Module: `backend/app/skills/mathematics.py` - Tests: `backend/tests/test_skills.py`,
`backend/tests/test_evidence.py`

## Input

`operation` (required) selects the computation; the remaining fields are the operands.

| Operation | Operands | Result |
| --- | --- | --- |
| `propagate` | `terms` (value, sd, weight) | combined mean and sd of a linear combination |
| `bayes_update` | `prior_mean`, `prior_sd`, `observation`, `observation_sd` | posterior mean and sd (variance always shrinks) |
| `calibrate` | `pairs` (predicted, measured) | OLS slope, intercept, bias, residual sd, rmse |
| `residuals` | `residuals` | bias, absolute-error summary, trend across the campaign |
| `rank` | `items` (score, sd, cost) | upper-confidence score per dollar, ranked, with `exploration` weight |

## Output

`result` (operation-specific), `metrics` (stamped numbers), `citations`.

## Why it exists

The drift model is the product's core claim: the gap between in-silico prediction and wet-lab
measurement must shrink over a campaign. `calibrate` and `bayes_update` are what turn each residual
into a corrected prediction for the next version, and `rank` is what turns "which experiment next"
into information per dollar instead of a beauty contest between scores.

## Evidence

Machine-readable in `app.skills.mathematics.EVIDENCE`. These primitives are *analytically exact*:
unlike the biology skills there is no benchmark error to quote, only the assumptions of each formula.
The one heuristic is the exploration term in ranking.

| Key | Claim | DOI | Applicability (assumptions) | Known error | Status |
| --- | --- | --- | --- | --- | --- |
| `math.uncertainty_propagation` | objective sd = root sum of squares of weighted term sds | — | independent, approximately normal terms combined linearly; correlated inputs would need a covariance matrix | exact for a linear combination of independent variables; first-order only if the caller linearised a non-linear objective | exact |
| `math.bayesian_update` | normal-normal conjugate posterior for prediction bias | — | normal prior, known-variance normal likelihood; posterior precision = prior + observation precision | exact under those assumptions; a mis-specified observation sd propagates straight into the posterior | exact |
| `math.calibration_fit` | OLS line predicted -> measured plus residual spread | — | >= 2 pairs, homoscedastic residuals | the fit is exact; usefulness is limited by n and no cross-validation is done | exact |
| `math.rank_under_cost` | rank by `(value + exploration * sd) / cost` | [10.1023/A:1013689704352](https://doi.org/10.1023/A:1013689704352) | choosing the next experiment among candidates in comparable value units | uncalibrated: UCB1's regret bound applies to its own log(t)/n bonus on bounded rewards, not to this cost-divided variant, so no guarantee transfers and `exploration` is a tuning knob | anchored |
| `math.value_of_information` | sd enters the utility because uncertainty is worth paying to reduce | — | experiment selection when measurements cost money | no empirical calibration: the exchange rate between a unit of sd and a dollar is a project choice; the skill neither estimates nor optimises information gain | proxy |

## Citations

- Taylor 1997, *An Introduction to Error Analysis*, 2nd ed. (linear uncertainty propagation)
- Gelman et al. 2013, *Bayesian Data Analysis*, 3rd ed., ch.2 (normal-normal conjugate update)
- Auer, Cesa-Bianchi & Fischer 2002, *Finite-time analysis of the multiarmed bandit problem*
  (Machine Learning 47:235) - doi:10.1023/A:1013689704352 - upper-confidence selection
- Settles 2012, *Active Learning* (Synthesis Lectures on AI and ML 6:1) - value of information
