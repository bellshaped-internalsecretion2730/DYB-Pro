"""Program economics: what this run cost, and what the human baseline would have cost.

Everything here is an explicit, auditable arithmetic model over published industry cost and
timeline benchmarks (see ``app/pharma/CITATIONS.md``) plus the ACUs Pharmakon actually spent.
It is a planning aid, not a financial projection: the risk-adjusted value uses historical
stage probabilities, which do not transfer to any individual program.
"""

from __future__ import annotations

from app.pharma.stages import STAGES, stage

ACU_PRICE_USD = 2.25  # list price per ACU used for the comparison
PLATFORM_MONTHLY_USD = 4_000.0  # infrastructure attributable to one program
WETLAB_ASSAY_USD = 850.0  # cost of one confirmatory in-vitro assay run
AUTONOMOUS_CALENDAR_FRACTION = 0.18  # in-silico stages compress calendar time, not lab work


def stage_costs(stage_key: str) -> dict:
    spec = stage(stage_key)
    return {
        "stage": spec.key,
        "human_team_cost_usd": spec.human_team_cost_usd,
        "human_team_months": spec.human_team_months,
        "historical_pos": spec.historical_pos,
    }


def program_economics(
    *,
    current_stage: str,
    acus_used: float,
    cycles_run: int,
    assays_ingested: int,
    months_elapsed: float,
    peak_sales_usd: float = 1_200_000_000.0,
) -> dict:
    """Compare the autonomous run against the human baseline for the stages completed."""
    spec = stage(current_stage)
    completed = [s for s in STAGES if s.order < spec.order]
    baseline_cost = sum(s.human_team_cost_usd for s in completed)
    baseline_months = sum(s.human_team_months for s in completed)

    agent_cost = round(acus_used * ACU_PRICE_USD, 2)
    platform_cost = round(max(months_elapsed, 0.0) * PLATFORM_MONTHLY_USD, 2)
    wetlab_cost = round(assays_ingested * WETLAB_ASSAY_USD, 2)
    actual_cost = round(agent_cost + platform_cost + wetlab_cost, 2)

    remaining = [s for s in STAGES if s.order >= spec.order]
    pos_remaining = 1.0
    for s in remaining:
        pos_remaining *= s.historical_pos
    remaining_baseline = sum(s.human_team_cost_usd for s in remaining)

    return {
        "stages_completed": [s.key for s in completed],
        "autonomous": {
            "acus_used": round(acus_used, 2),
            "agent_cost_usd": agent_cost,
            "platform_cost_usd": platform_cost,
            "wetlab_cost_usd": wetlab_cost,
            "total_cost_usd": actual_cost,
            "months_elapsed": round(months_elapsed, 2),
            "cycles_run": cycles_run,
        },
        "human_baseline": {
            "total_cost_usd": baseline_cost,
            "months": baseline_months,
            "source": "published preclinical stage cost/duration benchmarks (see CITATIONS.md)",
        },
        "delta": {
            "cost_usd_saved": round(baseline_cost - actual_cost, 2),
            "cost_ratio": round(actual_cost / baseline_cost, 5) if baseline_cost else None,
            "months_saved": round(baseline_months - months_elapsed, 2),
        },
        "forward_look": {
            "remaining_stages": [s.key for s in remaining],
            "remaining_human_cost_usd": remaining_baseline,
            "probability_of_reaching_fih": round(pos_remaining, 4),
            "risk_adjusted_value_usd": round(pos_remaining * peak_sales_usd, 2),
            "expected_cost_to_fih_usd": round(remaining_baseline, 2),
            "caveat": (
                "Historical stage probabilities are portfolio statistics; they are not a "
                "prediction for this program and exclude clinical attrition after first-in-human."
            ),
        },
    }


def portfolio_view(programs: list[dict]) -> dict:
    """Roll individual program economics into a portfolio summary."""
    if not programs:
        return {"programs": 0, "total_cost_usd": 0.0, "risk_adjusted_value_usd": 0.0, "rows": []}

    def spend(row: dict) -> float:
        return float((row.get("autonomous") or {}).get("total_cost_usd") or 0.0)

    def value(row: dict) -> float:
        return float((row.get("forward_look") or {}).get("risk_adjusted_value_usd") or 0.0)

    def baseline(row: dict) -> float:
        return float((row.get("human_baseline") or {}).get("total_cost_usd") or 0.0)

    rows = sorted(programs, key=lambda p: (-value(p), spend(p)))
    top = rows[0]
    return {
        "programs": len(rows),
        "total_cost_usd": round(sum(spend(p) for p in rows), 2),
        "risk_adjusted_value_usd": round(sum(value(p) for p in rows), 2),
        "human_baseline_cost_usd": round(sum(baseline(p) for p in rows), 2),
        "rows": rows,
        "recommendation": (
            f"Fund {top.get('name')} first: highest risk-adjusted value "
            f"(${value(top):,.0f}) of the {len(rows)} program(s) on record, at "
            f"${spend(top):,.0f} spent so far. Risk-adjusted value uses historical stage "
            "probabilities, so it ranks programs and is not a forecast for any one of them."
        ),
    }
