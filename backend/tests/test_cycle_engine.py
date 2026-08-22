"""End-to-end design cycle on the explicitly-labelled local simulation provider."""

from __future__ import annotations

from sqlalchemy import select

from app.models import AgentRun, DesignCycle, Observation, ProteinCommit
from app.services import learning
from app.services.cycle import run_cycle
from app.versioning import get_branch


def _start(db, project, brief="round 1: raise stability, keep binding") -> DesignCycle:
    cycle = DesignCycle(
        project_id=project.id, brief=brief, round=1, branch="main", acu_limit=20, status="queued"
    )
    db.add(cycle)
    db.commit()
    return cycle


def test_cycle_runs_end_to_end_and_labels_the_simulation_provider(db, project, root_commit):
    cycle = run_cycle(db, _start(db, project).id, sleep=lambda *_: None)

    assert cycle.status in {"committed", "partial"}, cycle.error
    assert cycle.provider == "local-simulation"  # never claims to be Devin
    assert cycle.plan.get("agents")
    assert cycle.summary

    runs = list(db.scalars(select(AgentRun).where(AgentRun.cycle_id == cycle.id)))
    roles = {r.role for r in runs}
    assert "orchestrator" in roles
    assert roles - {"orchestrator"}  # the orchestrator fanned out
    assert all(r.provider == "local-simulation" for r in runs)
    assert all(r.tags for r in runs)
    assert "ranking" in roles
    assert [r.role for r in runs if r.status != "finished"] == [], [r.error for r in runs]

    commits = list(
        db.scalars(
            select(ProteinCommit).where(
                ProteinCommit.project_id == project.id, ProteinCommit.cycle_id == cycle.id
            )
        )
    )
    assert commits
    for c in commits:
        assert c.parent_ids == [root_commit.id]
        assert c.provider == "local-simulation"
        assert c.agent_role
        assert c.rationale
        assert c.scores.get("composite_score") is not None
        assert c.structure_source.startswith("model:")
        assert c.prompt


def test_cycle_produces_a_ranked_shortlist_with_economics(db, project, root_commit):
    cycle = run_cycle(db, _start(db, project).id, sleep=lambda *_: None)
    pack = cycle.shortlist["pack"]
    ranked = cycle.shortlist["ranked"]

    assert ranked and ranked[0]["rank"] == 1
    assert ranked[0]["why"]
    assert pack["shortlist"]
    assert pack["economics"]["savings_usd"] >= 0
    assert cycle.shortlist["narrative"]["headline"]


def test_cycle_writes_an_observation_trail_the_next_round_can_learn_from(db, project, root_commit):
    cycle = run_cycle(db, _start(db, project).id, sleep=lambda *_: None)
    kinds = {
        o.kind
        for o in db.scalars(select(Observation).where(Observation.cycle_id == cycle.id))
    }
    assert {"cycle_started", "plan", "cycle_finished"} <= kinds

    digest = learning.history_digest(db, project)
    assert digest["cycles_completed"] >= 1
    assert digest["commit_count"] >= 1
    assert digest["mutation_ledger"]  # the next round knows what has been tried
    assert learning.digest_to_prompt(digest)


def test_second_cycle_builds_on_the_new_branch_head(db, project, root_commit):
    first = run_cycle(db, _start(db, project).id, sleep=lambda *_: None)
    head = get_branch(db, project.id, "main").head_commit_id
    if first.status == "committed":
        assert head != root_commit.id  # best passing design became the head

    second = DesignCycle(
        project_id=project.id,
        brief="round 2: build on the winner",
        round=2,
        branch="main",
        acu_limit=20,
        status="queued",
    )
    db.add(second)
    db.commit()
    second = run_cycle(db, second.id, sleep=lambda *_: None)
    assert second.status in {"committed", "partial"}, second.error
    children = list(
        db.scalars(select(ProteinCommit).where(ProteinCommit.cycle_id == second.id))
    )
    assert children
    assert all(c.parent_ids == [head] for c in children)


def test_cycle_fails_cleanly_when_the_branch_has_no_commits(db, project):
    cycle = run_cycle(db, _start(db, project).id, sleep=lambda *_: None)
    assert cycle.status == "failed"
    assert "upload" in (cycle.error or "")
