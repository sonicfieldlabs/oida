"""New owner routes and MCP commands use isolated synthetic text execution."""

import asyncio
import base64
import os
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from akousma.material_terms import MATERIAL_ACTIONS
from oida.reasoning.contracts import ModelDescriptor, ProviderDescriptor, ProviderResult
from oida.reasoning.registry import ProviderRegistry
from oida.representation_api import build_router


class Provider:
    provider_id = "fixture-text"

    def probe(self):
        return ProviderDescriptor(
            id=self.provider_id,
            name="Synthetic fixture",
            kind="openai_compatible",
            locality="local",
            enabled=True,
            available=True,
            authenticated=True,
            capabilities=["text"],
        )

    def list_models(self):
        return [
            ModelDescriptor(
                id="fixture-model",
                provider_id=self.provider_id,
                name="Synthetic fixture",
                locality="local",
                capabilities=["text"],
            )
        ]

    def complete(self, request):
        return ProviderResult(
            provider_id=self.provider_id,
            model_id=request.model_id,
            status="ok",
            parsed=dict(status="completed", text="hum\n\nhum\n", claims=[], reason=""),
            raw_metadata=dict(classification="synthetic_fixture"),
        )


@pytest.fixture
def owner(tmp_path):
    registry = ProviderRegistry()
    registry.register(Provider())

    def admin(request):
        if request.headers.get("X-Fixture-Owner") != "authenticated":
            raise HTTPException(403, "fixture owner boundary")

    app = FastAPI()
    app.include_router(
        build_router(
            data_dir=tmp_path / "data",
            memory_root=lambda: tmp_path / "memory",
            workspace_ref="fixture:workspace",
            actor_ref="fixture:owner",
            registry_factory=lambda: registry,
            require_admin=admin,
        )
    )
    with TestClient(app) as client:
        yield client, {"X-Fixture-Owner": "authenticated"}, tmp_path


def body(**extra):
    return dict(
        source=dict(
            material_base64=base64.b64encode(b"hum\r\n  hum\r\n").decode(),
            terms_ref="fixture:terms",
        ),
        provider_id="fixture-text",
        model_id="fixture-model",
        intent="sound",
        **extra,
    )


def grant(client, headers):
    response = client.put(
        "/representations/terms/fixture:terms",
        headers=headers,
        json=dict(
            actions=sorted(MATERIAL_ACTIONS - {"export_public", "disclose_external"}),
            basis="Synthetic owned material",
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_read_routes_require_owner_and_capabilities_do_not_open_state(owner):
    client, headers, root = owner
    for route in (
        "/representations/capabilities",
        "/representations/providers",
        "/listening/profiles",
        "/listening/profiles/presets",
        "/representations/records",
    ):
        assert client.get(route).status_code == 403
    assert not (root / "data").exists() and not (root / "memory").exists()
    assert (
        client.get("/representations/capabilities", headers=headers).json()[
            "audio_required"
        ]
        is False
    )
    options = client.get("/representations/providers", headers=headers)
    assert options.status_code == 200
    assert options.json()["providers"][0]["id"] == "fixture-text"
    assert "detail" not in options.json()["providers"][0]
    assert not (root / "data").exists()


def test_owner_http_path_retains_retrieves_and_reuses_completed_text(owner):
    client, headers, _ = owner
    assert (
        client.post(
            "/representations/execute", headers=headers, json=body()
        ).status_code
        == 403
    )
    grant(client, headers)
    first = client.post(
        "/representations/execute", headers=headers, json=body(remember=True)
    )
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["execution"]["qualification"] == "synthetic_fixture"
    rid = result["record"]["record_ref"]
    detail = client.get("/representations/records/" + rid, headers=headers)
    assert detail.status_code == 200 and any(
        a["text"] == "hum\n\nhum\n" for a in detail.json()["artifacts"]
    )
    ref = result["record"]["outputs"][0]["representation_ref"]
    request = dict(
        source_ref=ref,
        intent="sound",
        provider_id="fixture-text",
        model_id="fixture-model",
        remember=True,
    )
    second = client.post("/representations/execute", headers=headers, json=request)
    assert second.status_code == 200, second.text
    detail = client.get(
        "/representations/records/" + second.json()["record"]["record_ref"],
        headers=headers,
    ).json()
    assert detail["memory_parents"] == [rid]
    receipt = client.get(
        "/representations/executions/" + result["record"]["operation_ref"],
        headers=headers,
    ).json()
    assert receipt["request_sha256"] and "report" not in receipt
    terms = client.get("/representations/terms/fixture:terms", headers=headers).json()
    client.put(
        "/representations/terms/fixture:terms",
        headers=headers,
        json=dict(
            actions=[],
            basis="Revoke metadata",
            expected_revision=terms["revision"],
            active=False,
        ),
    )
    assert (
        client.get(
            "/representations/executions/" + result["record"]["operation_ref"],
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.get("/representations/records/missing", headers=headers).status_code
        == 404
    )


def test_authoring_and_import_do_not_adopt_and_conflicts_do_not_overwrite(owner):
    client, headers, _ = owner
    draft = dict(document_id="fixture:doc", text="# Listening\n", expected_sha256=None)
    saved = client.put("/listening/profiles/drafts", headers=headers, json=draft).json()
    frozen = client.post(
        "/listening/profiles/revisions",
        headers=headers,
        json=dict(
            document_id="fixture:doc",
            expected_sha256=saved["sha256"],
            authors=["fixture:owner"],
            terms_ref="fixture:terms",
        ),
    )
    assert frozen.status_code == 200 and frozen.json()["adoption"] == "not_adopted"
    assert (
        client.put(
            "/listening/profiles/drafts", headers=headers, json=draft
        ).status_code
        == 422
    )
    imported = client.post("/listening/profiles/presets/minimal", headers=headers)
    assert imported.status_code == 200 and imported.json()["adoption"] == "not_adopted"
    assert client.get("/listening/profiles", headers=headers).json()["adoptions"] == []
    authority = client.post(
        "/listening/profiles/authorities",
        headers=headers,
        json=dict(
            scope_path=[dict(kind="workspace", ref="fixture:workspace")],
            party_refs=["fixture:owner"],
            actions=["adopt", "apply", "revoke"],
            basis="Synthetic owner scope",
        ),
    ).json()
    adoption = client.post(
        "/listening/profiles/adoptions",
        headers=headers,
        json=dict(
            revision_ref=imported.json()["document"]["revision_ref"],
            scope_path=[dict(kind="workspace", ref="fixture:workspace")],
            party_refs=["fixture:owner"],
            authority_ref=authority["authority_ref"],
            expected_generation=0,
        ),
    )
    assert adoption.status_code == 200
    assert (
        client.delete(
            "/listening/profiles/authorities/" + authority["authority_ref"],
            headers=headers,
        ).json()["active"]
        is False
    )


@pytest.mark.parametrize(
    "mutation",
    [
        dict(source_ref="duplicate"),
        dict(source=dict(material_base64="not base64", terms_ref="fixture:terms")),
        dict(scope_path=[dict(kind="workspace", ref="foreign")]),
        dict(unknown="not permitted"),
    ],
)
def test_bad_or_foreign_requests_are_explicitly_rejected(owner, mutation):
    client, headers, _ = owner
    grant(client, headers)
    request = body()
    request.update(mutation)
    assert client.post(
        "/representations/execute", headers=headers, json=request
    ).status_code in {403, 422}


def test_new_routes_keep_real_local_admin_and_workspace_boundary(tmp_path):
    code = """
from fastapi.testclient import TestClient
from oida.server import create_app
app = create_app(profile='stub')
with TestClient(app, base_url='http://127.0.0.1:8765', client=('127.0.0.1',50000)) as client:
    identity = client.get('/owner/identity').json()
    assert client.get('/representations/capabilities').status_code == 200
    assert client.put('/representations/terms/fixture', json={'actions':[], 'basis':'fixture'}).status_code == 409
    headers = {'X-Centaur-Workspace':identity['workspace_id'], 'X-Centaur-Generation':identity['generation'], 'X-Centaur-Binding':identity['binding']}
    assert client.put('/representations/terms/fixture', json={'actions':[], 'basis':'fixture'}, headers=headers).status_code == 200
    assert client.get('/listening/profiles').json()['adoptions'] == []
remote = TestClient(app, base_url='http://100.64.0.20:8765', client=('100.64.0.21',50000))
assert remote.get('/representations/capabilities').status_code == 403
"""
    env = dict(os.environ)
    env.update(
        LISTENINGSTACK_WORKSPACE_ID="ws_0123456789abcdef01234567",
        LISTENINGSTACK_WORKSPACE_GENERATION="phase5-fixture",
        OIDA_DATA_DIR=str(tmp_path / "data"),
        OIDA_AUDIO_DIR=str(tmp_path / "audio"),
        OIDA_TRIAL_DIR=str(tmp_path / "trial"),
        AKOUSMATA_PATH=str(tmp_path / "memory"),
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_mcp_text_tool_is_explicit_and_does_not_adopt_or_use_audio(monkeypatch):
    from oida import mcp_server

    calls = []

    def post(_server, route, payload):
        calls.append((route, payload))
        return dict(fixture=True)

    monkeypatch.setattr(mcp_server, "post_json", post)
    asyncio.run(
        mcp_server.oida_inscription(
            provider_id="fixture-text",
            model_id="fixture-model",
            intent="sound",
            text="hum\n",
            terms_ref="fixture:terms",
        )
    )
    route, payload = calls[0]
    assert route == "/representations/execute" and payload["remember"] is False
    assert base64.b64decode(payload["source"]["material_base64"]) == b"hum\n"
    with pytest.raises(ValueError):
        asyncio.run(
            mcp_server.oida_inscription(
                provider_id="fixture-text",
                model_id="fixture-model",
                intent="sound",
                text="ungranted",
            )
        )


def test_mcp_continuation_and_filters_keep_exact_query_values(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from oida import mcp_server
    calls = []
    def get(_server, path):
        calls.append(path)
        return dict(records=[], next_cursor="B" * 43)
    monkeypatch.setattr(mcp_server, "get_json", get)
    result = asyncio.run(mcp_server.oida_representation_memory(limit=200, cursor="A" * 43,
                         query="▧ & metadata", form="text", output_kind="work"))
    assert result["next_cursor"] == "B" * 43
    assert parse_qs(urlsplit(calls[0]).query) == dict(limit=["200"], cursor=["A" * 43],
                 query=["▧ & metadata"], form=["text"], output_kind=["work"])


def test_stale_profile_generation_refuses_provider_and_has_a_permission_checked_receipt(owner):
    client, headers, _ = owner
    grant(client, headers)
    request = body() | dict(expected_profile_generation=99, operation_ref="fixture_stale_generation")
    rejected = client.post("/representations/execute", headers=headers, json=request)
    assert rejected.status_code == 422
    receipt = client.get("/representations/executions/fixture_stale_generation", headers=headers)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "failed" and receipt.json()["provider_called"] is False
