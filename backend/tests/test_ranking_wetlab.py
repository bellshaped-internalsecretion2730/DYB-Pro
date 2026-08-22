from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.evaluation import Candidate, evaluate_all
from app.services.ranking import rank
from app.services.wetlab import build_pack, to_csv, to_fasta

GB1 = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"


@dataclass
class _Stub:
    """Minimal evaluation-shaped object, to control exactly which objectives are scored."""

    label: str
    scores: dict
    uncertainty: dict = field(default_factory=dict)
    filters: dict = field(default_factory=lambda: {"passed": True, "failed": []})
    rationale: str = ""
    mutations: list = field(default_factory=list)
    sequence: str = GB1
    geometry_usable: bool = True


def _evaluations():
    candidates = [
        Candidate(label="design-1", parent_sequence=GB1, mutations=["T2K"], rationale="surface charge"),
        Candidate(label="design-2", parent_sequence=GB1, mutations=["L5D", "I6D"], rationale="core polar"),
        Candidate(label="design-3", parent_sequence=GB1, mutations=["A24E"], rationale="helix dipole"),
        Candidate(label="broken", parent_sequence=GB1, mutations=["A2K"], rationale="wrong wildtype"),
    ]
    return evaluate_all(candidates)


def test_evaluation_rejects_invalid_candidates_without_crashing():
    evals = _evaluations()
    by_label = {e.label: e for e in evals}
    assert by_label["broken"].scores == {} or by_label["broken"].filters.get("failed")
    assert by_label["design-1"].scores
    assert by_label["design-1"].structure_source.startswith("model:")


def test_ranking_is_multi_objective_with_uncertainty_and_explanations():
    ranked = rank(_evaluations())
    assert ranked
    assert [r.rank for r in ranked] == sorted(r.rank for r in ranked)
    top = ranked[0]
    assert 0.0 <= top.composite <= 1.0
    assert 0.0 <= top.confidence <= 1.0
    assert top.why
    assert any(r.pareto for r in ranked)
    if len(ranked) > 1:
        assert ranked[0].why_not_next


def test_composite_scores_are_comparable_across_cohorts():
    """Fixed normalisation windows: a design's score must not move because its cohort changed."""
    evals = _evaluations()
    full = {r.label: r.composite for r in rank(evals)}
    subset = {r.label: r.composite for r in rank(evals[:2])}
    for label, score in subset.items():
        assert abs(score - full[label]) < 1e-9


def test_missing_objectives_are_reported_not_scored_as_zero():
    """A candidate lacking an objective must not be scored as if it had the worst possible value."""
    full = _Stub("full", {"solubility": 2.0, "aggregation": 0.1})
    partial = _Stub("partial", {"solubility": 2.0})
    ranked = {r.label: r for r in rank([full, partial])}
    assert ranked["partial"].as_dict()["missing_objectives"] == ["aggregation"]
    assert ranked["full"].as_dict()["missing_objectives"] == []
    assert ranked["partial"].composite == pytest.approx(ranked["full"].normalized["solubility"])
    assert "not a probability" in ranked["partial"].as_dict()["confidence_meaning"]


def test_exclusions_from_history_are_demoted_with_a_reason():
    evals = _evaluations()
    label = evals[0].label
    ranked = rank(evals, exclusions={label: "failed expression in round 1"})
    excluded = next(r for r in ranked if r.label == label)
    assert excluded.excluded_reason == "failed expression in round 1"
    assert excluded.rank == max(r.rank for r in ranked)


def test_wetlab_pack_has_orderable_content_and_cost_comparison():
    evals = _evaluations()
    ranked = rank(evals)
    pack = build_pack(
        ranked,
        {e.label: e for e in evals},
        parent_sequence=GB1,
        project_name="test",
        top_n=3,
        total_candidate_pool=len(evals),
    )
    assert pack["shortlist"]
    entry = pack["shortlist"][0]
    assert entry["sequence"]
    assert entry["construct"]["orf_length_bp"] % 3 == 0
    assert entry["assay_plan"]
    for assay in entry["assay_plan"]:
        # Each assay states which proxy it tests and how to decide; it never predicts the outcome.
        assert assay["tests_in_silico_proxy"]
        assert assay["decision_rule"]
        assert "predicted_signal" not in assay
    assert entry["cost"]["total_usd"] > 0
    assert entry["citations"]
    assert entry["why"]
    econ = pack["economics"]
    assert econ["spend_avoided_usd"] >= 0
    assert econ["candidate_pool"] == len(evals)
    assert pack["risks"]
    assert pack["primers_orderable"] is False  # no user plasmid was supplied

    csv_text = to_csv(pack)
    assert csv_text.splitlines()[0].count(",") >= 5
    assert len(csv_text.splitlines()) == len(pack["shortlist"]) + 1

    fasta = to_fasta(pack)
    assert fasta.startswith(">")
    assert fasta.count(">") == len(pack["shortlist"])
