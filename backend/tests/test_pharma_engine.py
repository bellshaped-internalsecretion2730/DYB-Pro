"""Deterministic program engine: ladder, gates, autonomy, drift, experiments, economics.

These are the decisions Pharmakon is allowed to make without a human, so each rule gets an
explicit test: agents may only supply numbers, the functions below decide.
"""

from __future__ import annotations

import itertools

import pytest

from app.pharma.dossier import build_dossier
from app.pharma.economics import portfolio_view, program_economics
from app.pharma.experiments import experiment_plan
from app.pharma.gates import GO_SCORE_THRESHOLD, RECYCLE_PATIENCE, evaluate_gate
from app.pharma.metrics import prediction_drift, program_metrics
from app.pharma.stages import (
    AUTONOMY_LEVELS,
    FIRST_STAGE,
    HUMAN_SIGNED_STAGES,
    STAGE_KEYS,
    ladder,
    next_stage,
    stage,
)


def passing_metrics(stage_key: str) -> dict[str, float]:
    """Metrics that satisfy every criterion of a stage, derived from the criteria themselves."""
    metrics: dict[str, float] = {}
    for criterion in stage(stage_key).criteria:
        if criterion.comparator in (">=", ">"):
            metrics[criterion.key] = criterion.threshold + abs(criterion.threshold) * 0.1 + 0.5
        elif criterion.comparator in ("<=", "<"):
            metrics[criterion.key] = max(0.0, criterion.threshold - 0.05)
        else:
            metrics[criterion.key] = criterion.threshold
    return metrics


def test_ladder_is_ordered_and_terminates():
    keys = [s["key"] for s in ladder()]
    assert keys == list(STAGE_KEYS)
    assert keys[0] == FIRST_STAGE
    assert next_stage(keys[-1]) is None
    for a, b in itertools.pairwise(keys):
        assert next_stage(a) == b
        assert stage(a).order < stage(b).order


def test_every_stage_declares_roles_criteria_and_a_baseline():
    for key in STAGE_KEYS:
        spec = stage(key)
        assert spec.roles and spec.criteria
        assert any(c.blocking for c in spec.criteria)
        assert spec.human_team_cost_usd > 0 and spec.human_team_months > 0
        assert 0.0 < spec.historical_pos <= 1.0
        assert spec.min_autonomy in AUTONOMY_LEVELS
        assert spec.exit_deliverable


def test_full_evidence_passes_the_gate():
    result = evaluate_gate(FIRST_STAGE, passing_metrics(FIRST_STAGE), autonomy_level=4)
    assert result.decision == "go"
    assert result.score >= GO_SCORE_THRESHOLD
    assert result.next_stage == next_stage(FIRST_STAGE)
    assert not result.requires_approval


def test_missing_evidence_recycles_and_never_kills():
    """An unmeasured criterion is not a failed one - offline runs must not kill a program."""
    result = evaluate_gate(FIRST_STAGE, {}, autonomy_level=4)
    assert result.decision == "recycle"
    assert all(c["missing_evidence"] for c in result.blocking_failures)
    assert "not a failed one" in result.rationale

    patient = evaluate_gate(FIRST_STAGE, {}, autonomy_level=4, stage_cycles=RECYCLE_PATIENCE)
    assert patient.decision == "no_go"  # holds for a human, still not a kill


def test_measured_validity_failure_kills():
    metrics = passing_metrics(FIRST_STAGE) | {"target_evidence_score": 0.05}
    result = evaluate_gate(FIRST_STAGE, metrics, autonomy_level=4)
    assert result.decision == "kill"
    assert result.requires_approval  # irreversible: a human confirms
    assert "premise" in result.rationale


def test_measured_optimizable_failure_recycles_until_patience_runs_out():
    metrics = passing_metrics("hit_to_lead") | {"best_pkd": 1.0}
    early = evaluate_gate("hit_to_lead", metrics, autonomy_level=4, stage_cycles=0)
    late = evaluate_gate(
        "hit_to_lead", metrics, autonomy_level=4, stage_cycles=RECYCLE_PATIENCE
    )
    assert early.decision == "recycle"
    assert late.decision == "kill"
    assert late.requires_approval


@pytest.mark.parametrize("stage_key", STAGE_KEYS)
def test_autonomy_gates_advancement(stage_key):
    metrics = passing_metrics(stage_key)
    spec = stage(stage_key)
    low = evaluate_gate(stage_key, metrics, autonomy_level=0)
    high = evaluate_gate(stage_key, metrics, autonomy_level=4)
    assert low.requires_approval
    assert high.requires_approval is spec.human_signature_required


def test_human_signed_stages_always_need_a_signature():
    for key in HUMAN_SIGNED_STAGES:
        result = evaluate_gate(key, passing_metrics(key), autonomy_level=4)
        assert result.requires_approval
        assert result.approval_reason


def test_recommended_actions_separate_missing_from_insufficient():
    missing = evaluate_gate(FIRST_STAGE, {}, autonomy_level=2)
    assert any("missing evidence" in a for a in missing.recommended_actions)
    weak = evaluate_gate(
        "hit_to_lead", passing_metrics("hit_to_lead") | {"best_pkd": 1.0}, autonomy_level=2
    )
    assert any("Move" in a for a in weak.recommended_actions)


def test_gate_decision_is_a_pure_function():
    metrics = passing_metrics("lead_optimization")
    a = evaluate_gate("lead_optimization", metrics, autonomy_level=2, stage_cycles=1)
    b = evaluate_gate("lead_optimization", metrics, autonomy_level=2, stage_cycles=1)
    assert a.as_dict() == b.as_dict()


def test_program_metrics_prefer_measured_assays_over_predictions():
    evaluations = [
        {
            "molecule_hash": "aaa",
            "smiles": "c1ccccc1",
            "composite_score": 0.8,
            "binding": {"pkd": 6.0, "kd_nm": 1000.0},
            "admet": {"admet_score": 0.7},
        }
    ]
    without = program_metrics(evaluations=evaluations, assays=[], findings={}, stage_cycles=0)
    with_assay = program_metrics(
        evaluations=evaluations,
        assays=[{"molecule_hash": "aaa", "metric": "pkd", "value": 8.0}],
        findings={},
        stage_cycles=0,
    )
    assert without["best_pkd"] == 6.0
    assert with_assay["best_pkd"] == 8.0
    assert with_assay["assay_confirmed_count"] >= 1


def test_findings_reach_the_gate_metrics():
    metrics = program_metrics(
        evaluations=[],
        assays=[],
        findings={"target_evidence_score": 0.9, "freedom_to_operate": 0.8},
        stage_cycles=2,
    )
    assert metrics["target_evidence_score"] == 0.9
    assert metrics["freedom_to_operate"] == 0.8


def test_prediction_drift_only_compares_comparable_endpoints():
    evaluations = [{"molecule_hash": "m1", "binding": {"pkd": 7.0}}]
    drift = prediction_drift(
        evaluations,
        [
            {"molecule_hash": "m1", "metric": "pkd", "value": 6.0},
            {"molecule_hash": "m1", "metric": "solubility_ug_ml", "value": 42.0},
            {"molecule_hash": "unknown", "metric": "pkd", "value": 9.0},
        ],
    )
    assert drift["n"] == 1
    assert drift["rmse"] == pytest.approx(1.0)
    assert drift["bias"] == pytest.approx(1.0)  # the model predicted tighter than measured


def test_no_assays_means_no_drift_not_zero_drift():
    drift = prediction_drift([{"molecule_hash": "m1", "binding": {"pkd": 7.0}}], [])
    assert drift["n"] == 0
    assert drift["rmse"] is None


def test_experiment_plan_targets_the_failing_criteria_within_budget():
    gate = evaluate_gate(
        "hit_to_lead", passing_metrics("hit_to_lead") | {"best_pkd": 1.0}, autonomy_level=2
    )
    candidates = [
        {
            "molecule_hash": "m1",
            "smiles": "c1ccccc1",
            "binding": {"pkd": 5.0, "kd_nm": 10000.0},
            "admet": {"excretion": {"half_life_h": 3.0}},
        }
    ]
    plan = experiment_plan(gate.as_dict(), candidates, budget_usd=2000.0)
    assert plan["proposals"]
    assert plan["total_cost_usd"] <= 2000.0
    for proposal in plan["proposals"]:
        assert proposal["metric"] in {c["key"] for c in gate.criteria if not c["passed"]}
        assert proposal["compounds"] and proposal["falsifies_if"]
    potency = [p for p in plan["proposals"] if p["metric"] == "best_pkd"]
    if potency:
        # A comparable prediction must be quoted in the assay's own unit, so drift is meaningful.
        assert potency[0]["predicted_value"] == 10000.0
        assert potency[0]["endpoint"] == "kd_nm"


def test_economics_compares_against_a_human_baseline():
    econ = program_economics(
        current_stage="lead_optimization",
        acus_used=120.0,
        cycles_run=8,
        assays_ingested=3,
        months_elapsed=2.0,
    )
    assert econ["autonomous"]["total_cost_usd"] > 0
    assert econ["human_baseline"]["total_cost_usd"] > econ["autonomous"]["total_cost_usd"]
    assert econ["forward_look"]["risk_adjusted_value_usd"] >= 0
    assert 0.0 < econ["forward_look"]["probability_of_reaching_fih"] <= 1.0
    assert econ["delta"]["cost_usd_saved"] > 0


def test_portfolio_ranks_by_risk_adjusted_value():
    rows = [
        {
            "name": "low",
            **program_economics(
                current_stage="hit_finding",
                acus_used=10.0,
                cycles_run=1,
                assays_ingested=0,
                months_elapsed=1.0,
            ),
        },
        {
            "name": "high",
            **program_economics(
                current_stage="candidate_selection",
                acus_used=400.0,
                cycles_run=20,
                assays_ingested=8,
                months_elapsed=6.0,
            ),
        },
    ]
    view = portfolio_view(rows)
    assert view["programs"] == 2
    assert view["rows"][0]["name"] == "high"
    assert "high" in view["recommendation"]
    assert view["total_cost_usd"] > 0


def test_dossier_reports_its_own_incompleteness():
    empty = build_dossier(
        program={"name": "p", "target_name": "t", "indication": "i", "objective": "o"},
        candidate=None,
        gate_history=[],
        assays=[],
        drift={"n": 0, "rmse": None},
    )
    assert 0.0 <= empty["completeness"] < 1.0
    assert empty["sections"]
    assert empty["missing_sections"]
    assert all(s["title"] for s in empty["sections"].values())
    assert empty["disclaimer"]

    full = build_dossier(
        program={"name": "p", "target_name": "t", "indication": "i", "objective": "o"},
        candidate={
            "smiles": "c1ccccc1",
            "molecule_hash": "m1",
            "binding": {"pkd": 8.0},
            "admet": {"toxicity": {"herg": {"risk": 0.1}}},
            "synthesis": {"sa_score": 2.0},
            "dose_projection": {"projected_dose_mg": 50.0},
        },
        assays=[{"molecule_hash": "m1", "metric": "pkd", "value": 7.8}],
        gate_history=[{"stage": "hit_finding", "decision": "go"}],
        findings={
            "target_evidence_score": 0.8,
            "citations": ["doi:10.0/x"],
            "freedom_to_operate": 0.7,
            "starting_dose_mg": 5.0,
            "stopping_rules": ["ALT > 3x ULN"],
        },
        drift={"n": 1, "rmse": 0.2},
    )
    assert full["completeness"] > empty["completeness"]
    # Even a complete package is explicitly a draft for human review, never a submission.
    assert "not" in full["disclaimer"].lower()
