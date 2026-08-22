# skill.math (v1.0.0)

Uncertainty propagation, normal-normal Bayesian recalibration, OLS drift calibration, residual
summaries and upper-confidence ranking per dollar.

Module: `backend/app/skills/mathematics.py` - Tests: `backend/tests/test_skills.py`

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

## Citations

- Taylor 1997, *An Introduction to Error Analysis*, 2nd ed. (linear uncertainty propagation)
- Gelman et al. 2013, *Bayesian Data Analysis*, 3rd ed., ch.2 (normal-normal conjugate update)
- Auer, Cesa-Bianchi & Fischer 2002, *Finite-time analysis of the multiarmed bandit problem*
  (Machine Learning 47:235) - upper-confidence selection
- Settles 2012, *Active Learning* (Synthesis Lectures on AI and ML 6:1) - value of information
