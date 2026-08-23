"""API tests for the seeded campaign the 90-second demo path opens on.

``seed-campaign`` and the paper cache endpoint are the two demo-path routes the rest of the API
tests never exercise, and they are the ones the UI depends on for a non-empty campaign: three
versions, a cached paper trail, one measured result per version and a shrinking drift table.
"""

from __future__ import annotations

from tests.conftest import headers


def test_seed_campaign_builds_a_measured_history_and_is_idempotent(client, project, root_commit):
    project_id = project.id

    res = client.post(
        f"/api/lab/projects/{project_id}/research/seed-campaign", headers=headers("scientist")
    )
    assert res.status_code == 200
    body = res.json()
    assert body["seeded"] is True
    # the wild type plus the two designed rounds, each with a simulated measurement behind it
    assert len(body["versions"]) == 3
    assert len(body["rounds"]) == 3
    assert body["papers"] > 0
    assert body["lab_bias"]["melting_temperature"] < 0, "the demo lab must start optimistic"
    assert body["daemon"]["campaign_id"]
    assert body["daemon"]["status"] != "error", body["daemon"]["detail"]

    commits = client.get(f"/api/projects/{project_id}/commits", headers=headers("viewer"))
    assert commits.status_code == 200
    assert len(commits.json()) == 3

    overview = client.get(f"/api/lab/projects/{project_id}/research", headers=headers("viewer"))
    assert overview.status_code == 200
    digest = overview.json()["digest"]
    assert digest["version_count"] >= 3
    assert digest["paper_count"] > 0
    assert digest["result_count"] >= 3
    assert overview.json()["learned"]["headline"]

    papers = client.get(
        f"/api/lab/projects/{project_id}/research/papers", headers=headers("viewer")
    )
    assert papers.status_code == 200
    rows = papers.json()
    assert rows, "the immutable paper cache must be readable after a campaign pass"
    assert all(row["paper_key"] and row["title"] and row["source"] for row in rows)

    drift = client.get(
        f"/api/lab/projects/{project_id}/research/drift", headers=headers("viewer")
    )
    assert drift.status_code == 200
    tm = next(d for d in drift.json() if d["metric"] == "melting_temperature")
    assert tm["n_observations"] >= 3
    # calibration removes the seeded bias, so the residual genuinely shrinks v1 -> latest
    assert abs(tm["history"][-1]["residual"]) < abs(tm["history"][0]["residual"])

    again = client.post(
        f"/api/lab/projects/{project_id}/research/seed-campaign", headers=headers("scientist")
    )
    assert again.status_code == 200
    assert again.json()["seeded"] is False
    assert client.get(
        f"/api/projects/{project_id}/commits", headers=headers("viewer")
    ).json().__len__() == 3


def test_seed_campaign_requires_a_scientist(client, demo_project_id):
    res = client.post(
        f"/api/lab/projects/{demo_project_id}/research/seed-campaign", headers=headers("viewer")
    )
    assert res.status_code == 403


def test_seed_campaign_on_an_empty_project_reports_why(client, project):
    res = client.post(
        f"/api/lab/projects/{project.id}/research/seed-campaign", headers=headers("scientist")
    )
    assert res.status_code == 200
    body = res.json()
    assert body["seeded"] is False
    assert body["reason"] == "project has no root version"
