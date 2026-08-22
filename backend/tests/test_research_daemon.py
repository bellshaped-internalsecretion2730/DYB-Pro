"""The research daemon: durable queue, coalescing debounce, cache reuse, tenancy."""

from __future__ import annotations

import time

from sqlalchemy import select

from app.config import get_settings
from app.models import MeasuredResult, ResearchEvent, ResearchNote
from app.services import research
from tests.conftest import headers


def test_triggers_coalesce_into_one_queued_event_without_dropping_any(db, project):
    first = research.enqueue(db, project.id, "commit", ref="a")
    for ref in ("b", "c", "d"):
        research.enqueue(db, project.id, "commit", ref=ref)
    db.commit()

    events = list(
        db.scalars(select(ResearchEvent.id).where(ResearchEvent.project_id == project.id))
    )
    assert len(events) == 1
    assert first.coalesced == 3
    assert [t["ref"] for t in first.triggers] == ["a", "b", "c", "d"]


def test_debounce_delays_processing_but_never_discards(db, project):
    event = research.enqueue(db, project.id, "commit")
    db.commit()
    settings = get_settings()

    assert event.id not in [e.id for e in research.queued_events(db, settings)]
    ready = research.queued_events(
        db, settings, now=time.time() + settings.research_debounce_seconds + 1
    )
    assert event.id in [e.id for e in ready]


def test_event_records_change_drift_and_writes_the_cache(db, project, root_commit):
    event = research.enqueue(db, project.id, "commit", ref=root_commit.id)
    db.commit()

    done = research.process_event(db, event.id)

    assert done.status == "done", done.error
    assert done.provider == "local-simulation"
    assert done.changed["new_commit_count"] >= 1
    assert done.head_commit_id == root_commit.id
    assert done.metrics["geometry"]["is_model"] is True
    assert done.cache_writes > 0
    assert done.summary
    notes = list(
        db.scalars(select(ResearchNote.topic_key).where(ResearchNote.project_id == project.id))
    )
    assert notes


def test_second_event_reuses_the_cache_instead_of_re_researching(db, project, root_commit):
    first = research.process_event(db, research.enqueue(db, project.id, "commit").id)
    assert first.cache_writes > 0

    second = research.process_event(db, research.enqueue(db, project.id, "manual").id)

    assert second.cache_hits >= 1
    assert second.cache_writes == 0
    reused = max(
        db.scalars(select(ResearchNote.reuse_count).where(ResearchNote.project_id == project.id))
    )
    assert reused >= 1
    assert any(f["from_cache"] for f in second.findings)


def test_a_measured_result_is_a_change_the_daemon_reacts_to(db, project, root_commit):
    db.add(
        MeasuredResult(
            project_id=project.id,
            commit_id=root_commit.id,
            assay="nanoDSF",
            objective="stability",
            readout="Tm",
            value=51.2,
            unit="degC",
            outcome="miss",
        )
    )
    db.commit()

    event = research.process_event(db, research.enqueue(db, project.id, "measured_result").id)

    assert event.status == "done", event.error
    assert event.changed["new_measurements"][0]["assay"] == "nanoDSF"
    assert "miss:stability" in [f["topic_key"] for f in event.findings]
    assert event.drift["measurements"] == 1


def test_a_failed_event_stays_visible_as_failed(db, project, monkeypatch):
    event = research.enqueue(db, project.id, "manual")
    db.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(research, "detect_change", boom)
    failed = research.process_event(db, event.id)

    assert failed.status == "failed"
    assert "provider exploded" in (failed.error or "")


def test_notes_are_never_discarded_by_a_later_event(db, project, root_commit):
    research.process_event(db, research.enqueue(db, project.id, "commit").id)
    before = list(db.scalars(select(ResearchNote.id).where(ResearchNote.project_id == project.id)))
    research.process_event(db, research.enqueue(db, project.id, "manual").id)
    after = list(db.scalars(select(ResearchNote.id).where(ResearchNote.project_id == project.id)))

    assert set(before) <= set(after)


def test_research_routes_report_the_daemon_and_its_cache(client, demo_project_id):
    status = client.get("/api/research/daemon", headers=headers("viewer")).json()
    assert status["enabled"] is True
    assert status["provider"] == "local-simulation"

    triggered = client.post(
        f"/api/projects/{demo_project_id}/research", headers=headers("scientist")
    )
    assert triggered.status_code == 200
    event_id = triggered.json()["id"]

    fetched = client.get(f"/api/research/events/{event_id}", headers=headers("viewer"))
    assert fetched.status_code == 200
    assert fetched.json()["status"] in {"queued", "running", "done"}

    pane = client.get(f"/api/projects/{demo_project_id}/research", headers=headers("viewer")).json()
    assert pane["daemon"]["enabled"] is True
    assert any(e["id"] == event_id for e in pane["events"])
    assert "append-only" in pane["cache"]["note"]


def test_unknown_research_event_is_404(client):
    assert client.get("/api/research/events/nope", headers=headers("viewer")).status_code == 404


def test_viewer_cannot_trigger_research(client, demo_project_id):
    response = client.post(
        f"/api/projects/{demo_project_id}/research", headers=headers("viewer")
    )
    assert response.status_code == 403
