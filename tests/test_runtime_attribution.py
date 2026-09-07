from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from jsonschema import validate

from oida.engine import FallbackEngine
from oida.engine_base import EngineResult, EngineUnavailable, MossEngine
from oida.engine_stub import StubMossEngine
from oida.pass_provenance import pass_receipt, weight_inventory
from oida.recipes import get_recipe
from oida.reporting import aggregate_engine_results, make_engine_info
from oida.server import create_app


def test_each_model_survives_aggregation_and_schema():
    settings = get_recipe("caption_dense").settings
    results = [
        EngineResult(
            text="",
            model=m,
            profile="adapter",
            settings=settings,
            pass_provenance=[
                pass_receipt(model=m, provider=m, model_kind=settings.model_kind)
            ],
        )
        for m in ("first", "second")
    ]
    result = aggregate_engine_results(results)
    assert [p["model"] for p in result.pass_provenance] == ["first", "second"]
    info = make_engine_info(result, []).model_dump()
    schema = json.loads(
        (
            Path(__import__("oida").__file__).parent / "schemas/perception-report.schema.json"
        ).read_text()
    )
    validate(info, schema["properties"]["engine"])
    assert all(
        p["effective_input"]["status"] == "unknown" for p in info["pass_provenance"]
    )


def test_fallback_preserves_actual_stub_attribution():
    class Missing(MossEngine):
        profile = "mac-mps"

        def generate(self, *args, **kwargs):
            raise EngineUnavailable("fixture: unavailable")

    result = FallbackEngine(Missing(), StubMossEngine()).generate(
        "fixture", "", get_recipe("caption_dense").settings
    )
    assert result.profile == "mac-mps"  # Existing compatibility field.
    assert result.pass_provenance[0]["provider"] == "stub"
    assert result.pass_provenance[0]["effective_input"]["status"] == "not_applicable"


def test_weight_inventory_is_byte_based_without_absolute_paths(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(
        b"fixture weights, not a working model"
    )
    before = weight_inventory(tmp_path)
    assert (
        before["files"][0]["sha256"]
        == hashlib.sha256(b"fixture weights, not a working model").hexdigest()
    )
    assert str(tmp_path) not in json.dumps(before)
    (tmp_path / "model.safetensors").write_bytes(b"changed")
    assert weight_inventory(tmp_path)["sha256"] != before["sha256"]
    assert weight_inventory(tmp_path / "missing")["status"] == "unknown"


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith(("OIDA_", "HMM_", "AEAR_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    return TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")


def fixture_request(tmp_path):
    path = tmp_path / "pulse.wav"
    sf.write(path, np.zeros(16_000, dtype=np.float32), 16_000)
    subject = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    access = {
        "contract": "earworm/listening-access/v1",
        "declaration_id": "fixture-access",
        "subject_ref": subject,
        "capture": {"status": "unknown", "reason": "No physical capture evidence"},
        "sampled_representation": {
            "status": "known",
            "representation_ref": "repr:source",
            "sample_rate_hz": 16000,
            "channels": 1,
            "retained_band_hz": {"lower": 0, "upper": 8000},
            "evidence_refs": ["evidence:source"],
        },
        "model_input": {
            "status": "unknown",
            "reason": "No model consumed this fixture",
        },
        "human_access": [{"status": "unknown", "reason": "No listening evidence"}],
    }
    request = {
        "contract": "akouo/extended-spectrum-request/v0.1",
        "request_id": "fixture-request",
        "subject_ref": subject,
        "band_hz": {"lower": 10000, "upper": 20000},
        "window_s": {"start": 0, "end": 1},
        "channel_count": 1,
        "claim_kind": "spectral_measurement",
        "resolved_refs": [subject, "evidence:source"],
        "preprocessing": [],
    }
    return {"path": str(path), "listening_access": access, "spectral_request": request}


def test_gateway_abstains_before_model_or_memory(client, tmp_path):
    request = fixture_request(tmp_path)
    with patch("oida.server.report", side_effect=AssertionError("must not run")):
        response = client.post("/gateway/listen", json={**request, "remember": True})
    assert response.status_code == 409, response.text
    decision = response.json()["detail"]["apparatus_decision"]
    assert decision["support"] == "unsupported"
    assert decision["decision"]["outcome"] == "abstain"
    assert decision["claim_status"] == "undetermined"
    assert not decision["measurement_permitted"]


def test_gateway_rejects_wrong_source_and_missing_pair(client, tmp_path):
    request = fixture_request(tmp_path)
    request["listening_access"]["sampled_representation"]["sample_rate_hz"] = 96000
    assert client.post("/gateway/listen", json=request).status_code == 400
    del request["spectral_request"]
    assert client.post("/gateway/listen", json=request).status_code == 400


def test_ordinary_stub_event_retains_every_pass_receipt(client, tmp_path):
    request = fixture_request(tmp_path)
    response = client.post(
        "/gateway/listen", json={"path": request["path"], "remember": True}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # The attributed report travels with the existing event rather than a new store.
    receipts = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("contract") == "oida/pass-provenance/v1":
                receipts.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(body)
    from akousma import AkousmataStore, load_schema

    store = AkousmataStore(tmp_path / "store")
    rows = store.query(limit=2)
    assert len(rows) == 1
    # Read the retained canonical account via the existing store API.
    stored = store.get(rows[0]["akousma_id"])
    validate(stored, load_schema())
    before = len(receipts)
    visit(stored)
    assert len(receipts) > before
    store.close()
    assert receipts
    assert all(
        p["provider"] == "stub" and p["effective_input"]["status"] == "not_applicable"
        for p in receipts
    )


def test_mps_receipt_uses_actual_processor_rate_and_array(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace, ModuleType
    from oida.engine_mps import MpsMossEngine

    engine = MpsMossEngine(SimpleNamespace(moss_audio_repo=None))
    engine._model_id = lambda settings: "fixture-resident"
    engine._loaded_revisions["fixture-resident"] = "a" * 40
    engine._weight_provenance["fixture-resident"] = {
        "status": "unknown",
        "reason": "mock model",
    }

    class Inputs(dict):
        def to(self, device):
            return self

    class Processor:
        config = SimpleNamespace(mel_sr=24000)
        tokenizer = SimpleNamespace(eos_token_id=0)
        audio_token_id = 7

        def __call__(self, **kwargs):
            assert kwargs["audios"][0].shape == (12000,)
            return Inputs(input_ids=np.zeros((1, 3), dtype=int))

    model = SimpleNamespace(
        device="cpu", generate=lambda **kwargs: np.zeros((1, 5), dtype=int)
    )
    engine._load_pair = lambda model_id: (model, Processor())
    module = ModuleType("src.audio_io")

    def load_audio(path, sample_rate):
        assert sample_rate == 24000
        return np.zeros(12000, dtype=np.float32)

    module.load_audio = load_audio
    with (
        patch.dict(
            "sys.modules",
            {"torch": SimpleNamespace(no_grad=nullcontext), "src.audio_io": module},
        ),
        patch("oida.engine_mps._safe_decode", return_value="fixture"),
    ):
        result = engine.generate(
            str(tmp_path / "mock.wav"), "fixture", get_recipe("caption_dense").settings
        )
    receipt = result.pass_provenance[0]
    assert receipt["effective_input"]["sample_rate_hz"] == 24000
    assert receipt["effective_input"]["channels"] == 1
    assert receipt["effective_input"]["duration_s"] == 0.5
    assert receipt["revision"]["value"] == "a" * 40


def test_forged_resolved_refs_cannot_grant_support(client, tmp_path):
    request = fixture_request(tmp_path)
    request["spectral_request"]["band_hz"] = {"lower": 100, "upper": 1000}
    request["spectral_request"]["resolved_refs"] += [
        "repr:source",
        "model:fake",
        "calibration:fake",
    ]
    response = client.post("/gateway/listen", json=request)
    assert response.status_code == 409, response.text
    result = response.json()["detail"]["apparatus_decision"]
    assert result["support"] == "undetermined"
    assert result["decision"]["outcome"] == "abstain"


def test_missing_packaged_contract_is_unavailable_not_server_crash(client, tmp_path):
    request = fixture_request(tmp_path)
    with patch(
        "oida.apparatus_gate.extended_spectrum_decision",
        side_effect=FileNotFoundError("schema"),
    ):
        response = client.post("/gateway/listen", json=request)
    assert response.status_code == 503
    assert "contracts unavailable" in response.json()["detail"]
