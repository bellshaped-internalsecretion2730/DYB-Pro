"""Devin client + provider resolution. The real API is never called: httpx is mocked."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.devin.client import DevinAPIError, DevinClient, DevinNotConfigured
from app.devin.runner import PROVIDER_DEVIN, PROVIDER_SIM, ProviderUnavailable, resolve_provider


@pytest.fixture(autouse=True)
def _forget_resolved_flavor():
    """The v3->v1 demotion is cached per key, so it must not leak between tests."""
    from app.devin import client as client_mod

    client_mod._RESOLVED_FLAVOR.clear()
    yield
    client_mod._RESOLVED_FLAVOR.clear()


def _settings(**over) -> Settings:
    base = {
        "devin_api_key": "cog_test_key",
        "devin_org_id": "org-123",
        "devin_api_flavor": "v3",
        "allow_local_simulation": False,
    }
    base.update(over)
    return Settings(**base)


def _client(handler, **over) -> DevinClient:
    settings = _settings(**over)
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url=settings.devin_api_base)
    return DevinClient(settings, client=http)


def test_v3_session_creation_sends_playbook_tags_acu_limit_and_schema():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "session_id": "devin-abc",
                "url": "https://app.devin.ai/sessions/abc",
                "status": "running",
            },
        )

    client = _client(handler)
    state = client.create_session(
        "design something",
        title="DYB Pro orchestrator",
        playbook_id="playbook-1",
        child_playbook_id="playbook-2",
        tags=["dyb-pro", "role:orchestrator"],
        max_acu_limit=8,
        structured_output_schema={"type": "object"},
        parent_session_id="devin-parent",
    )
    assert "/v3/organizations/org-123/sessions" in seen["url"]
    assert "devin_id=devin-parent" in seen["url"]
    body = seen["json"]
    assert body["playbook_id"] == "playbook-1"
    assert body["child_playbook_id"] == "playbook-2"
    assert body["max_acu_limit"] == 8
    assert body["tags"] == ["dyb-pro", "role:orchestrator"]
    assert body["structured_output_required"] is True
    assert state.session_id == "devin-abc"
    assert state.status == "running"
    assert not state.is_terminal


def test_a_personal_key_falls_back_from_the_org_api_to_v1():
    """A key without org scope gets 403 on /v3; the client retries the same call on /v1."""
    from app.devin import client as client_mod

    client_mod._RESOLVED_FLAVOR.clear()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.startswith("/v3/"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, json={"session_id": "devin-abc", "status": "running"})

    client = _client(handler, devin_api_key="cog_personal_key")
    try:
        state = client.create_session("design something", title="orchestrator")
        assert state.session_id == "devin-abc"
        assert calls == ["/v3/organizations/org-123/sessions", "/v1/sessions"]

        # The demotion is remembered, so later calls go straight to v1.
        client.get_session("devin-abc")
        assert calls[-1] == "/v1/sessions/devin-abc"

        # Playbook management has no v1 equivalent: it degrades instead of pretending.
        assert client.list_playbooks() == []
        with pytest.raises(DevinNotConfigured):
            client.create_playbook("title", "body", None)
    finally:
        client_mod._RESOLVED_FLAVOR.clear()


def test_session_state_terminality_and_structured_output():
    payload = {
        "session_id": "devin-abc",
        "status": "running",
        "status_detail": "finished",
        "acus_consumed": 3.5,
        "structured_output": {"candidates": [{"label": "d1", "mutations": ["T2K"]}]},
        "tags": ["dyb-pro"],
    }
    client = _client(lambda request: httpx.Response(200, json=payload))
    state = client.get_session("devin-abc")
    assert state.is_terminal and not state.failed
    assert state.acus_consumed == 3.5
    assert state.structured_output["candidates"][0]["label"] == "d1"

    failed = _client(
        lambda request: httpx.Response(200, json={"session_id": "devin-x", "status": "expired"})
    ).get_session("devin-x")
    assert failed.is_terminal and failed.failed


def test_cancel_messages_then_terminates_the_session():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    _client(handler).cancel_session("devin-abc", "user cancelled")
    assert calls == [
        "/v3/organizations/org-123/sessions/devin-abc/messages",
        "/v3/organizations/org-123/sessions/devin-abc",
    ]


def test_api_errors_are_raised_with_status_and_path():
    client = _client(lambda request: httpx.Response(403, text="forbidden"))
    with pytest.raises(DevinAPIError) as excinfo:
        client.get_session("devin-abc")
    assert excinfo.value.status_code == 403
    assert "sessions/devin-abc" in excinfo.value.path


def test_client_refuses_to_start_without_credentials():
    with pytest.raises(DevinNotConfigured):
        DevinClient(Settings(devin_api_key=None, devin_org_id=None))


def test_provider_resolution_never_pretends_to_be_devin():
    assert resolve_provider(_settings()) == PROVIDER_DEVIN
    sim = Settings(devin_api_key=None, devin_org_id=None, allow_local_simulation=True)
    assert resolve_provider(sim) == PROVIDER_SIM
    strict = Settings(devin_api_key=None, devin_org_id=None, allow_local_simulation=False)
    with pytest.raises(ProviderUnavailable):
        resolve_provider(strict)

def test_health_reports_the_flavor_actually_in_use():
    """A personal key is demoted by the health probe, so the badge must read v1, not v3."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v3/"):
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, json={"sessions": []})

    client = _client(handler, devin_api_key="cog_personal_key")
    assert client.health() == {"ok": True, "flavor": "v1", "org_id": "org-123"}
    assert client.api_flavor() == "v1"
