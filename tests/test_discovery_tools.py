from types import SimpleNamespace
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from oida import discovery_tools
from oida.reasoning.contracts import ProviderResult


def client_with(result):
    calls = []

    def complete(req):
        calls.append(req)
        return result

    registry = SimpleNamespace(complete=complete)
    workspace = SimpleNamespace(
        settings=SimpleNamespace(load=lambda: {}),
        reasoning=SimpleNamespace(registry_factory=lambda settings: registry),
    )

    def admin(req):
        if req.headers.get("origin") == "https://outside.example":
            raise HTTPException(403)

    app = FastAPI()
    app.include_router(discovery_tools.discovery_tools_router(workspace, admin))
    return TestClient(app), calls


def test_planning_reuses_provider_without_creating_memory_or_sessions():
    result = ProviderResult(
        provider_id="ollama",
        model_id="test",
        status="ok",
        parsed={
            "queries": ["crickets", "crickets", "wetlands"],
            "reason": "Follow the retained sound",
        },
    )
    client, calls = client_with(result)
    response = client.post(
        "/discovery/plan",
        json={"provider_id": "ollama", "context": "Untrusted source context"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["queries"] == ["crickets", "wetlands"]
    assert calls[0].metadata["retention_owner"] == "listeningstackweb"
    assert "untrusted" in calls[0].system_prompt
    assert (
        client.post(
            "/discovery/plan", json={"provider_id": "local_structured", "context": "x"}
        ).status_code
        == 400
    )
    assert len(calls) == 1


def test_search_reuses_existing_retrievers_and_admin(monkeypatch):
    client, _ = client_with(None)
    monkeypatch.setattr(
        discovery_tools, "wiki_search", lambda query: [{"title": query, "kind": "wiki"}]
    )
    assert (
        client.post(
            "/discovery/search", json={"kind": "wiki", "query": "wetlands"}
        ).json()["sources"][0]["kind"]
        == "wiki"
    )
    assert (
        client.post(
            "/discovery/search",
            json={"kind": "wiki", "query": "wetlands"},
            headers={"origin": "https://outside.example"},
        ).status_code
        == 403
    )


def test_provider_error_does_not_become_a_model_plan():
    result = ProviderResult(
        provider_id="codex",
        model_id="test",
        status="error",
        error="disabled",
        parsed={"queries": ["should not run"], "reason": "x"},
    )
    client, _ = client_with(result)
    assert (
        client.post(
            "/discovery/plan", json={"provider_id": "codex", "context": "x"}
        ).status_code
        == 503
    )
