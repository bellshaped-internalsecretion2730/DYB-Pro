"""The seeded demo campaign must produce the history the 90-second demo path shows."""

from __future__ import annotations

from app.models import Project
from app.seed import DEMO_FASTA
from app.services import demo_campaign, ingest
from app.services import lab_research as research_svc
from app.versioning import get_branch


def _demo_project(db) -> Project:
    project = Project(
        name="seeded campaign test",
        goal="raise thermal stability without losing Fc binding",
        target_name="",
        target_sequence="",
    )
    db.add(project)
    db.flush()
    get_branch(db, project.id, "main", create=True)
    ingest.ingest_file(db, project, "gb1-wildtype.fasta", DEMO_FASTA.encode())
    db.commit()
    return project


def test_seed_campaign_builds_three_versions_with_measured_history(db):
    project = _demo_project(db)
    summary = demo_campaign.seed_campaign(db, project)
    db.commit()

    assert summary["seeded"] is True
    assert len(summary["versions"]) == 3, summary["versions"]
    # the bundled corpus means the demo has citable evidence with no network at all
    assert summary["papers"] > 0

    rp = research_svc.ensure_research_project(db, project)
    results = research_svc.results_for(db, rp)
    assert len(results) == 3
    assert {r.source for r in results} == {"simulator"}
    # the injected optimism is documented on every simulated row, never hidden
    for result in results:
        assert result.error_model["systematic_bias"]["offsets"] == demo_campaign.DEMO_LAB_BIAS

    # each version was researched and measured, and the ledger kept every step
    kinds = {e.kind for e in research_svc.events(db, rp, limit=400)}
    assert {"version_created", "literature", "metrics", "wetlab_plan", "learn", "insight",
            "proposal"} <= kinds

    # recalibration removes the systematic offset, so drift shrinks across the campaign
    drift = {d.metric: d for d in research_svc.drift_models(db, rp)}
    tm = drift["melting_temperature"]
    assert tm.n_observations == 3
    first, last = tm.history[0], tm.history[-1]
    assert abs(last["residual"]) < abs(first["residual"])

    learned = research_svc.learned_summary(db, rp)
    assert learned["headline"]
    assert learned["drift"]


def test_seed_campaign_is_idempotent(db):
    project = _demo_project(db)
    demo_campaign.seed_campaign(db, project)
    db.commit()
    again = demo_campaign.seed_campaign(db, project)
    assert again["seeded"] is False
