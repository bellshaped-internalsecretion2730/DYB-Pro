from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import AutonomyDecision, DesignCycle
from app.services import autonomy
from app.services.cycle import run_cycle
from tests.conftest import headers


def _start(db, project) -> DesignCycle:
    cycle = DesignCycle(
        project_id=project.id,
        brief="record autonomy",
        round=1,
        branch="main",
        acu_limit=20,
        status="queued",
    )
    db.add(cycle)
    db.commit()
    return cycle


def test_record_rejects_unknown_step_and_summary_handles_empty_ledger(db, project):
    with pytest.raises(ValueError, match="unknown autonomy step"):
        autonomy.record(
            db,
            project.id,
            "unknown",
            "x",
            actor="system",
            autonomy="autonomous",
            basis={},
            reversible=True,
            confidence_basis="basis",
        )
    summary = autonomy.summary(db, project.id)
    assert summary["decisions"] == 0
    assert summary["autonomous_fraction"] is None
    assert summary["override_rate"] is None
    assert summary["meaning"].startswith("fraction of recorded decision points")


def test_cycle_records_all_decision_steps(db, project, root_commit):
    cycle = run_cycle(db, _start(db, project).id, sleep=lambda *_: None)
    rows = list(
        db.scalars(
            select(AutonomyDecision).where(AutonomyDecision.cycle_id == cycle.id)
        )
    )
    assert {row.step for row in rows} == autonomy.STEPS
    assert {row.step: row.autonomy for row in rows}["plan"] == "agent_advised"
    assert {row.step: row.autonomy for row in rows}["fanout"] == "autonomous"
    assert {row.step: row.autonomy for row in rows}["ranking_order"] == "agent_advised"
    assert all(row.confidence_basis for row in rows)


def test_override_is_append_only_and_second_override_is_rejected(db, project, client):
    created = autonomy.record(
        db,
        project.id,
        "tier_choice",
        "tier=T2",
        actor="system",
        autonomy="autonomous",
        basis={"tier": "T2"},
        reversible=True,
        confidence_basis="catalogue",
    )
    db.commit()
    original = (created.decision, created.basis, created.autonomy)
    overridden = autonomy.override(db, created.id, "user-1", "human selected a different tier")
    db.commit()
    assert (overridden.decision, overridden.basis, overridden.autonomy) == original
    assert overridden.overridden_by == "user-1"
    with pytest.raises(ValueError, match="already"):
        autonomy.override(db, created.id, "user-2", "second opinion")

    response = client.get(
        f"/api/projects/{project.id}/autonomy-ledger", headers=headers("viewer")
    )
    assert response.status_code == 200
    assert response.json()["summary"]["override_count"] == 1
    response = client.post(
        f"/api/projects/{project.id}/autonomy-ledger/{created.id}/override",
        json={"reason": "another decision"},
        headers=headers("scientist"),
    )
    assert response.status_code == 409


def test_autonomy_endpoints_include_explicit_override_effect(client, project, db):
    decision = autonomy.record(
        db,
        project.id,
        "filter_gate",
        "passing=1",
        actor="system",
        autonomy="autonomous",
        basis={"passing": 1},
        reversible=True,
        confidence_basis="filter recall is unmeasured",
    )
    db.commit()
    response = client.post(
        f"/api/projects/{project.id}/autonomy-ledger/{decision.id}/override",
        json={"reason": "review required"},
        headers=headers("scientist"),
    )
    assert response.status_code == 200, response.text
    assert "recorded, not reverted" in response.json()["effect"]
