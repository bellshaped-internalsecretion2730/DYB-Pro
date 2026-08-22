"""Drift calibration: measured results are the only place a hit rate may come from."""

from __future__ import annotations

from app.models import MeasuredResult
from app.services import calibration
from app.toolkit import sequence as seqlib
from app.versioning import commit_design

GB1 = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"


def _design(db, project, mutation: str, binding: float):
    seq, applied = seqlib.apply_mutations(GB1, [mutation])
    return commit_design(
        db,
        project_id=project.id,
        sequence=seq,
        message=f"design {mutation}",
        label=mutation,
        mutations=applied,
        provider="local-simulation",
        scores={"binding_score": binding, "composite_score": 0.5},
    )


def _measure(db, project, commit, value: float, outcome: str = "hit"):
    db.add(
        MeasuredResult(
            project_id=project.id,
            commit_id=commit.id,
            assay="SPR",
            objective="binding_score",
            value=value,
            unit="nM",
            higher_is_better=False,
            outcome=outcome,
        )
    )


def test_no_measurements_means_no_hit_rate(db, project, root_commit):
    out = calibration.project_calibration(db, project.id)
    assert out == {"measurements": 0, "hit_rate": None, "objectives": {}, "commits_measured": 0}


def test_agreement_is_withheld_until_enough_paired_designs(db, project, root_commit):
    for i, mutation in enumerate(["T2K", "Y3F", "K4R"]):
        commit = _design(db, project, mutation, binding=-100.0 - i)
        _measure(db, project, commit, value=10.0 + i)
    db.flush()
    out = calibration.project_calibration(db, project.id)
    assert out["measurements"] == 3
    assert out["objectives"]["binding_score"]["kendall_tau"] is None
    assert str(calibration.MIN_PAIRS_FOR_AGREEMENT) in out["objectives"]["binding_score"]["note"]


def test_perfect_rank_agreement_scores_tau_one(db, project, root_commit):
    mutations = ["T2K", "Y3F", "K4R", "L5I", "I6L", "L7I"]
    for i, mutation in enumerate(mutations):
        # A stronger (more negative) proxy score paired with a tighter (lower nM) measurement.
        commit = _design(db, project, mutation, binding=-100.0 - 10 * i)
        _measure(db, project, commit, value=100.0 - 10 * i)
    db.flush()
    out = calibration.project_calibration(db, project.id)
    entry = out["objectives"]["binding_score"]
    assert entry["pairs"] == len(mutations)
    assert entry["kendall_tau"] == 1.0
    assert out["hit_rate"] == 1.0


def test_anticorrelated_proxy_is_reported_as_negative_agreement(db, project, root_commit):
    mutations = ["T2K", "Y3F", "K4R", "L5I", "I6L", "L7I"]
    for i, mutation in enumerate(mutations):
        commit = _design(db, project, mutation, binding=-100.0 - 10 * i)
        _measure(db, project, commit, value=10.0 + 10 * i, outcome="miss")
    db.flush()
    out = calibration.project_calibration(db, project.id)
    assert out["objectives"]["binding_score"]["kendall_tau"] == -1.0
    assert out["hit_rate"] == 0.0


def test_kendall_tau_needs_two_pairs():
    assert calibration.kendall_tau([(1.0, 1.0)]) is None
    assert calibration.kendall_tau([(1.0, 1.0), (1.0, 1.0)]) is None
    assert calibration.kendall_tau([(1.0, 2.0), (2.0, 1.0)]) == -1.0
