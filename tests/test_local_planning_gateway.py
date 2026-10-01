import json
import sys
from fastapi.testclient import TestClient
from oida.reasoning.local import gateway


def setup(tmp_path, monkeypatch):
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json,sys\nfrom pathlib import Path\nPath(sys.argv[2]).write_text(json.dumps(dict(content='{}',usage={})))\n"
    )
    monkeypatch.setattr(gateway, "__file__", str(tmp_path / "gateway.py"))
    token = tmp_path / "token"
    token.write_text("x" * 40)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            dict(
                token_file=str(token),
                python=sys.executable,
                recommended_model="test",
                models=[
                    dict(
                        id="test",
                        path=str(tmp_path),
                        revision="a" * 40,
                        status="admitted",
                        evaluation_sha256="b" * 64,
                        files={str(worker): gateway.sha(worker)},
                    )
                ],
            )
        )
    )
    return TestClient(gateway.create_app(config)), worker


def test_gateway_auth_limits_and_unknown_model(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch)
    assert client.get("/v1/models").status_code == 401
    headers = {"Authorization": "Bearer " + "x" * 40}
    assert client.get("/v1/models", headers=headers).status_code == 200
    assert (
        client.get(
            "/v1/models", headers={**headers, "Origin": "https://foreign.test"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/chat/completions", headers=headers, json={"model": "unknown"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/chat/completions", headers=headers, content=b"x" * (192 * 1024 + 1)
        ).status_code
        == 413
    )


def test_gateway_artifact_changes_block_inference(tmp_path, monkeypatch):
    client, worker = setup(tmp_path, monkeypatch)
    worker.write_text("changed")
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + "x" * 40},
        json=dict(
            model="test",
            messages=[
                dict(role="system", content="Bounded"),
                dict(role="user", content="Data"),
            ],
            response_format={"json_schema": {"schema": {"type": "object", "$defs": {"Move": {}}}}},
            max_tokens=100,
        ),
    )
    assert response.status_code == 503 and "artifacts changed" in response.text


def test_gateway_serves_only_admitted_task_families(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch)
    request = dict(
        model="test",
        messages=[dict(role="system", content="Bounded"), dict(role="user", content="{}")],
        max_tokens=100,
    )
    headers = {"Authorization": "Bearer " + "x" * 40}
    routing = {"type": "object", "properties": {"candidate_id": {"type": "string"}}}
    # A deployment without a task list was admitted for situated planning only.
    response = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={**request, "response_format": {"json_schema": {"schema": routing}}},
    )
    assert response.status_code == 422 and "not admitted" in response.text and "routing" in response.text
    unknown = {"type": "object", "properties": {"text": {"type": "string"}}}
    response = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={**request, "response_format": {"json_schema": {"schema": unknown}}},
    )
    assert response.status_code == 422
