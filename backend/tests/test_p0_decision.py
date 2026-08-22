from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.models import MeasuredResult
from app.services.economics import (
    DEFAULT_PRIOR,
    cost_per_validated_hit,
    expected_hits,
    filter_performance,
    p_at_least_one,
    recommended_n,
    validate_prior,
    wilson,
)
from app.services.prices import PriceItem, item_cost, item_cost_interval
from app.services.ranking import Cluster, RankedCandidate, cluster_candidates, round_robin_clusters
from tests.conftest import headers


def test_item_cost_rounds_batches_and_honours_minimum():
    item = PriceItem("x", "per_sample", 0.2, 5.0, 10, "vendor", "source", "2026-08")
    assert item_cost(item, 1) == 5.0
    rounded = PriceItem("rounded", "per_sample", 2.0, 5.0, 10, "vendor", "source", "2026-08")
    assert item_cost(rounded, 11) == 40.0
    low, high = item_cost_interval(
        PriceItem("range", "per_sample", 5.0, 0.0, 10, "vendor", "source", "2026-08", 4.0, 8.0),
        11,
    )
    assert (low, high) == (80.0, 160.0)


def test_prior_shrinkage_and_override_validation():
    assert DEFAULT_PRIOR == (0.05, 0.23)
    assert validate_prior(0.2, 0.4) == (0.2, 0.4, "user-supplied")
    with pytest.raises(ValueError):
        validate_prior(-0.1, 0.4)
    with pytest.raises(ValueError):
        validate_prior(0.6, 0.4)


def test_independent_hit_functions_are_monotone_and_cost_interval_ordered():
    assert p_at_least_one(0.1, 3) > p_at_least_one(0.1, 2)
    assert expected_hits(0.1, 3) > expected_hits(0.1, 2)
    low, high = cost_per_validated_hit(100.0, 3)
    assert low <= high
    assert cost_per_validated_hit(100.0, 0, (0.0, 0.0)) is None


@dataclass
class _Candidate:
    label: str
    sequence: str


def _clusters(n: int) -> list[Cluster]:
    return [Cluster(_Candidate(f"c{i}", "A" * (10 + i)), []) for i in range(n)]


def test_stop_rule_has_target_budget_and_exhaustion_outcomes():
    clusters = _clusters(40)
    for cluster in clusters:
        cluster.members = [cluster.representative]
    ample = recommended_n(clusters, "T0", 10000.0)
    assert ample["stop_reason"] == "target_confidence_reached"
    tight = recommended_n(clusters, "T0", 1.0)
    assert tight["stop_reason"] == "budget_exhausted"
    assert tight["budget_limited"] is True
    exhausted = recommended_n(_clusters(2), "T0", None)
    assert exhausted["stop_reason"] == "candidates_exhausted"


def _ranked(label: str, sequence: str, rank: int, passed: bool = True) -> RankedCandidate:
    return RankedCandidate(
        label=label,
        rank=rank,
        composite=1.0 - rank / 100,
        confidence=0.9,
        normalized={"solubility": 0.5},
        scores={"solubility": 1.0},
        uncertainty={"solubility": 0.1},
        passed_filters=passed,
        failed_filters=[] if passed else ["solubility"],
        pareto=rank == 1,
        why="test",
        payload={"sequence": sequence},
    )


def test_clustering_uses_alignment_length_and_shortlist_is_round_robin():
    parent = "A" * 30
    variants = [_ranked(f"v{i}", parent[:i] + "C" + parent[i + 1 :], i) for i in range(20)]
    assert len(cluster_candidates(variants)) == 1
    unrelated = [_ranked(f"u{i}", "A" * 30 + "C" * i, i) for i in range(1, 5)]
    assert len(cluster_candidates(unrelated, threshold=0.99)) == 4
    short = round_robin_clusters(
        [
            Cluster(variants[0], variants[:2]),
            Cluster(unrelated[0], unrelated[:2]),
        ],
        3,
    )
    assert [item[0].label for item in short] == ["v0", "u1", "v1"]
    prefix = _ranked("prefix", "A" * 30, 1)
    long = _ranked("long", "A" * 30 + "C" * 270, 2)
    assert cluster_candidates([prefix, long], threshold=0.9)[1].representative.label == "long"


@dataclass
class _Evaluation:
    label: str
    sequence: str
    mutations: list[dict] = field(default_factory=list)
    filters: dict = field(default_factory=lambda: {"passed": True, "failed": []})
    geometry_usable: bool = True
    citations: list[str] = field(default_factory=lambda: ["test source"])
    structure_source: str = "model:test"
    profile: dict = field(default_factory=dict)
    structure_pdb: str = ""
    rationale: str = ""


def test_filter_performance_withholds_small_samples_and_wilson_contains_point():
    rows = [
        (type("Commit", (), {"filters": {"passed": i < 5}})(), type("Result", (), {"outcome": "hit" if i < 6 else "miss"})())
        for i in range(9)
    ]
    small = filter_performance(rows)
    assert small["status"] == "insufficient_data"
    assert all(small[name] is None for name in ["ppv", "npv", "sensitivity", "specificity", "fnr"])
    assert wilson(5, 10)[0] <= 0.5 <= wilson(5, 10)[1]


def test_filter_performance_endpoint_reports_confusion_matrix(client, project, db):
    from app.versioning import commit_design

    for i in range(10):
        commit = commit_design(
            db,
            project_id=project.id,
            sequence="A" * (20 + i),
            label=f"perf-{i}",
            message="performance fixture",
            filters={"passed": i < 5},
        )
        db.add(
            MeasuredResult(
                project_id=project.id,
                commit_id=commit.id,
                assay="test",
                value=float(i),
                outcome="hit" if i in {0, 1, 2, 5, 6, 7} else "miss",
            )
        )
    db.commit()
    response = client.get(
        f"/api/projects/{project.id}/filter-performance", headers=headers("viewer")
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["tp"], body["fp"], body["tn"], body["fn"]) == (3, 2, 2, 3)
    assert body["status"] == "ok"
    assert body["ppv_wilson_95"][0] <= body["ppv"] <= body["ppv_wilson_95"][1]


def test_thermostability_cannot_use_t0():
    from app.services.economics import validate_tier_for_readout

    with pytest.raises(ValueError):
        validate_tier_for_readout("T0", "thermostability")


def test_filter_recall_probe_is_seeded_separate_and_excluded_from_expectations():
    from app.services.wetlab import build_pack

    passing = _ranked("passing", "A" * 20, 1)
    failing = _ranked("failing", "A" * 19 + "C", 2, passed=False)
    evaluations = {
        "passing": _Evaluation("passing", "A" * 20),
        "failing": _Evaluation(
            "failing",
            "A" * 19 + "C",
            mutations=[{"mutation": "A20C", "wt": "A", "position": 20, "mt": "C"}],
            filters={"passed": False, "failed": ["solubility"]},
        ),
    }
    first = build_pack(
        [passing, failing],
        evaluations,
        "A" * 20,
        "probe-test",
        top_n=1,
        probe_seed=17,
        cycle_id="cycle-1",
    )
    second = build_pack(
        [passing, failing],
        evaluations,
        "A" * 20,
        "probe-test",
        top_n=1,
        probe_seed=17,
        cycle_id="cycle-2",
    )
    assert first["probes"] == second["probes"]
    assert len(first["probes"]) == 1
    assert first["probes"][0]["probe"] is True
    assert first["probes"][0]["failed_filters"] == ["solubility"]
    assert first["independent_bets"] == first["economics"]["n_eff"] == 1
    assert first["economics"]["n_tested"] == 1
    assert first["economics"]["probe_cost_usd"] > 0
