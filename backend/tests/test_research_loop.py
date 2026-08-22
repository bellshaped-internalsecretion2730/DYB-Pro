"""The research daemon, the wet-lab loop and the calibration that connects them."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import get_settings
from app.models import DaemonTask, ResidueLabel, utcnow
from app.services import daemon as daemon_svc
from app.services import lab_research as research_svc
from app.services import labels as label_svc
from app.services import wetlab_loop
from app.versioning import commit_design


@pytest.fixture
def campaign(db, project, root_commit):
    rp = research_svc.ensure_research_project(db, project)
    research_svc.upsert_metric_definitions(db)
    db.commit()
    return rp


# ------------------------------------------------------------------ queue


def test_enqueue_debounces_a_burst_without_dropping_any_event(db, campaign, root_commit):
    tasks = [
        daemon_svc.enqueue(db, campaign, "label_changed", commit_id=root_commit.id,
                           payload={"i": i})
        for i in range(5)
    ]
    db.commit()
    queued = [t for t in tasks if t.status == "queued"]
    coalesced = [t for t in tasks if t.status == "coalesced"]
    assert len(queued) == 1
    assert len(coalesced) == 4  # kept, not deleted: the ledger still shows five events
    assert queued[0].coalesced_count == 4
    assert all(t.coalesced_into == queued[0].id for t in coalesced)
    assert len(queued[0].payload["coalesced"]) == 4
    # the whole burst is still in the database
    rows = db.query(DaemonTask).filter(DaemonTask.research_project_id == campaign.id).count()
    assert rows == 5


def test_debounce_window_defers_work_and_claim_respects_it(db, campaign, root_commit):
    task = daemon_svc.enqueue(db, campaign, "manual_refresh", commit_id=root_commit.id,
                              debounce_seconds=30)
    db.commit()
    assert daemon_svc.claim_next(db, campaign) is None  # not due yet
    claimed = daemon_svc.claim_next(db, campaign, now=utcnow() + timedelta(seconds=31))
    assert claimed is not None and claimed.id == task.id
    assert claimed.status == "processing" and claimed.attempts == 1


def test_different_keys_do_not_coalesce(db, campaign, root_commit):
    a = daemon_svc.enqueue(db, campaign, "label_changed", commit_id=root_commit.id)
    b = daemon_svc.enqueue(db, campaign, "version_created", commit_id=root_commit.id)
    db.commit()
    assert a.status == "queued" and b.status == "queued"


def test_failed_pass_is_retried_then_marked_failed(db, campaign, root_commit, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("literature source exploded")

    monkeypatch.setattr(daemon_svc, "research_pass", boom)
    daemon_svc.enqueue(db, campaign, "manual_refresh", commit_id=root_commit.id,
                       debounce_seconds=0)
    db.commit()
    attempts = 0
    while True:
        claimed = daemon_svc.claim_next(db, campaign, now=utcnow() + timedelta(hours=1))
        if claimed is None:
            break
        attempts += 1
        out = daemon_svc.run_task(db, claimed)
        assert "literature source exploded" in claimed.error
        if out["status"] == "failed":
            break
    assert attempts == get_settings().daemon_max_attempts  # retried, then failed loudly
    assert campaign.daemon_status == "error"


def test_task_without_a_known_commit_falls_back_to_the_head_version(db, campaign, root_commit):
    task = daemon_svc.enqueue(db, campaign, "manual_refresh", commit_id=None, debounce_seconds=0)
    db.commit()
    claimed = daemon_svc.claim_next(db, campaign)
    assert claimed is not None and claimed.id == task.id
    assert daemon_svc.run_task(db, claimed)["status"] == "done"


def test_status_reports_queue_depth_and_provider(db, campaign, root_commit):
    daemon_svc.enqueue(db, campaign, "version_created", commit_id=root_commit.id)
    db.commit()
    status = daemon_svc.daemon_status(db, campaign)
    assert status["queue_depth"] == 1
    assert status["provider"] in {"local-simulation", "devin"}
    assert status["debounce_seconds"] > 0


# ------------------------------------------------------------------ labels


def test_labels_are_versioned_and_refinement_supersedes_the_parent(db, project, root_commit):
    parent = label_svc.create_label(
        db, root_commit, kind="liability", name="hydrophobic patch", residues=[3, 4, 5],
        note="surface exposed",
    )
    child = label_svc.create_label(
        db, root_commit, kind="liability", name="hydrophobic patch", residues=[4, 5],
        note="narrowed after inspection", parent_label_id=parent.id,
    )
    db.commit()
    assert child.version == parent.version + 1
    assert parent.superseded_by == child.id
    live = label_svc.labels_for_commit(db, root_commit.id)
    assert [lb.id for lb in live] == [child.id]
    assert len(label_svc.labels_for_commit(db, root_commit.id, include_superseded=True)) == 2
    tree = label_svc.label_tree(db, root_commit.id)
    assert tree and tree[0]["superseded"] is True
    assert tree[0]["refinements"][0]["residues"] == [4, 5]


def test_label_validation_rejects_out_of_range_and_unknown_kinds(db, root_commit):
    with pytest.raises(ValueError):
        label_svc.create_label(db, root_commit, kind="vibes", name="x", residues=[1])
    with pytest.raises(ValueError):
        label_svc.create_label(db, root_commit, kind="note", name="x",
                               residues=[len(root_commit.sequence) + 5])


# ------------------------------------------------------------- wet-lab loop


def test_prediction_carries_uncertainty_and_skill_provenance(db, campaign, root_commit):
    pred = wetlab_loop.predict_metrics(db, campaign, root_commit)
    assert pred["predictions"]
    for name, p in pred["predictions"].items():
        assert p["skill"].startswith("skill."), name
        assert p["method"], name
        if isinstance(p["value"], int | float):
            assert p["sd"] >= 0.0
    assert "melting_temperature" in pred["predictions"]


def test_offline_pass_caches_bundled_papers_with_attributed_citations(db, campaign, root_commit):
    lit = research_svc.run_literature_pass(
        db, campaign, root_commit, ["protein melting temperature core packing"], allow_network=False
    )
    research_svc.cache_papers(db, campaign, lit, query="offline")
    db.commit()
    cached = research_svc.papers(db, campaign)
    assert cached
    assert {p.source for p in cached} == {"bundled-corpus"}  # honest: never a live source
    assert all(p.authors and "Anon" not in p.citation for p in cached)
    # a replay from the cache keeps the authors, so the citation stays attributed
    replay = research_svc.cached_corpus(db, campaign)
    assert all(record["authors"] for record in replay)


def test_every_risk_row_is_rendered_complete(db, campaign, root_commit):
    """A blank cell in the UI is a contract break: every row needs a level, detail and mitigation."""
    prediction = wetlab_loop.predict_metrics(db, campaign, root_commit)
    report = wetlab_loop.risk_report(prediction, root_commit)
    assert report["risks"]
    for row in report["risks"]:
        assert row["level"], row
        assert row["detail"], row
        assert row["mitigation"], row


def test_plan_is_ranked_by_information_per_dollar_and_has_thresholds(db, campaign, project, root_commit):
    plan = wetlab_loop.build_plan(db, campaign, project, root_commit)
    db.commit()
    assert plan.assays and plan.thresholds and plan.controls
    assert plan.total_cost_usd > 0
    assert plan.information_per_usd > 0
    assert plan.constructs and plan.constructs[0]["orf"]
    assert plan.constructs[0]["route"] in {"site-directed mutagenesis", "de-novo gene synthesis"}
    assert plan.skills_used
    assert plan.provider == "local-simulation"  # honest: no Devin key in tests
    costs = [a["cost_usd"] for a in plan.assays]
    assert costs == sorted(costs) or plan.assays[0]["information_per_usd"] >= plan.assays[-1][
        "information_per_usd"
    ]


def test_simulator_is_labelled_and_documents_its_error_model(db, campaign, project, root_commit):
    plan = wetlab_loop.build_plan(db, campaign, project, root_commit)
    result = wetlab_loop.simulate_results(db, campaign, plan, seed=7)
    db.commit()
    assert result.source == "simulator"
    assert result.error_model["seed"] == 7
    assert result.error_model["per_metric"]
    assert result.measurements
    assert result.residuals
    again = wetlab_loop.simulate_results(db, campaign, plan, seed=7)
    assert [m["value"] for m in again.measurements] == [m["value"] for m in result.measurements]


def test_result_parsing_accepts_csv_json_and_pasted_values():
    csv_rows = wetlab_loop.parse_result_rows(
        "metric,value,unit\nTm,61.2,C\nyield,18,mg/L\nexpressed,yes,\n"
    )
    assert {r["metric"] for r in csv_rows} == {"Tm", "yield", "expressed"}
    json_rows = wetlab_loop.parse_result_rows('[{"metric": "KD", "value": 42, "unit": "nM"}]')
    assert json_rows[0]["metric"] == "KD"
    wrapped = wetlab_loop.parse_result_rows('{"measurements": [{"metric": "Tm", "value": 59}]}')
    assert wrapped[0]["value"] == 59
    pasted = wetlab_loop.parse_result_rows("Tm, 58.4, C\nsoluble, 41, %")
    assert len(pasted) == 2


def test_residuals_compare_predicted_with_measured_per_metric(db, campaign, project, root_commit):
    plan = wetlab_loop.build_plan(db, campaign, project, root_commit)
    predicted_tm = plan.predictions["melting_temperature"]["value"]
    result = wetlab_loop.ingest_results(
        db, campaign, root_commit.id,
        [{"metric": "Tm", "value": predicted_tm - 6.0, "unit": "C"}],
        source="scientist", plan=plan,
    )
    db.commit()
    res = result.residuals["melting_temperature"]
    assert res["predicted"] == pytest.approx(predicted_tm)
    assert res["residual"] == pytest.approx(-6.0, abs=1e-6)
    assert res["z"] is not None
    assert plan.status == "complete"


def test_drift_calibration_shifts_the_next_prediction_towards_measurement(
    db, campaign, project, root_commit
):
    naive = wetlab_loop.predict_metrics(db, campaign, root_commit)["predictions"][
        "melting_temperature"
    ]["value"]
    for _ in range(3):
        plan = wetlab_loop.build_plan(db, campaign, project, root_commit)
        result = wetlab_loop.ingest_results(
            db, campaign, root_commit.id,
            [{"metric": "Tm", "value": naive - 8.0, "unit": "C"}],
            source="scientist", plan=plan,
        )
        wetlab_loop.update_drift(db, campaign, result)
    db.commit()
    drift = {d.metric: d for d in research_svc.drift_models(db, campaign)}
    assert "melting_temperature" in drift
    assert drift["melting_temperature"].n_observations == 3
    assert drift["melting_temperature"].bias < 0  # we over-predict Tm, and the model knows
    calibrated = wetlab_loop.predict_metrics(db, campaign, root_commit)["predictions"][
        "melting_temperature"
    ]
    assert calibrated["value"] < naive
    assert calibrated["calibrated"] is True


def test_hypotheses_are_resolved_by_measurements(db, campaign, project, root_commit):
    plan = wetlab_loop.build_plan(db, campaign, project, root_commit)
    research_svc.record_hypothesis(
        db, campaign, root_commit,
        statement="Tm is 90 C", metric="melting_temperature",
        predicted_value=90.0, predicted_sd=0.5,
    )
    result = wetlab_loop.ingest_results(
        db, campaign, root_commit.id, [{"metric": "Tm", "value": 55.0, "unit": "C"}],
        source="scientist", plan=plan,
    )
    resolved = research_svc.resolve_hypotheses(
        db, campaign, root_commit.id,
        {m["metric"]: m["value"] for m in result.measurements},
    )
    db.commit()
    assert resolved and resolved[0]["status"] == "refuted"


# --------------------------------------------------------- full daemon pass


def test_full_research_pass_writes_immutable_events_and_a_proposal(
    db, campaign, project, root_commit
):
    label_svc.create_label(
        db, root_commit, kind="liability", name="exposed patch", residues=[10, 11],
        note="watch aggregation",
    )
    daemon_svc.enqueue(db, campaign, "version_created", commit_id=root_commit.id,
                       debounce_seconds=0)
    db.commit()
    outcomes = daemon_svc.process_pending(db, campaign, limit=1)
    db.commit()
    assert outcomes and outcomes[0]["status"] == "done"
    kinds = [e.kind for e in research_svc.events(db, campaign)]
    assert {"version_created", "literature", "metrics", "wetlab_plan", "insight", "proposal"} <= set(
        kinds
    )
    # sequence numbers are dense and monotonic: the ledger is append-only
    seqs = sorted(e.sequence_no for e in research_svc.events(db, campaign))
    assert seqs == list(range(1, len(seqs) + 1))
    # a merged-path pass ran too
    assert any(e.payload.get("merged_path") for e in research_svc.events(db, campaign))
    proposal_event = next(e for e in research_svc.events(db, campaign) if e.kind == "proposal")
    assert proposal_event.payload["target_metric"]
    proposed = proposal_event.payload["proposed"]
    assert proposed and proposed["mutations"]
    assert campaign.daemon_status in {"live", "degraded"}
    assert campaign.knowledge_version >= 1


def test_proposal_respects_active_site_labels(db, campaign, project, root_commit):
    protected = list(range(1, len(root_commit.sequence) + 1))
    db.add(
        ResidueLabel(
            project_id=project.id, commit_id=root_commit.id, kind="active_site",
            name="everything", residues=protected,
        )
    )
    db.commit()
    from app.services import proposal as proposal_svc

    out = proposal_svc.propose_next(db, campaign, project, root_commit)
    assert out["proposed"] is None  # nothing may be mutated, and we say so rather than guessing
    assert "protected" in out["reason"] or "refuted" in out["reason"]


def test_results_event_triggers_learn_pass_and_recalibration(db, campaign, project, root_commit):
    plan = wetlab_loop.build_plan(db, campaign, project, root_commit)
    result = wetlab_loop.simulate_results(db, campaign, plan, seed=3)
    daemon_svc.enqueue(db, campaign, "results_registered", commit_id=root_commit.id,
                       payload={"result_id": result.id}, debounce_seconds=0)
    db.commit()
    outcomes = daemon_svc.process_pending(db, campaign, limit=1)
    db.commit()
    assert outcomes[0]["status"] == "done"
    learn = [e for e in research_svc.events(db, campaign) if e.kind == "learn"]
    assert learn and learn[0].payload["residuals"]
    assert learn[0].payload["drift"]
    assert research_svc.drift_models(db, campaign)


def test_learned_summary_spans_v1_to_latest(db, campaign, project, root_commit):
    child = commit_design(
        db,
        project_id=project.id,
        sequence=root_commit.sequence[:10] + "K" + root_commit.sequence[11:],
        message="v2",
        label="v2",
        parent_ids=[root_commit.id],
        scores={"composite_score": 0.8},
    )
    db.commit()
    learned = research_svc.learned_summary(db, campaign)
    assert len(learned["versions"]) == 2
    assert learned["versions"][-1]["label"] == child.label
    assert isinstance(learned["lessons"], list)
    assert isinstance(learned["generated_at"], str)  # payload must stay JSON-serialisable
