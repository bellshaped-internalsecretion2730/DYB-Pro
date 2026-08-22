from __future__ import annotations

import pytest

from app.models import ProteinCommit
from app.toolkit import sequence as seqlib
from app.versioning import (
    MergeConflict,
    branch_from,
    commit_design,
    diff_commits,
    export_graph,
    lineage,
    merge_branches,
)

GB1 = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"


def _child(db, project, parent, mutation: str, branch: str = "main", **kw):
    seq, applied = seqlib.apply_mutations(parent.sequence, [mutation])
    return commit_design(
        db,
        project_id=project.id,
        sequence=seq,
        message=f"design {mutation}",
        label=mutation,
        parent_ids=[parent.id],
        branch=branch,
        mutations=applied,
        agent_role="sequence",
        provider="local-simulation",
        **kw,
    )


def test_commit_id_is_content_hash_and_idempotent(db, project, root_commit):
    again = commit_design(
        db,
        project_id=project.id,
        sequence=root_commit.sequence,
        message=root_commit.message,
        label="wt",
        provider="upload",
        created_at=root_commit.created_at,
    )
    assert again.id == root_commit.id
    assert db.query(ProteinCommit).filter_by(id=root_commit.id).count() == 1


def test_commit_moves_branch_head_and_keeps_parents(db, project, root_commit):
    child = _child(db, project, root_commit, "T2K")
    assert child.parent_ids == [root_commit.id]
    graph = export_graph(db, project.id)
    main = next(b for b in graph["branches"] if b["name"] == "main")
    assert main["head"] == child.id
    assert {n["id"] for n in graph["nodes"]} >= {root_commit.id, child.id}
    assert {"source": root_commit.id, "target": child.id} in graph["edges"]


def test_lineage_walks_ancestors_and_descendants(db, project, root_commit):
    c1 = _child(db, project, root_commit, "T2K")
    c2 = _child(db, project, c1, "Y3F")
    ancestors = [c.id for c in lineage(db, c2.id, "ancestors")]
    assert ancestors == [c1.id, root_commit.id]  # walk excludes the commit itself
    descendants = [c.id for c in lineage(db, root_commit.id, "descendants")]
    assert {c1.id, c2.id} <= set(descendants)


def test_diff_reports_mutations_and_score_deltas(db, project, root_commit):
    child = _child(
        db,
        project,
        root_commit,
        "T2K",
        scores={"composite_score": 0.8, "solubility": 0.5},
        rationale="charge engineering",
    )
    out = diff_commits(db, root_commit.id, child.id)
    assert [m["mutation"] for m in out["mutations"]] == ["T2K"]
    assert out["identity"] == pytest.approx(1 - 1 / len(GB1), abs=1e-3)
    assert out["length_delta"] == 0
    assert out["score_delta"]["solubility"]["after"] == 0.5
    assert out["why"]["to_rationale"] == "charge engineering"
    assert out["why"]["to_provider"] == "local-simulation"
    assert out["common_ancestor"] == root_commit.id


def test_branch_and_merge_of_disjoint_positions(db, project, root_commit):
    ours = _child(db, project, root_commit, "T2K", branch="main")
    branch_from(db, project.id, "alt", root_commit.id)
    theirs = _child(db, project, root_commit, "Y3F", branch="alt")
    merged = merge_branches(db, project.id, "main", "alt", message="combine mechanisms")
    assert sorted(merged.parent_ids) == sorted([ours.id, theirs.id])
    assert merged.sequence[1] == "K" and merged.sequence[2] == "F"
    assert merged.scores["merge_estimated"] == 1.0
    assert merged.filters["failed"] == ["requires_rescoring"]


def test_merge_conflict_is_reported_not_resolved(db, project, root_commit):
    _child(db, project, root_commit, "T2K", branch="main")
    branch_from(db, project.id, "rival", root_commit.id)
    _child(db, project, root_commit, "T2D", branch="rival")
    with pytest.raises(MergeConflict) as exc:
        merge_branches(db, project.id, "main", "rival")
    assert exc.value.conflicts
    assert exc.value.conflicts[0]["position"] == 2
