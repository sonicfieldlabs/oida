from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace, ModuleType

import numpy as np
import pytest

from oida.engine import FallbackEngine
from oida.engine_base import EngineUnavailable, MossEngine
from oida.engine_mps import MpsMossEngine
from oida.engine_stub import StubMossEngine
from oida.input_binding import (
    InputBindingChanged,
    enforce_input_bindings,
    input_array_receipt,
)
from oida.recipes import get_recipe
from oida.reporting import prepare_report_bindings
from oida.apparatus_gate import gate_spectral_request
from test_runtime_attribution import fixture_request, client as client


@pytest.fixture
def loaded(monkeypatch):
    engine = MpsMossEngine(SimpleNamespace(moss_audio_repo=None))
    engine.model_id_for_kind = lambda kind: "fixture-resident"
    state = {"samples": np.zeros(12000, dtype=np.float32), "calls": 0, "loads": 0}

    class Inputs(dict):
        def to(self, device):
            return self

    class Processor:
        config = SimpleNamespace(mel_sr=24000)
        tokenizer = SimpleNamespace(eos_token_id=0)
        audio_token_id = 7

        def __call__(self, **kwargs):
            return Inputs(input_ids=np.zeros((1, 3), dtype=int))

    processor = Processor()

    def generate(**kwargs):
        state["calls"] += 1
        return np.zeros((1, 5), dtype=int)

    model = SimpleNamespace(device="cpu", generate=generate)
    engine._models["fixture-resident"] = model
    engine._processors["fixture-resident"] = processor
    engine._loaded_revisions["fixture-resident"] = "a" * 40
    engine._weight_provenance["fixture-resident"] = {
        "status": "unknown",
        "reason": "test double",
    }
    engine._load_pair = lambda model_id: (model, processor)
    module = ModuleType("src.audio_io")

    def load_audio(path, sample_rate):
        state["loads"] += 1
        return state["samples"].copy()

    module.load_audio = load_audio
    monkeypatch.setitem(__import__("sys").modules, "src.audio_io", module)
    monkeypatch.setitem(
        __import__("sys").modules, "torch", SimpleNamespace(no_grad=nullcontext)
    )
    monkeypatch.setattr("oida.engine_mps._safe_decode", lambda *args: "fixture")
    return engine, processor, state


def test_preparation_and_execution_use_same_input_identity(loaded):
    engine, processor, state = loaded
    binding = engine.prepare_input_binding("fixture.wav", "instruct")
    assert binding["status"] == "prepared" and state["calls"] == 0
    receipt = binding["receipt"]["effective_input"]
    assert receipt["sample_rate_hz"] == 24000 and receipt["sample_count"] == 12000
    assert receipt["encoding"] == "mono-f32le"
    import hashlib
    assert receipt["sha256"] == hashlib.sha256(np.zeros(12000, dtype="<f4").tobytes()).hexdigest()
    with enforce_input_bindings({"instruct": binding["binding_id"]}):
        result = engine.generate(
            "fixture.wav", "fixture", get_recipe("caption_dense").settings
        )
    assert state["calls"] == 1
    assert result.pass_provenance[0]["input_binding_id"] == binding["binding_id"]
    assert result.pass_provenance[0]["effective_input"] == receipt


@pytest.mark.parametrize("requested_kind,actual_kind", [
    ("music", "thinking"), ("targeted_relisten", "thinking"),
    ("transcription", "instruct"),
])
def test_prepared_role_binding_keeps_loaded_family_identity(
    loaded, monkeypatch, tmp_path, requested_kind, actual_kind
):
    from dataclasses import replace

    monkeypatch.setenv("LISTENINGSTACK_RESOURCE_DIR", str(tmp_path / "fixture-leases"))
    engine, _, state = loaded
    setattr(engine.config, actual_kind + "_model", "fixture-resident")
    binding = engine.prepare_input_binding("fixture.wav", requested_kind)
    assert binding["receipt"]["model_kind"] == actual_kind
    settings = replace(get_recipe("qa").settings, model_kind=requested_kind)
    with enforce_input_bindings({requested_kind: binding["binding_id"]}):
        result = engine.generate("fixture.wav", "fixture", settings)
    assert state["calls"] == 1
    assert result.pass_provenance[0]["input_binding_id"] == binding["binding_id"]
    assert result.pass_provenance[0]["model_kind"] == actual_kind
    # An identical digest under a different invocation role is not permission.
    with enforce_input_bindings({actual_kind: binding["binding_id"]}):
        with pytest.raises(InputBindingChanged):
            engine.generate("fixture.wav", "fixture", settings)
    assert state["calls"] == 1


@pytest.mark.parametrize("change", ["samples", "rate", "revision", "weights", "family"])
def test_drift_is_rejected_before_model_generation(loaded, change):
    engine, processor, state = loaded
    binding = engine.prepare_input_binding("fixture.wav")
    if change == "samples":
        state["samples"][0] = 0.5
    if change == "rate":
        processor.config.mel_sr = 16000
    if change == "revision":
        engine._loaded_revisions["fixture-resident"] = "b" * 40
    if change == "family":
        engine.config.thinking_model = "fixture-resident"
    if change == "weights":
        engine._weight_provenance["fixture-resident"] = {
            "status": "unknown",
            "reason": "different inventory",
        }
    with enforce_input_bindings({"instruct": binding["binding_id"]}):
        with pytest.raises(InputBindingChanged):
            engine.generate(
                "fixture.wav", "fixture", get_recipe("caption_dense").settings
            )
    assert state["calls"] == 0
    # A failed request must not poison later ordinary requests.
    StubMossEngine().generate(
        "fixture.wav", "fixture", get_recipe("caption_dense").settings
    )


def test_unloaded_preparation_never_loads_a_model(loaded):
    engine, processor, state = loaded
    engine._models.clear()
    engine._load_pair = lambda key: pytest.fail("must not load weights")
    assert engine.prepare_input_binding("fixture.wav")["status"] == "unknown"
    assert state["loads"] == 0 and state["calls"] == 0


def test_fallback_cannot_satisfy_a_prepared_model_binding(loaded):
    primary, _, _ = loaded
    binding = primary.prepare_input_binding("fixture.wav")
    primary.generate = lambda *args, **kwargs: (_ for _ in ()).throw(
        EngineUnavailable("fixture")
    )
    engine = FallbackEngine(primary, StubMossEngine())
    with enforce_input_bindings({"instruct": binding["binding_id"]}):
        with pytest.raises(InputBindingChanged):
            engine.generate(
                "fixture.wav", "fixture", get_recipe("caption_dense").settings
            )


def test_long_sources_do_not_preprocess_unbounded_input():
    class Never(MossEngine):
        def prepare_input_binding(self, *args):
            pytest.fail("must not prepare long source")

    bindings = prepare_report_bindings(
        Never(), "fixture.wav", ["caption"], {"durationSeconds": 601}, 600
    )
    assert bindings["instruct"]["status"] == "unknown"


def test_invalid_arrays_do_not_gain_binding_identity():
    with pytest.raises(ValueError):
        input_array_receipt(np.zeros((1, 10), dtype=np.float32), 16000)
    with pytest.raises(ValueError):
        input_array_receipt(np.array([np.nan], dtype=np.float32), 16000)
    with pytest.raises(ValueError):
        input_array_receipt(np.zeros(2, dtype=np.float64), 16000)


def test_gate_exposes_actual_preparation_but_not_measurement_permission(
    loaded, tmp_path
):
    engine, _, state = loaded
    request = fixture_request(tmp_path)
    result = gate_spectral_request(
        __import__("pathlib").Path(request["path"]),
        request["listening_access"],
        request["spectral_request"],
        prepare_bindings=lambda source: prepare_report_bindings(
            engine, request["path"], ["caption"], source, 600
        ),
    )
    assert result["input_bindings"]["instruct"]["status"] == "prepared"
    assert (
        result["measurement_permitted"] is False
        and result["claim_status"] == "undetermined"
    )
    assert state["calls"] == 0


def test_gateway_stub_reports_unknown_input_binding(client, tmp_path):
    result = client.post("/gateway/listen", json=fixture_request(tmp_path))
    assert result.status_code == 409
    bindings = result.json()["detail"]["apparatus_decision"]["input_bindings"]
    assert bindings and all(value["status"] == "unknown" for value in bindings.values())


def test_routed_external_provider_does_not_probe_local_or_remote(loaded):
    from oida.reasoning.audio_router import RoutedAudioEngine
    from oida.reasoning.contracts import ModelRole

    engine, _, state = loaded
    settings = SimpleNamespace(
        load=lambda: SimpleNamespace(
            roles={ModelRole.FAST_PERCEPTION: SimpleNamespace(provider_id="external")}
        )
    )
    routed = RoutedAudioEngine(engine, settings_store=settings, secret_store=None)
    assert routed.prepare_input_binding("fixture.wav")["status"] == "unknown"
    assert state["loads"] == 0 and state["calls"] == 0


def test_representation_identity_includes_rate_and_preflight_is_detached(loaded):
    engine, processor, state = loaded
    first = engine.prepare_input_binding("fixture.wav")
    first["receipt"]["weights"]["reason"] = "caller edit"
    assert engine._weight_provenance["fixture-resident"]["reason"] == "test double"
    processor.config.mel_sr = 16000
    second = engine.prepare_input_binding("fixture.wav")
    assert (
        first["receipt"]["effective_input"]["sha256"]
        == second["receipt"]["effective_input"]["sha256"]
    )
    assert first["representation_ref"] != second["representation_ref"]


def test_declared_input_mismatch_is_reported_as_unsupported(loaded, tmp_path):
    from pathlib import Path

    engine, _, _ = loaded
    request = fixture_request(tmp_path)
    request["listening_access"]["model_input"] = {
        "status": "known",
        "model_ref": "model:wrong",
        "representation_ref": "repr:wrong",
        "sample_rate_hz": 16000,
        "channels": 1,
        "effective_band_hz": {"lower": 0, "upper": 8000},
        "window_s": {"start": 0, "end": 1},
        "preprocessing_refs": [],
        "evidence_refs": ["model-card:fixture"],
        "blind_spots": [],
    }
    result = gate_spectral_request(
        Path(request["path"]),
        request["listening_access"],
        request["spectral_request"],
        prepare_bindings=lambda source: prepare_report_bindings(
            engine, request["path"], ["caption"], source, 600
        ),
    )
    assert (
        result["support"] == "unsupported" and result["measurement_permitted"] is False
    )
    assert "declared sample_rate_hz differs" in result["decision"]["reason"]
