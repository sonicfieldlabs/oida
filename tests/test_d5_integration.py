# ruff: noqa: F811
import json
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest
from akousma import AkousmataStore
from oida.cosmo_subscription import request_from_feed
from oida.listening_bundles import package_passes, receive
from test_source_capture import setup  # noqa: F401
from test_observation_source import payload, validator_module  # noqa: F401


def test_cosmo_poll_uses_observation_owner_and_retry(setup, payload, validator_module, monkeypatch):
    client = setup()
    monkeypatch.setenv("OIDA_COSMOAUDITION_URL", "http://127.0.0.1:7777")
    feed = dict(contract="cosmo/observation-feed/v1", relation={"of": "signal"}, source_register="non-acoustic", producer={"acquisitionMode": "fixture", "producerId": payload["producer_id"]}, source_record=payload["source_record"])
    calls = []
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json=feed)
    factory = httpx.Client
    body = dict(mode="fixture", signal_id=payload["source_record"]["observations"][0]["field"], consent_ref="fixture-permission", remember=True, operation_id="d5-poll")
    with patch("oida.cosmo_subscription.httpx.Client", side_effect=lambda **kw: factory(transport=httpx.MockTransport(handler), **kw)):
        first = client.post("/sources/cosmoaudition/poll", json=body)
        assert first.status_code == 200, first.text
        second = client.post("/sources/cosmoaudition/poll", json=body)
    assert second.status_code == 409, second.text
    assert len(calls) == 1
    assert first.json()["akousma_id"] == second.json()["detail"]["receipt"]["akousma_id"]
    assert "audio" not in first.json()["record"]
    assert first.json()["record"]["listening"]["akouo.observation"]["payload"]["source_snapshot"] == payload["source_record"]


def test_cosmo_remote_origin_refused(monkeypatch):
    monkeypatch.setenv("OIDA_COSMOAUDITION_URL", "https://example.org")
    with pytest.raises(ValueError, match="loopback"):
        request_from_feed(dict(mode="fixture", observation_ref="x", consent_ref="x", remember=False, operation_id="x"))


def test_embedded_library_keeps_the_owner_request_boundary(setup):
    from fastapi.testclient import TestClient

    configured = setup()
    local = TestClient(configured.app, base_url="http://127.0.0.1", client=("127.0.0.1", 53001))
    assert local.get("/library/api/health").status_code == 200
    assert local.get("/library/api/health", headers={"Host": "foreign.example"}).status_code in {400, 403}
    assert local.get("/library/api/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 400
    assert local.post("/library/api/wiki/rebuild", headers={"Origin": "https://foreign.example"}).status_code == 403
    remote = TestClient(configured.app, base_url="http://127.0.0.1", client=("203.0.113.8", 53002))
    assert remote.get("/library/api/health").status_code == 403


def test_cached_pass_delegate_and_current_forgetting(tmp_path, monkeypatch):
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "source"))
    record = json.loads((Path(__file__).parent / "fixtures/sources/cached-listening.json").read_text())
    record["provenance"]["consent_status"] = "owned"
    with AkousmataStore(tmp_path / "source") as source, AkousmataStore(tmp_path / "target") as target:
        source.put(record)
        selected = dict(record_ref=record["akousma_id"], listening_ref=record["auditum"]["listenings"][0]["listening_id"])
        result = package_passes(source, [selected])
        assert result["execution"] == "not_requested"
        contracts = ["earworm/listening-memories/v1", "earworm/akousma/v1.6", "earworm/akousma/v1.7"]
        data = Path(result["archive"]).read_bytes()
        receive(target, data, supported_contracts=contracts)
        assert target.get(record["akousma_id"]) == record
        target.forget(record["akousma_id"])
        with pytest.raises(ValueError, match="forgetting"):
            receive(target, data, supported_contracts=contracts)
        with pytest.raises(ValueError, match="resolve"):
            package_passes(source, [{**selected, "listening_ref": "missing"}])
