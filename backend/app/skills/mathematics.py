"""skill.math — uncertainty, calibration and ranking-under-cost.

The daemon needs four numerically honest primitives:

* propagate uncertainty through a weighted objective,
* update a belief about prediction bias from new wet-lab measurements (normal-normal conjugate),
* fit a calibration line predicted -> measured and report the residual spread,
* rank candidates by information gained per dollar rather than by raw score.

Everything is closed-form, deterministic and dependency-free, so a research event can be replayed
years later and produce the identical number.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

from app.skills.base import Metric, SkillSpec, collect, register

VERSION = "1.0.0"

CITATIONS = [
    "Taylor 1997, An Introduction to Error Analysis, 2nd ed. (linear uncertainty propagation)",
    "Gelman et al. 2013, Bayesian Data Analysis, 3rd ed., ch.2 (normal-normal conjugate update)",
    "Auer, Cesa-Bianchi & Fischer 2002, Finite-time analysis of the multiarmed bandit problem "
    "(Machine Learning 47:235) — upper-confidence selection",
    "Settles 2012, Active Learning (Synthesis Lectures on AI and ML 6:1) — value of information",
]

Operation = Literal[
    "propagate_uncertainty",
    "bayesian_update",
    "calibrate",
    "rank_under_cost",
    "residual_summary",
]


class Term(BaseModel):
    name: str = ""
    value: float
    sd: float = 0.0
    weight: float = 1.0


class Pair(BaseModel):
    label: str = ""
    predicted: float
    measured: float
    predicted_sd: float = 0.0


class CostItem(BaseModel):
    label: str
    value: float = Field(description="expected objective value, higher is better")
    sd: float = Field(default=0.0, ge=0.0, description="uncertainty; drives exploration value")
    cost_usd: float = Field(default=1.0, gt=0.0)


class MathInput(BaseModel):
    operation: Operation
    terms: list[Term] = Field(default_factory=list)
    pairs: list[Pair] = Field(default_factory=list)
    items: list[CostItem] = Field(default_factory=list)
    residuals: list[float] = Field(default_factory=list)
    prior_mean: float = 0.0
    prior_sd: float = 1.0
    observation: float = 0.0
    observation_sd: float = 1.0
    exploration: float = Field(default=1.0, ge=0.0, le=3.0)


class MathOutput(BaseModel):
    operation: str
    result: dict = Field(default_factory=dict)
    metrics: dict[str, dict] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)


def propagate(terms: list[Term]) -> dict:
    """Weighted sum with independent errors: sd = sqrt(sum (w_i * sd_i)^2)."""
    value = sum(t.weight * t.value for t in terms)
    variance = sum((t.weight * t.sd) ** 2 for t in terms)
    return {
        "value": round(value, 6),
        "sd": round(math.sqrt(variance), 6),
        "n_terms": len(terms),
        "method": "linear propagation of independent errors (Taylor 1997)",
    }


def bayesian_update(prior_mean: float, prior_sd: float, obs: float, obs_sd: float) -> dict:
    """Normal-normal conjugate update of a scalar belief (e.g. per-metric prediction bias)."""
    prior_var = max(prior_sd, 1e-9) ** 2
    obs_var = max(obs_sd, 1e-9) ** 2
    post_var = 1.0 / (1.0 / prior_var + 1.0 / obs_var)
    post_mean = post_var * (prior_mean / prior_var + obs / obs_var)
    shrinkage = post_var / prior_var
    return {
        "posterior_mean": round(post_mean, 6),
        "posterior_sd": round(math.sqrt(post_var), 6),
        "variance_reduction": round(1.0 - shrinkage, 6),
        "method": "normal-normal conjugate update (Gelman et al. 2013)",
    }


def calibrate(pairs: list[Pair]) -> dict:
    """Ordinary least squares measured ~ a + b * predicted, plus residual spread and Pearson r."""
    n = len(pairs)
    if n == 0:
        return {"n": 0, "slope": 1.0, "intercept": 0.0, "residual_sd": 0.0, "pearson_r": 0.0,
                "bias": 0.0, "method": "no data: identity calibration"}
    xs = [p.predicted for p in pairs]
    ys = [p.measured for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx if sxx > 1e-12 else 1.0
    intercept = my - slope * mx
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys, strict=True)]
    dof = max(n - 2, 1)
    residual_sd = math.sqrt(sum(r * r for r in residuals) / dof)
    denom = math.sqrt(sxx * syy)
    pearson = sxy / denom if denom > 1e-12 else 0.0
    return {
        "n": n,
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
        "residual_sd": round(residual_sd, 6),
        "pearson_r": round(pearson, 6),
        "bias": round(my - mx, 6),
        "mae": round(sum(abs(r) for r in residuals) / n, 6),
        "rmse": round(math.sqrt(sum(r * r for r in residuals) / n), 6),
        "method": "OLS calibration of measured on predicted with residual standard deviation",
    }


def rank_under_cost(items: list[CostItem], exploration: float = 1.0) -> dict:
    """Rank by upper-confidence value per dollar: (value + k*sd) / cost.

    The ``k*sd`` term is what makes an uncertain candidate worth testing: it is the part of the
    objective the experiment would actually resolve, which is the information we are buying.
    """
    ranked = []
    for item in items:
        optimistic = item.value + exploration * item.sd
        ranked.append(
            {
                "label": item.label,
                "value": round(item.value, 6),
                "sd": round(item.sd, 6),
                "cost_usd": round(item.cost_usd, 2),
                "optimistic_value": round(optimistic, 6),
                "value_per_usd": round(item.value / item.cost_usd, 8),
                "information_per_usd": round((exploration * item.sd) / item.cost_usd, 8),
                "utility_per_usd": round(optimistic / item.cost_usd, 8),
            }
        )
    ranked.sort(key=lambda r: r["utility_per_usd"], reverse=True)
    for position, row in enumerate(ranked, start=1):
        row["rank"] = position
    return {
        "ranked": ranked,
        "exploration": exploration,
        "method": "upper-confidence value per unit cost (Auer et al. 2002; Settles 2012)",
    }


def residual_summary(residuals: list[float]) -> dict:
    n = len(residuals)
    if n == 0:
        return {"n": 0, "bias": 0.0, "mae": 0.0, "rmse": 0.0, "sd": 0.0,
                "method": "no residuals recorded yet"}
    bias = sum(residuals) / n
    var = sum((r - bias) ** 2 for r in residuals) / max(n - 1, 1)
    return {
        "n": n,
        "bias": round(bias, 6),
        "mae": round(sum(abs(r) for r in residuals) / n, 6),
        "rmse": round(math.sqrt(sum(r * r for r in residuals) / n), 6),
        "sd": round(math.sqrt(var), 6),
        "method": "sample bias / MAE / RMSE of prediction residuals (measured - predicted)",
    }


def run(payload: MathInput) -> MathOutput:
    if payload.operation == "propagate_uncertainty":
        result = propagate(payload.terms)
        metrics = collect(
            [
                Metric("objective_value", result["value"], "score", result["method"], "skill.math",
                       sd=result["sd"], citations=(CITATIONS[0],)),
            ]
        )
    elif payload.operation == "bayesian_update":
        result = bayesian_update(
            payload.prior_mean, payload.prior_sd, payload.observation, payload.observation_sd
        )
        metrics = collect(
            [
                Metric("posterior_bias", result["posterior_mean"], "metric units", result["method"],
                       "skill.math", sd=result["posterior_sd"], citations=(CITATIONS[1],)),
            ]
        )
    elif payload.operation == "calibrate":
        result = calibrate(payload.pairs)
        metrics = collect(
            [
                Metric("calibration_slope", result["slope"], "ratio", result["method"], "skill.math"),
                Metric("calibration_residual_sd", result["residual_sd"], "metric units",
                       result["method"], "skill.math"),
            ]
        )
    elif payload.operation == "rank_under_cost":
        result = rank_under_cost(payload.items, payload.exploration)
        best = result["ranked"][0] if result["ranked"] else {"utility_per_usd": 0.0}
        metrics = collect(
            [
                Metric("best_utility_per_usd", best["utility_per_usd"], "score/USD", result["method"],
                       "skill.math", citations=(CITATIONS[2], CITATIONS[3])),
            ]
        )
    else:
        result = residual_summary(payload.residuals)
        metrics = collect(
            [
                Metric("residual_rmse", result["rmse"], "metric units", result["method"], "skill.math"),
                Metric("residual_bias", result["bias"], "metric units", result["method"], "skill.math"),
            ]
        )
    return MathOutput(operation=payload.operation, result=result, metrics=metrics, citations=CITATIONS)


SKILL = register(
    SkillSpec(
        name="skill.math",
        version=VERSION,
        summary=(
            "Uncertainty propagation, normal-normal Bayesian recalibration, OLS drift calibration, "
            "residual summaries and upper-confidence ranking per dollar."
        ),
        input_model=MathInput,
        output_model=MathOutput,
        runner=run,
        citations=CITATIONS,
    )
)
