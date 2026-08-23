"""HTTP surface: auth, RBAC, quotas, uploads, cycles, version graph and exports."""

from __future__ import annotations

import io

from sqlalchemy import select

from app.db import SessionLocal
from app.models import DesignCycle, Project, User
from app.security import hash_api_key
from tests.conftest import headers

FASTA = ">wt GB1\nMTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE\n"
PDB = """ATOM      1  CA  MET A   1       0.000   0.000   0.000  1.00 20.00           C
ATOM      2  CA  THR A   2       3.800   0.000   0.000  1.00 20.00           C
ATOM      3  CA  TYR A   3       7.600   0.000   0.000  1.00 20.00           C
END
"""
LIGAND_PDB = """HETATM    1  C1  LIG Z   1       1.000   2.000   3.000  1.00 20.00           C
HETATM    2  O1  LIG Z   1       2.100   2.000   3.000  1.00 20.00           O
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

    structure = client.get(
        f"/api/commits/{commit_id}/structure", headers=headers("viewer")
    )
    assert structure.status_code == 200
    assert structure.headers["content-type"].startswith("chemical/x-pdb")
    assert structure.headers["x-dyb-pro-structure-source"] == (
        "model:coarse-geometric:on-demand"
    )
    assert structure.headers["x-dyb-pro-structure-ephemeral"] == "true"
    assert structure.text.startswith("REMARK  DYB Pro CA trace (model:coarse-geometric)")
    assert sum(line.startswith("ATOM") for line in structure.text.splitlines()) == 56


def test_upload_does_not_claim_experimental_provenance_from_the_extension(client):
    project_id = _new_project(client)
    body = _upload(client, project_id, "wt.pdb", PDB).json()
    assert body["artifact"]["kind"] == "pdb"
    assert body["structure"]["residues"] == 3
    commit_id = body["commits"][0]["id"]
    commit = client.get(f"/api/commits/{commit_id}", headers=headers("viewer")).json()
    assert commit["structure_source"] == "uploaded:pdb (provenance undeclared)"
    structure = client.get(
        f"/api/commits/{commit_id}/structure", headers=headers("viewer")
    )
    assert structure.status_code == 200
    assert structure.text == PDB
    assert structure.headers["x-dyb-pro-structure-source"] == (
        "uploaded:pdb (provenance undeclared)"
    )
    assert "x-dyb-pro-structure-ephemeral" not in structure.headers


def test_declared_experimental_method_is_believed(client):
    project_id = _new_project(client)
    body = _upload(
        client, project_id, "xray.pdb", "EXPDTA    X-RAY DIFFRACTION\n" + PDB
    ).json()
    commit = client.get(
        f"/api/commits/{body['commits'][0]['id']}", headers=headers("viewer")
    ).json()
    assert commit["structure_source"] == "experimental:x-ray diffraction"


def test_predicted_model_upload_is_labelled_as_a_model(client):
    project_id = _new_project(client)
    body = _upload(
        client, project_id, "af.pdb", "REMARK   1 ALPHAFOLD pLDDT in B-factor\n" + PDB
    ).json()
    commit = client.get(
        f"/api/commits/{body['commits'][0]['id']}", headers=headers("viewer")
    ).json()
    assert commit["structure_source"].startswith("model:")


def test_unknown_and_empty_uploads_are_rejected(client):
    project_id = _new_project(client)
    assert _upload(client, project_id, "notes.xyz", "just some prose").status_code == 400
    resp = client.post(
        f"/api/projects/{project_id}/uploads",
        files={"file": ("empty.fasta", io.BytesIO(b""), "text/plain")},
        headers=headers("scientist"),
    )
    assert resp.status_code == 400


def test_target_and_ligand_pdb_inputs_are_kept_as_distinct_artifacts(client):
    project_id = _new_project(client)
    target = client.post(
        f"/api/projects/{project_id}/binding-inputs/target",
        files={"file": ("target.pdb", io.BytesIO(PDB.encode()), "chemical/x-pdb")},
        headers=headers("scientist"),
    )
    ligand = client.post(
        f"/api/projects/{project_id}/binding-inputs/ligand",
        files={"file": ("ligand.pdb", io.BytesIO(LIGAND_PDB.encode()), "chemical/x-pdb")},
        headers=headers("scientist"),
    )
    assert target.status_code == 200, target.text
    assert ligand.status_code == 200, ligand.text
    assert target.json()["kind"] == "target_pdb"
    assert target.json()["atom_count"] == 3
    assert ligand.json()["kind"] == "ligand_pdb"
    assert ligand.json()["atom_count"] == 2

    listed = client.get(
        f"/api/projects/{project_id}/binding-inputs", headers=headers("viewer")
    ).json()
    assert {row["role"] for row in listed} == {"target", "ligand"}
    assert client.get(
        f"/api/artifacts/{ligand.json()['id']}/content", headers=headers("viewer")
    ).text == LIGAND_PDB


def test_binding_input_validation_rejects_wrong_role_and_non_coordinates(client):
    project_id = _new_project(client)
    bad_role = client.post(
        f"/api/projects/{project_id}/binding-inputs/receptor",
        files={"file": ("x.pdb", io.BytesIO(PDB.encode()), "chemical/x-pdb")},
        headers=headers("scientist"),
    )
    prose = client.post(
        f"/api/projects/{project_id}/binding-inputs/ligand",
        files={"file": ("x.pdb", io.BytesIO(b"not coordinates"), "chemical/x-pdb")},
        headers=headers("scientist"),
    )
    assert bad_role.status_code == 400
    assert prose.status_code == 400


def test_workspace_chat_has_selected_context_and_executes_explicit_ui_commands(client):
    project_id = _new_project(client)
    commit_id = _upload(client, project_id, "wt.fasta", FASTA).json()["commits"][0]["id"]
    response = client.post(
        f"/api/projects/{project_id}/assistant/chat",
        json={
            "selected_commit_id": commit_id,
            "messages": [{"role": "user", "content": "show lineage"}],
            "actions_enabled": True,
        },
        headers=headers("scientist"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "deterministic"
    assert body["actions"] == [
        {"type": "open_tab", "value": "lineage", "reason": "Open lineage."}
    ]
    models = client.get("/api/assistant/models", headers=headers("viewer")).json()
    assert models["default"] == "gpt-5.6-terra"
    assert models["models"][0] == "gpt-5.6-terra"


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


# -------------------------------------------------------------------- tenancy


def test_another_scientists_project_is_readable_but_not_writable(client):
    """Viewers read the whole workspace; only the owner (or an admin) may mutate a lineage."""
    project_id = _new_project(client)
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        other = User(
            email="other-scientist@example.com",
            role="scientist",
            api_key_hash=hash_api_key("test-other"),
        )
        session.add(other)
        session.flush()
        project.owner_id = other.id
        session.commit()
    finally:
        session.close()

    assert client.get(
        f"/api/projects/{project_id}", headers=headers("viewer")
    ).status_code == 200
    assert _upload(client, project_id, "wt.fasta", FASTA).status_code == 403
    assert client.post(
        f"/api/projects/{project_id}/cycles",
        json={"brief": "hijack"},
        headers=headers("scientist"),
    ).status_code == 403
    assert client.post(
        f"/api/projects/{project_id}/branches",
        json={"name": "nope", "from_commit": "x"},
        headers=headers("scientist"),
    ).status_code == 403
    assert _upload(client, project_id, "wt.fasta", FASTA, role="admin").status_code == 200


# ---------------------------------------------------------- measured results


def test_measured_results_feed_calibration_without_touching_the_commit(client):
    project_id = _new_project(client)
    commit_id = _upload(client, project_id, "wt.fasta", FASTA).json()["commits"][0]["id"]
    before = client.get(f"/api/commits/{commit_id}", headers=headers("viewer")).json()

    created = client.post(
        f"/api/projects/{project_id}/results",
        json={
            "commit_id": commit_id,
            "assay": "SPR",
            "objective": "binding_score",
            "value": 12.5,
            "unit": "nM",
            "higher_is_better": False,
            "outcome": "hit",
        },
        headers=headers("scientist"),
    )
    assert created.status_code == 200, created.text
    assert created.json()["origin"] == "measured"

    after = client.get(f"/api/commits/{commit_id}", headers=headers("viewer")).json()
    assert after["scores"] == before["scores"]

    listed = client.get(
        f"/api/projects/{project_id}/results", headers=headers("viewer")
    ).json()
    assert len(listed) == 1

    drift = client.get(
        f"/api/projects/{project_id}/calibration", headers=headers("viewer")
    ).json()
    assert drift["measurements"] == 1
    assert drift["hit_rate"] == 1.0
    # One measurement cannot establish rank agreement between a proxy and reality.
    assert all(o["kendall_tau"] is None for o in drift["objectives"].values())

    timeline = client.get(
        f"/api/projects/{project_id}/timeline", headers=headers("viewer")
    ).json()
    assert "measured_result" in {o["kind"] for o in timeline}


def test_results_csv_upload_is_ingested_as_measurements(client):
    project_id = _new_project(client)
    commit_id = _upload(client, project_id, "wt.fasta", FASTA).json()["commits"][0]["id"]
    csv_body = (
        "commit,assay,objective,value,unit,outcome\n"
        f"{commit_id},SPR,binding_score,4.2,nM,hit\n"
        "unknown-commit,SPR,binding_score,9.9,nM,miss\n"
    )
    body = _upload(client, project_id, "results.csv", csv_body).json()
    assert body["assays"] == 2
    assert body["measured_results_ingested"] == 1


def test_demo_seed_endpoint_gives_a_three_click_starting_point(client, demo_project_id):
    project = client.get(f"/api/projects/{demo_project_id}", headers=headers("viewer")).json()
    assert project["is_demo"] is True
    assert project["head_commit_id"]
    assert project["target_sequence"]
