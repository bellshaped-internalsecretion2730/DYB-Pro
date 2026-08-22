from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.devin.prompts import child_prompt, orchestrator_prompt
from app.models import MeasuredResult, ProblemSpec
from app.services.economics import cheapest_tier_for_readout, filter_performance
from app.services.prices import TIERS
from app.services.problem import SpecError, classify, validate_spec
from tests.conftest import headers


def _payload(deciding: str = "affinity") -> dict:
    return {
        "objectives": [
            {
                "name": "affinity",
                "readout": "binding_kd",
                "unit": "nM",
                "direction": "lower_is_better",
                "threshold": 50.0,
                "must_pass": True,
                "weight": 0.0,
                "proxy": "binding_score",
                "proxy_calibrated": True,
                "assay_tiers": ["T1", "T2"],
            },
            {
                "name": "stability",
                "readout": "thermostability",
                "unit": "degC",
                "direction": "higher_is_better",
                "threshold": 55.0,
                "must_pass": False,
                "weight": 0.4,
                "proxy": "ddg_proxy",
                "assay_tiers": ["T2"],
            },
        ],
        "hard_constraints": [{"name": "no_free_cysteine", "description": "avoid unpaired cysteine"}],
        "deciding_objective": deciding,
        "notes": "test spec",
    }


def _result(**kwargs) -> MeasuredResult:
    defaults = {
        "objective": "affinity",
        "readout": "binding_kd",
        "value": 50.0,
        "unit": "nM",
        "higher_is_better": False,
        "outcome": "miss",
    }
    defaults.update(kwargs)
    return MeasuredResult(**defaults)


def test_validate_spec_rejects_every_declared_rule():
    base = _payload()
    cases = [
        ({"objectives": [base["objectives"][0], base["objectives"][0]]}, "duplicate"),
        ({"objectives": [{**base["objectives"][0], "readout": "unknown"}]}, "readout"),
        ({"objectives": [{**base["objectives"][0], "proxy": "made_up"}]}, "proxy"),
        (
            {"objectives": [{**base["objectives"][0], "assay_tiers": ["T0"]}]},
            "does not decide",
        ),
        (
            {"objectives": [{**base["objectives"][0], "must_pass": False}], "deciding_objective": "affinity"},
            "must_pass",
        ),
        (
            {
                "objectives": [
                    {**base["objectives"][0], "weight": 0.7, "must_pass": False},
                    {**base["objectives"][1], "weight": 0.5},
                ],
                "deciding_objective": "affinity",
            },
            "sum",
        ),
        ({"objectives": [{**base["objectives"][0], "threshold": float("inf")}]}, "finite"),
        ({"objectives": [{**base["objectives"][0], "unit": "  "}]}, "unit"),
    ]
    for changes, message in cases:
        payload = _payload()
        payload.update(changes)
        if "deciding_objective" not in payload:
            payload["deciding_objective"] = "affinity"
        with pytest.raises(SpecError, match=message):
            validate_spec(payload)


def test_validate_spec_is_server_owned_for_proxy_calibration():
    validated = validate_spec(_payload())
    assert validated["objectives"][0]["proxy_calibrated"] is False


def test_classify_uses_inclusive_threshold_and_explicit_undecidable_reasons(db, project):
    spec = ProblemSpec(
        project_id=project.id,
        version=1,
        status="active",
        objectives=validate_spec(_payload())["objectives"],
        hard_constraints=[],
        deciding_objective="affinity",
        target_readout="binding_kd",
    )
    db.add(spec)
    db.flush()
    assert classify(spec, _result(value=50.0))["decision"] == "hit"
    assert classify(spec, _result(value=50.1))["decision"] == "miss"
    assert classify(spec, _result(value=55.0, objective="stability", readout="thermostability", unit="degC", higher_is_better=True))["decision"] == "hit"
    assert classify(
        spec, _result(objective="other", readout="not_declared")
    )["decision"] == "undecidable"
    assert classify(spec, _result(unit="uM"))["decision"] == "undecidable"
    assert classify(spec, _result(higher_is_better=True))["decision"] == "undecidable"
    assert classify(spec, _result(value=float("nan")))["decision"] == "undecidable"
    assert all(
        classify(spec, result)["reason"]
        for result in [
            _result(objective="other", readout="not_declared"),
            _result(unit="uM"),
            _result(higher_is_better=True),
            _result(value=float("nan")),
        ]
    )


def test_filter_performance_aggregates_designs_and_preserves_no_spec_counts():
    def row(commit_id: str, passed: bool, **kwargs):
        return (
            SimpleNamespace(id=commit_id, filters={"passed": passed}),
            _result(**kwargs),
        )

    rows = [
        row("a", True, value=10.0, outcome="hit"),
        row("a", True, value=20.0, outcome="hit"),
        row("b", True, value=20.0, outcome="miss"),
        row("c", False, value=60.0, outcome="hit"),
        row("d", False, value=70.0, outcome="miss"),
    ]
    body = filter_performance(rows)
    assert (body["tp"], body["fp"], body["tn"], body["fn"]) == (1, 1, 1, 1)
    assert body["classification_source"] == "reported_outcome"
    assert body["n_designs"] == 4


def test_filter_performance_spec_conflicts_and_disagreements(db, project):
    validated = validate_spec(_payload())
    spec = ProblemSpec(
        project_id=project.id,
        version=2,
        status="active",
        objectives=validated["objectives"],
        hard_constraints=[],
        deciding_objective="affinity",
        target_readout="binding_kd",
    )
    rows = [
        (
            SimpleNamespace(id="a", filters={"passed": True}),
            _result(value=20.0, outcome="hit"),
        ),
        (
            SimpleNamespace(id="a", filters={"passed": True}),
            _result(value=30.0, outcome="hit"),
        ),
        (
            SimpleNamespace(id="b", filters={"passed": True}),
            _result(value=20.0, outcome="miss"),
        ),
        (
            SimpleNamespace(id="c", filters={"passed": False}),
            _result(value=70.0, outcome="miss"),
        ),
    ]
    body = filter_performance(rows, spec)
    assert body["conflicting"] == 0
    assert body["outcome_disagreements"] == 1
    assert body["n_paired"] == 3
    assert body["classification_source"] == "problem_spec_v2"

    conflict = [
        *rows,
        (
            SimpleNamespace(id="d", filters={"passed": True}),
            _result(value=10.0, outcome="hit"),
        ),
        (
            SimpleNamespace(id="d", filters={"passed": True}),
            _result(value=90.0, outcome="miss"),
        ),
    ]
    conflict_body = filter_performance(conflict, spec)
    assert conflict_body["conflicting"] == 1
    assert conflict_body["n_designs"] == 4


def test_cheapest_tier_for_readout_uses_catalogue_costs():
    candidates = [SimpleNamespace(sequence="A" * 50, mutations=[])]
    assert cheapest_tier_for_readout("binding_kd", candidates) == "T1"
    assert cheapest_tier_for_readout("thermostability", candidates) == "T2"
    assert cheapest_tier_for_readout("unknown", candidates) is None
    assert "T1" in TIERS


def test_problem_spec_endpoints_version_and_tenancy(client, project):
    response = client.post(
        f"/api/projects/{project.id}/problem-spec",
        json=_payload(),
        headers=headers("scientist"),
    )
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["version"] == 1
    assert first["objectives"][0]["proxy_calibrated"] is False
    second = client.post(
        f"/api/projects/{project.id}/problem-spec",
        json=_payload(),
        headers=headers("scientist"),
    )
    assert second.status_code == 200
    assert second.json()["version"] == 2
    assert client.get(
        f"/api/projects/{project.id}/problem-spec", headers=headers("viewer")
    ).json()["version"] == 2
    history = client.get(
        f"/api/projects/{project.id}/problem-spec/history", headers=headers("viewer")
    )
    assert [item["version"] for item in history.json()] == [2, 1]
    invalid = client.post(
        f"/api/projects/{project.id}/problem-spec",
        json={**_payload(), "objectives": []},
        headers=headers("scientist"),
    )
    assert invalid.status_code == 422
    assert client.post(
        f"/api/projects/{project.id}/problem-spec",
        json=_payload(),
        headers=headers("viewer"),
    ).status_code == 403


def test_prompt_problem_spec_is_optional():
    args = {
        "brief": "brief",
        "project_goal": "goal",
        "parent": {"label": "parent", "commit_id": "id", "length": 3, "sequence": "AAA"},
        "evidence": {},
        "history": "",
        "available_roles": ["sequence"],
        "shortlist_size": 5,
    }
    base = orchestrator_prompt(**args)
    with_spec = orchestrator_prompt(**args, problem_spec="OBJECTIVES\n- affinity")
    assert "OBJECTIVES" not in base
    assert "OBJECTIVES" in with_spec
    child_args = {
        "role": "sequence",
        "task": "task",
        "brief": "brief",
        "strategy": "strategy",
        "parent": args["parent"],
        "evidence": {},
        "history": "",
        "focus_regions": [],
        "must_avoid": [],
    }
    assert "OBJECTIVES" not in child_prompt(**child_args)
    assert "OBJECTIVES" in child_prompt(**child_args, problem_spec="OBJECTIVES")
