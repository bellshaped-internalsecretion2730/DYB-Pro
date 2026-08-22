"""API tests for the Research Module + wet-lab loop surface (no dead controls)."""

from __future__ import annotations

from tests.conftest import headers


def _first_commit(client, project_id: str) -> str:
    graph = client.get(f"/api/projects/{project_id}/graph", headers=headers("viewer"))
    assert graph.status_code == 200
    nodes = graph.json()["nodes"]
    assert nodes, "demo project must have versions"
    return nodes[0]["id"]


def test_campaign_overview_is_created_on_demand(client, demo_project_id):
    res = client.get(f"/api/lab/projects/{demo_project_id}/research", headers=headers("viewer"))
    assert res.status_code == 200
    body = res.json()
    assert body["campaign"]["project_id"] == demo_project_id
    assert body["daemon"]["status"] in {"idle", "queued", "working", "degraded"}
    assert "provider" in body["daemon"]


def test_metric_vocabulary_comes_from_the_skill(client):
    res = client.get("/api/lab/research/metrics", headers=headers("viewer"))
    assert res.status_code == 200
    names = {row["name"] for row in res.json()}
    assert {"melting_temperature", "expression_yield", "soluble_fraction"} <= names
    tm = next(r for r in res.json() if r["name"] == "melting_temperature")
    assert tm["unit"] == "C"
    assert tm["skill"] == "skill.wetlab_metrics"


def test_labels_are_validated_and_wake_the_daemon(client, demo_project_id):
    commit_id = _first_commit(client, demo_project_id)
    bad = client.post(
        f"/api/lab/commits/{commit_id}/labels",
        json={"kind": "active_site", "name": "site", "residues": [99999]},
        headers=headers("scientist"),
    )
    assert bad.status_code == 422

    ok = client.post(
        f"/api/lab/commits/{commit_id}/labels",
        json={"kind": "liability", "name": "exposed cys", "residues": [3, 4],
              "note": "watch for scrambling"},
        headers=headers("scientist"),
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["label"]["residues"] == [3, 4]
    assert body["daemon_task"]["status"] == "queued"

    listing = client.get(f"/api/lab/commits/{commit_id}/labels", headers=headers("viewer"))
    assert listing.status_code == 200
    assert any(lb["name"] == "exposed cys" for lb in listing.json()["labels"])
    assert listing.json()["tree"]


def test_viewer_cannot_write_labels(client, demo_project_id):
    commit_id = _first_commit(client, demo_project_id)
    res = client.post(
        f"/api/lab/commits/{commit_id}/labels",
        json={"kind": "note", "name": "n", "residues": [1]},
        headers=headers("viewer"),
    )
    assert res.status_code == 403


def test_wetlab_loop_end_to_end(client, demo_project_id):
    commit_id = _first_commit(client, demo_project_id)

    risk = client.get(f"/api/lab/commits/{commit_id}/wetlab/risk", headers=headers("viewer"))
    assert risk.status_code == 200
    assert risk.json()["predictions"]
    assert risk.json()["risk"]["risks"]

    plan = client.post(
        f"/api/lab/commits/{commit_id}/wetlab/plan",
        json={"host": "E. coli BL21(DE3)", "max_assays": 3},
        headers=headers("scientist"),
    )
    assert plan.status_code == 201
    plan_body = plan.json()
    assert plan_body["constructs"] and plan_body["assays"]
    assert plan_body["total_cost_usd"] > 0
    assert plan_body["information_per_usd"] >= 0

    latest = client.get(f"/api/lab/commits/{commit_id}/wetlab/plan", headers=headers("viewer"))
    assert latest.status_code == 200
    assert latest.json()["id"] == plan_body["id"]

    sim = client.post(
        f"/api/lab/wetlab/plans/{plan_body['id']}/simulate",
        json={"seed": 7},
        headers=headers("scientist"),
    )
    assert sim.status_code == 201
    result = sim.json()["result"]
    # a simulated measurement must never be presented as a real one
    assert result["source"] == "simulator"
    assert result["error_model"]["description"]
    assert result["measurements"]
    assert result["residuals"]

    results = client.get(f"/api/lab/commits/{commit_id}/wetlab/results", headers=headers("viewer"))
    assert results.status_code == 200
    assert any(r["id"] == result["id"] for r in results.json())


def test_pasted_csv_results_are_normalised(client, demo_project_id):
    commit_id = _first_commit(client, demo_project_id)
    res = client.post(
        f"/api/lab/commits/{commit_id}/wetlab/results",
        json={
            "text": "metric,value,unit\nTm,54.5,C\nyield,12,mg/L\nwibble,3,units\n",
            "source": "csv",
            "operator": "bench scientist",
        },
        headers=headers("scientist"),
    )
    assert res.status_code == 201
    body = res.json()
    metrics = {m["metric"] for m in body["result"]["measurements"]}
    assert "melting_temperature" in metrics
    assert "expression_yield" in metrics
    # unknown metrics are surfaced, not silently accepted as science
    assert "wibble" in " ".join(str(u) for u in body["unknown_metrics"])

    empty = client.post(
        f"/api/lab/commits/{commit_id}/wetlab/results",
        json={"text": "   "},
        headers=headers("scientist"),
    )
    assert empty.status_code == 422


def test_daemon_refresh_tick_and_research_trail(client, demo_project_id):
    commit_id = _first_commit(client, demo_project_id)
    refresh = client.post(
        f"/api/lab/projects/{demo_project_id}/research/refresh?commit_id={commit_id}",
        headers=headers("scientist"),
    )
    assert refresh.status_code == 200
    assert refresh.json()["status"] in {"queued", "coalesced"}

    tick = client.post(
        f"/api/lab/projects/{demo_project_id}/research/tick?limit=2",
        headers=headers("scientist"),
    )
    assert tick.status_code == 200
    assert tick.json()["processed"] >= 1

    events = client.get(
        f"/api/lab/projects/{demo_project_id}/research/events", headers=headers("viewer")
    )
    assert events.status_code == 200
    assert events.json(), "a processed task must leave research events behind"

    daemon = client.get(
        f"/api/lab/projects/{demo_project_id}/research/daemon", headers=headers("viewer")
    )
    assert daemon.status_code == 200
    assert daemon.json()["heartbeat_at"]


def test_drift_learned_and_proposal(client, demo_project_id):
    drift = client.get(f"/api/lab/projects/{demo_project_id}/research/drift", headers=headers("viewer"))
    assert drift.status_code == 200

    learned = client.get(
        f"/api/lab/projects/{demo_project_id}/research/learned", headers=headers("viewer")
    )
    assert learned.status_code == 200
    assert "headline" in learned.json()

    proposal = client.get(
        f"/api/lab/projects/{demo_project_id}/research/proposal", headers=headers("viewer")
    )
    assert proposal.status_code == 200
    body = proposal.json()
    assert body["proposed"]["mutations"]
    assert body["proposed"]["predicted_wetlab"]
    assert body["target_metric"]
    assert body["ranking"]


def test_assay_catalog_is_available(client):
    res = client.get("/api/lab/wetlab/assays", headers=headers("viewer"))
    assert res.status_code == 200
    assert any(row["assay"] == "expression_screen" for row in res.json())


def test_unknown_ids_are_404(client):
    assert client.get("/api/lab/commits/nope/labels", headers=headers("viewer")).status_code == 404
    assert client.get(
        "/api/lab/projects/nope/research", headers=headers("viewer")
    ).status_code == 404
    assert client.post(
        "/api/lab/wetlab/plans/nope/simulate", json={}, headers=headers("scientist")
    ).status_code == 404
