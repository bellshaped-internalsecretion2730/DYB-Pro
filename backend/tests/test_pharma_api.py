"""Pharmakon API: the autonomous loop as a caller experiences it.

Everything runs against the offline provider (no Devin credentials in tests), which is exactly
the case where the system must be most careful: it may not invent literature, patent or
target-validation evidence, and it must say so instead of guessing.
"""

from __future__ import annotations

import pytest

from app.pharma.stages import STAGE_KEYS
from tests.conftest import headers


@pytest.fixture(scope="module")
def program(client, demo_project_id) -> dict:
    body = {
        "name": "api test program",
        "target_name": "demo target",
        "target_sequence": "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE",
        "indication": "test indication",
        "objective": "exercise the ladder",
        "autonomy_level": 2,
        "seed_smiles": ["NC(=O)c1ccccc1", "Nc1ccncc1"],
    }
    res = client.post(
        f"/api/projects/{demo_project_id}/programs", json=body, headers=headers("scientist")
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def get_program(client, program_id: str) -> dict:
    res = client.get(f"/api/pharma/programs/{program_id}", headers=headers("viewer"))
    assert res.status_code == 200, res.text
    return res.json()


def test_ladder_endpoint_describes_every_stage(client):
    res = client.get("/api/pharma/ladder", headers=headers("viewer"))
    assert res.status_code == 200
    body = res.json()
    assert [s["key"] for s in body["stages"]] == list(STAGE_KEYS)
    assert body["autonomy_levels"]


def test_unparseable_seed_is_reported_not_silently_dropped(client, demo_project_id):
    res = client.post(
        f"/api/projects/{demo_project_id}/programs",
        json={"name": "bad seeds", "seed_smiles": ["NC(=O)c1ccccc1", "not-a-molecule"]},
        headers=headers("scientist"),
    )
    assert res.status_code == 400
    rejected = res.json()["detail"]["rejected"]
    assert [r["smiles"] for r in rejected] == ["not-a-molecule"]
    assert rejected[0]["error"]


def test_program_creation_commits_its_seeds(client, program):
    assert program["current_stage"] == STAGE_KEYS[0]
    assert program["molecule_count"] == 2
    mols = client.get(
        f"/api/pharma/programs/{program['id']}/molecules", headers=headers("viewer")
    ).json()
    assert {m["smiles"] for m in mols} == {"NC(=O)c1ccccc1", "Nc1ccncc1"}
    assert all(m["provider"] == "human" for m in mols)


def test_offline_round_runs_and_labels_its_provider(client, program):
    res = client.post(
        f"/api/pharma/programs/{program['id']}/advance", headers=headers("scientist")
    )
    assert res.status_code == 200, res.text
    detail = get_program(client, program["id"])
    rnd = client.get(
        f"/api/pharma/programs/{program['id']}/rounds", headers=headers("viewer")
    ).json()[0]
    assert rnd["provider"] == "local-simulation"
    assert rnd["status"] == "gated"
    assert detail["latest_gate"]["decision"] in ("go", "no_go", "recycle", "kill")


def test_offline_target_evidence_recycles_instead_of_killing(client, program):
    """The offline provider cannot read literature, so the gate must hold, not kill."""
    detail = get_program(client, program["id"])
    gate = detail["latest_gate"]
    assert gate["decision"] != "kill"
    assert detail["program"]["status"] != "killed"
    missing = {c["key"] for c in gate["criteria"] if c["missing_evidence"]}
    assert "target_evidence_score" in missing


def test_metric_without_citation_is_rejected(client, program):
    res = client.post(
        f"/api/pharma/programs/{program['id']}/research",
        json={"claim": "trust me", "metric": "target_evidence_score", "value": 0.9},
        headers=headers("scientist"),
    )
    assert res.status_code == 422


def test_cited_human_evidence_unblocks_the_target_gate(client, program):
    for metric, value in (
        ("target_evidence_score", 0.85),
        ("druggability_score", 0.75),
        ("freedom_to_operate", 0.7),
        ("citation_count", 12),
    ):
        res = client.post(
            f"/api/pharma/programs/{program['id']}/research",
            json={
                "claim": f"{metric} supported by published data",
                "citation": "doi:10.0000/demo",
                "metric": metric,
                "value": value,
            },
            headers=headers("scientist"),
        )
        assert res.status_code == 200, res.text

    events = client.get(
        f"/api/pharma/programs/{program['id']}/research", headers=headers("viewer")
    ).json()
    human = [e for e in events if e["kind"] == "human_evidence"]
    assert len(human) == 4
    assert all(e["citation"] for e in human)

    res = client.post(
        f"/api/pharma/programs/{program['id']}/advance", headers=headers("scientist")
    )
    assert res.status_code == 200, res.text
    detail = get_program(client, program["id"])
    assert detail["latest_gate"]["decision"] == "go"
    assert detail["program"]["current_stage"] == STAGE_KEYS[1]


def test_evidence_is_carried_into_later_rounds(client, program):
    rounds = client.get(
        f"/api/pharma/programs/{program['id']}/rounds", headers=headers("viewer")
    ).json()
    latest = rounds[0]
    assert latest["findings"]["target_evidence_score"] == 0.85
    assert latest["metrics"]["freedom_to_operate"] == 0.7


def test_rounds_generate_falsifiable_experiments(client, program):
    """A blocked chemistry gate must produce assays; a blocked evidence gate must say so."""
    rounds = client.get(
        f"/api/pharma/programs/{program['id']}/rounds", headers=headers("viewer")
    ).json()
    plans = [r["experiment_plan"] for r in rounds]
    # The target-assessment gate is blocked on literature, which no assay can settle.
    assert any(p["evidence_tasks"] for p in plans)

    # Later stages are blocked on potency/selectivity/ADMET, which assays can settle.
    experiments: list[dict] = []
    for _ in range(4):
        client.post(
            f"/api/pharma/programs/{program['id']}/advance", headers=headers("scientist")
        )
        experiments = client.get(
            f"/api/pharma/programs/{program['id']}/experiments", headers=headers("viewer")
        ).json()
        if experiments:
            break
    assert experiments
    for exp in experiments:
        assert exp["assay"] and exp["endpoint"] and exp["falsification"]
        assert exp["cost_usd"] > 0
        assert exp["status"] == "proposed"
        assert exp["molecules"]
    assert any(e["blocking"] for e in experiments)


def test_assay_ingestion_overrides_prediction_and_scores_drift(client, program):
    mols = client.get(
        f"/api/pharma/programs/{program['id']}/molecules", headers=headers("viewer")
    ).json()
    lead = mols[0]
    predicted = lead["evaluation"]["binding"]["pkd"]
    res = client.post(
        f"/api/pharma/programs/{program['id']}/assays",
        json={
            "molecule_hash": lead["id"],
            "metric": "pkd",
            "value": round(predicted + 1.0, 2),
            "assay": "SPR dose-response",
            "source": "test lab",
        },
        headers=headers("scientist"),
    )
    assert res.status_code == 200, res.text

    drift = client.get(
        f"/api/pharma/programs/{program['id']}/drift", headers=headers("viewer")
    ).json()
    assert drift["n"] >= 1
    assert drift["rmse"] == pytest.approx(1.0, abs=0.05)
    assert drift["bias"] == pytest.approx(-1.0, abs=0.05)  # the proxy under-predicted potency


def test_unknown_molecule_hash_is_refused(client, program):
    res = client.post(
        f"/api/pharma/programs/{program['id']}/assays",
        json={"molecule_hash": "does-not-exist", "metric": "pkd", "value": 8.0},
        headers=headers("scientist"),
    )
    assert res.status_code == 404


def test_autonomy_level_blocks_advancement_and_requires_approval(client, program):
    res = client.post(
        f"/api/pharma/programs/{program['id']}/autonomy",
        json={"autonomy_level": 0},
        headers=headers("scientist"),
    )
    assert res.status_code == 200
    assert res.json()["autonomy_level"] == 0

    client.post(f"/api/pharma/programs/{program['id']}/advance", headers=headers("scientist"))
    detail = get_program(client, program["id"])
    gate = detail["latest_gate"]
    assert gate["requires_approval"]
    assert gate["approval_status"] == "pending"
    assert detail["program"]["status"] == "awaiting_approval"
    assert detail["next_action"]["action"] == "await_approval"

    # While a decision is pending, the program may not run itself further.
    blocked = client.post(
        f"/api/pharma/programs/{program['id']}/advance", headers=headers("scientist")
    )
    assert blocked.status_code == 409

    approved = client.post(
        f"/api/pharma/gates/{gate['id']}/approval",
        json={"approve": True, "note": "reviewed in test"},
        headers=headers("scientist"),
    )
    assert approved.status_code == 200, approved.text
    after = get_program(client, program["id"])
    assert after["program"]["status"] != "awaiting_approval"
    assert after["gate_history"][-1]["approval_status"] == "approved"

    client.post(
        f"/api/pharma/programs/{program['id']}/autonomy",
        json={"autonomy_level": 2},
        headers=headers("scientist"),
    )


def test_rejecting_a_gate_records_the_rejection(client, program):
    client.post(
        f"/api/pharma/programs/{program['id']}/autonomy",
        json={"autonomy_level": 0},
        headers=headers("scientist"),
    )
    client.post(f"/api/pharma/programs/{program['id']}/advance", headers=headers("scientist"))
    gate = get_program(client, program["id"])["latest_gate"]
    assert gate["approval_status"] == "pending"
    res = client.post(
        f"/api/pharma/gates/{gate['id']}/approval",
        json={"approve": False, "note": "not convinced by the evidence"},
        headers=headers("scientist"),
    )
    assert res.status_code == 200, res.text
    gates = client.get(
        f"/api/pharma/programs/{program['id']}/gates", headers=headers("viewer")
    ).json()
    rejected = next(g for g in gates if g["id"] == gate["id"])
    assert rejected["approval_status"] == "rejected"
    assert not rejected["applied"]
    client.post(
        f"/api/pharma/programs/{program['id']}/autonomy",
        json={"autonomy_level": 2},
        headers=headers("scientist"),
    )


def test_viewer_cannot_mutate_a_program(client, program):
    res = client.post(
        f"/api/pharma/programs/{program['id']}/advance", headers=headers("viewer")
    )
    assert res.status_code in (401, 403)


def test_dossier_and_portfolio_are_explicit_about_their_limits(client, program):
    dossier = client.get(
        f"/api/pharma/programs/{program['id']}/dossier", headers=headers("viewer")
    ).json()
    assert dossier["sections"]
    assert 0.0 <= dossier["completeness"] <= 1.0
    assert dossier["disclaimer"]

    portfolio = client.get("/api/pharma/portfolio", headers=headers("viewer")).json()[
        "portfolio"
    ]
    assert portfolio["programs"] >= 1
    assert portfolio["recommendation"]
    assert portfolio["rows"]


def test_molecule_export_is_csv_with_provenance(client, program):
    res = client.get(
        f"/api/pharma/programs/{program['id']}/export/molecules", headers=headers("viewer")
    )
    assert res.status_code == 200
    header = res.text.splitlines()[0]
    for column in ("smiles", "commit", "provider", "composite_score"):
        assert column in header


def test_daemon_reports_that_offline_mode_cannot_research(client, program):
    res = client.get(f"/api/pharma/programs/{program['id']}/daemon", headers=headers("viewer"))
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "local"  # no Devin credentials in tests
    assert body["devin_configured"] is False
    assert "literature" in body["note"].lower()


def test_memory_sync_without_devin_credentials_reports_why(client, program):
    res = client.post(
        f"/api/pharma/programs/{program['id']}/memory/sync", headers=headers("scientist")
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["synced"] is False
    assert body["error"]  # says why it stayed local-only rather than pretending it synced
    digest = client.get(
        f"/api/pharma/programs/{program['id']}/memory", headers=headers("viewer")
    ).json()
    assert digest["digest"]
