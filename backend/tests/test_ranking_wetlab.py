from __future__ import annotations

from app.services.evaluation import Candidate, evaluate_all
from app.services.ranking import rank
from app.services.wetlab import build_pack, to_csv, to_fasta

GB1 = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"


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
    assert all(a["predicted_signal"] for a in entry["assay_plan"])
    assert entry["cost"]["total_usd"] > 0
    assert entry["citations"]
    assert entry["why"]
    econ = pack["economics"]
    assert econ["shortlist_cost_usd"] <= econ["test_everything_cost_usd"]
    assert econ["savings_usd"] >= 0
    assert pack["risks"]

    csv_text = to_csv(pack)
    assert csv_text.splitlines()[0].count(",") >= 5
    assert len(csv_text.splitlines()) == len(pack["shortlist"]) + 1

    fasta = to_fasta(pack)
    assert fasta.startswith(">")
    assert fasta.count(">") == len(pack["shortlist"])
