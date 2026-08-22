"""HTTP surface: auth, RBAC, quotas, uploads, cycles, version graph and exports."""

from __future__ import annotations

import io

from sqlalchemy import select

from app.db import SessionLocal
from app.models import DesignCycle, User
from tests.conftest import headers

FASTA = ">wt GB1\nMTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE\n"
PDB = """ATOM      1  CA  MET A   1       0.000   0.000   0.000  1.00 20.00           C
ATOM      2  CA  THR A   2       3.800   0.000   0.000  1.00 20.00           C
ATOM      3  CA  TYR A   3       7.600   0.000   0.000  1.00 20.00           C
END
"""


def _upload(client, project_id: str, name: str, body: str, role: str = "scientist"):
    return client.post(
        f"/api/projects/{project_id}/uploads",
        files={"file": (name, io.BytesIO(body.encode()), "text/plain")},
        headers=headers(role),
    )


def _new_project(client) -> str:
    resp = client.post(
        "/api/projects",
        json={"name": "api test", "goal": "raise stability"},
        headers=headers("scientist"),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ------------------------------------------------------------------ auth/RBAC


def test_auth_is_required_and_keys_are_validated(client):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/api/projects", headers=headers("viewer")).status_code == 200


def test_viewer_cannot_mutate_but_can_read(client):
    resp = client.post(
        "/api/projects", json={"name": "x", "goal": "y"}, headers=headers("viewer")
    )
    assert resp.status_code == 403
    assert "viewer" in resp.json()["detail"]
    assert client.get("/api/me", headers=headers("viewer")).json()["role"] == "viewer"


def test_provider_status_reports_simulation_when_devin_is_unconfigured(client):
    body = client.get("/api/providers", headers=headers("viewer")).json()
    assert body["devin_configured"] is False
    assert body["provider"] == "local-simulation"
    assert body["local_simulation_allowed"] is True


def test_healthz_and_openapi_are_available(client):
    assert client.get("/api/healthz").json()["status"] == "ok"
    schema = client.get("/openapi.json").json()
    assert "/api/projects/{project_id}/cycles" in schema["paths"]


# -------------------------------------------------------------------- uploads


def test_fasta_upload_creates_a_root_commit(client):
    project_id = _new_project(client)
    body = _upload(client, project_id, "wt.fasta", FASTA).json()
    assert body["artifact"]["kind"] == "fasta"
    assert body["commits"]
    commit_id = body["commits"][0]["id"]

    commit = client.get(f"/api/commits/{commit_id}", headers=headers("viewer")).json()
    assert commit["sequence"].startswith("MTYKLILNG")
    assert commit["provider"] == "upload"
    assert commit["parent_ids"] == []

    artifacts = client.get(
        f"/api/projects/{project_id}/artifacts", headers=headers("viewer")
    ).json()
    assert artifacts and artifacts[0]["sha256"]


def test_structure_upload_is_labelled_experimental_not_a_model(client):
    project_id = _new_project(client)
    body = _upload(client, project_id, "wt.pdb", PDB).json()
    assert body["artifact"]["kind"] == "pdb"
    assert body["structure"]["residues"] == 3
    commit_id = body["commits"][0]["id"]
    commit = client.get(f"/api/commits/{commit_id}", headers=headers("viewer")).json()
    assert commit["structure_source"] == "experimental:pdb"
    assert client.get(
        f"/api/commits/{commit_id}/structure", headers=headers("viewer")
    ).status_code == 200


def test_unknown_and_empty_uploads_are_rejected(client):
    project_id = _new_project(client)
    assert _upload(client, project_id, "notes.xyz", "just some prose").status_code == 400
    resp = client.post(
        f"/api/projects/{project_id}/uploads",
        files={"file": ("empty.fasta", io.BytesIO(b""), "text/plain")},
        headers=headers("scientist"),
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------- cycles


def test_cycle_requires_a_starting_sequence(client):
    project_id = _new_project(client)
    resp = client.post(
        f"/api/projects/{project_id}/cycles",
        json={"brief": "improve it"},
        headers=headers("scientist"),
    )
    assert resp.status_code == 400
    assert "upload" in resp.json()["detail"]


def test_full_happy_path_from_upload_to_wetlab_export(client):
    project_id = _new_project(client)
    _upload(client, project_id, "wt.fasta", FASTA)

    cycle = client.post(
        f"/api/projects/{project_id}/cycles",
        json={"brief": "raise stability without losing binding", "acu_limit": 10},
        headers=headers("scientist"),
    ).json()
    cycle = client.get(f"/api/cycles/{cycle['id']}", headers=headers("viewer")).json()
    assert cycle["status"] in {"committed", "partial"}, cycle
    assert cycle["provider"] == "local-simulation"

    agents = client.get(f"/api/cycles/{cycle['id']}/agents", headers=headers("viewer")).json()
    assert {a["role"] for a in agents} >= {"orchestrator"}
    assert all(a["provider"] == "local-simulation" for a in agents)

    shortlist = client.get(
        f"/api/cycles/{cycle['id']}/shortlist", headers=headers("viewer")
    ).json()
    assert shortlist["pack"]["shortlist"]

    csv_resp = client.get(
        f"/api/cycles/{cycle['id']}/export?fmt=csv", headers=headers("viewer")
    )
    assert csv_resp.status_code == 200
    assert "attachment" in csv_resp.headers["content-disposition"]
    assert len(csv_resp.text.splitlines()) > 1
    for fmt in ("json", "fasta"):
        assert client.get(
            f"/api/cycles/{cycle['id']}/export?fmt={fmt}", headers=headers("viewer")
        ).status_code == 200

    graph = client.get(f"/api/projects/{project_id}/graph", headers=headers("viewer")).json()
    assert len(graph["nodes"]) > 1
    assert graph["edges"]

    timeline = client.get(
        f"/api/projects/{project_id}/timeline", headers=headers("viewer")
    ).json()
    assert {o["kind"] for o in timeline} >= {"cycle_started", "cycle_finished"}

    memory = client.get(f"/api/projects/{project_id}/memory", headers=headers("viewer")).json()
    assert memory["digest"]["cycles_completed"] >= 1
    assert memory["prompt_view"]


def test_export_before_the_cycle_finishes_is_a_conflict(client):
    project_id = _new_project(client)
    _upload(client, project_id, "wt.fasta", FASTA)
    session = SessionLocal()
    try:
        cycle = DesignCycle(
            project_id=project_id, brief="queued", round=9, branch="main", status="queued"
        )
        session.add(cycle)
        session.commit()
        cycle_id = cycle.id
    finally:
        session.close()
    assert client.get(f"/api/cycles/{cycle_id}/shortlist", headers=headers("viewer")).status_code == 409
    assert client.get(f"/api/cycles/{cycle_id}/export", headers=headers("viewer")).status_code == 409


def test_acu_quota_blocks_a_cycle_that_cannot_be_paid_for(client):
    project_id = _new_project(client)
    _upload(client, project_id, "wt.fasta", FASTA)
    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.role == "scientist"))
        original = user.acu_quota
        user.acu_quota = 1
        session.commit()
    finally:
        session.close()
    try:
        resp = client.post(
            f"/api/projects/{project_id}/cycles",
            json={"brief": "too expensive", "acu_limit": 50},
            headers=headers("scientist"),
        )
        assert resp.status_code == 429
        assert "quota" in resp.json()["detail"]
    finally:
        session = SessionLocal()
        try:
            user = session.scalar(select(User).where(User.role == "scientist"))
            user.acu_quota = original
            session.commit()
        finally:
            session.close()


# ------------------------------------------------------------------- versions


def test_branch_merge_and_diff_over_http(client):
    project_id = _new_project(client)
    root = _upload(client, project_id, "wt.fasta", FASTA).json()["commits"][0]["id"]

    created = client.post(
        f"/api/projects/{project_id}/branches",
        json={"name": "alt", "from_commit": root},
        headers=headers("scientist"),
    )
    assert created.status_code == 200
    assert created.json()["head_commit_id"] == root
    dup = client.post(
        f"/api/projects/{project_id}/branches",
        json={"name": "alt", "from_commit": root},
        headers=headers("scientist"),
    )
    assert dup.status_code == 409

    diff = client.get(f"/api/diff?a={root}&b={root}", headers=headers("viewer")).json()
    assert diff["mutations"] == []
    assert client.get(f"/api/diff?a={root}&b=missing", headers=headers("viewer")).status_code == 404

    lineage = client.get(
        f"/api/commits/{root}/lineage?direction=ancestors", headers=headers("viewer")
    ).json()
    assert lineage["lineage"] == []


def test_demo_seed_endpoint_gives_a_three_click_starting_point(client, demo_project_id):
    project = client.get(f"/api/projects/{demo_project_id}", headers=headers("viewer")).json()
    assert project["is_demo"] is True
    assert project["head_commit_id"]
    assert project["target_sequence"]
