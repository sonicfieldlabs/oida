import hashlib
import json
import numpy as np
import soundfile as sf
from oida.apparatus_gate import gate_spectral_request
from test_input_binding import loaded as loaded


def positive(tmp_path, engine):
    engine._weight_provenance["fixture-resident"] = {
        "status": "known",
        "algorithm": "synthetic-test-double",
        "sha256": "a" * 64,
    }
    path = tmp_path / "input.wav"
    sf.write(path, np.zeros(12000, dtype=np.float32), 24000)
    subject = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    binding = engine.prepare_input_binding(str(path), "instruct")
    access = dict(
        contract="earworm/listening-access/v1",
        declaration_id="access:approved",
        subject_ref=subject,
        capture=dict(
            status="known",
            apparatus_ref="fixture:apparatus",
            supported_band_hz=dict(lower=1, upper=10000),
            evidence_refs=["fixture:calibration"],
        ),
        sampled_representation=dict(
            status="known",
            representation_ref=binding["representation_ref"],
            sample_rate_hz=24000,
            channels=1,
            retained_band_hz=dict(lower=0, upper=12000),
            evidence_refs=["fixture:source"],
        ),
        model_input=dict(
            status="known",
            model_ref=binding["model_ref"],
            representation_ref=binding["representation_ref"],
            sample_rate_hz=24000,
            channels=1,
            effective_band_hz=dict(lower=1, upper=10000),
            window_s=dict(start=0, end=0.5),
            preprocessing_refs=[],
            evidence_refs=["fixture:competence"],
            blind_spots=[],
        ),
        human_access=[dict(status="unknown", reason="No human access evidence")],
    )
    entries = []
    for kind, refs in [
        ("capture", ["fixture:apparatus", "fixture:calibration"]),
        ("sampled_representation", ["fixture:source"]),
        (
            "model_input",
            [binding["model_ref"], binding["representation_ref"], "fixture:competence"],
        ),
    ]:
        for ref in refs:
            data = dict(
                ref=ref, kind=kind, subject_ref=subject, declaration=access[kind]
            )
            if kind == "model_input":
                data.update(
                    binding_ids={"instruct": binding["binding_id"]}, preprocessing=[]
                )
            name = str(len(entries)) + ".json"
            raw = json.dumps(data).encode()
            (tmp_path / name).write_bytes(raw)
            entries.append(
                dict(
                    ref=ref,
                    kind=kind,
                    file=name,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    expires_at="2099-01-01T00:00:00Z",
                )
            )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(dict(contract="oida/apparatus-evidence/v1", entries=entries))
    )
    request = dict(
        contract="akouo/extended-spectrum-request/v0.1",
        request_id="request:positive",
        subject_ref=subject,
        band_hz=dict(lower=100, upper=200),
        window_s=dict(start=0, end=0.5),
        channel_count=1,
        claim_kind="spectral_measurement",
        resolved_refs=[],
        preprocessing=[],
    )
    return path, access, request, manifest


def test_positive_requires_exact_prepared_approval_and_expiry(loaded, tmp_path):
    engine, processor, state = loaded
    path, access, request, manifest = positive(tmp_path, engine)

    def decide():
        return gate_spectral_request(
            path,
            access,
            request,
            evidence_manifest=manifest,
            prepare_bindings=lambda _: {
                "instruct": engine.prepare_input_binding(str(path), "instruct")
            },
        )

    value = decide()
    assert value["measurement_permitted"], value
    assert value["execution_bindings"] and value["claim_status"] == "undetermined"
    state["samples"][0] = 0.1
    assert not decide()["measurement_permitted"]
    state["samples"][0] = 0
    index = json.loads(manifest.read_text())
    index["entries"][-1]["expires_at"] = "2000-01-01T00:00:00Z"
    manifest.write_text(json.dumps(index))
    assert not decide()["measurement_permitted"]
    assert state["calls"] == 0


def test_owner_positive_route_has_durable_decision_and_binding(
    loaded, tmp_path, monkeypatch
):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from oida.server import create_app

    engine, processor, state = loaded
    path, access, request, manifest = positive(tmp_path, engine)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    monkeypatch.setenv("OIDA_APPARATUS_EVIDENCE", str(manifest))
    with patch("oida.server.build_engine", return_value=engine):
        client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    response = client.post(
        "/gateway/listen",
        json=dict(
            path=str(path),
            listening_access=access,
            spectral_request=request,
            remember=True,
            operation_id="positive",
        ),
    )
    assert response.status_code == 200, response.text
    assert state["calls"] == 1
    record_id = response.json()["akousma_id"]
    assert (
        record_id
        and client.get("/operations/positive").json()["akousma_id"] == record_id
    )
    events = client.get("/owner/journal").json()["events"]
    assert any(
        e["kind"] == "apparatus_decision" and e["payload"]["measurement_permitted"]
        for e in events
    )
    assert (
        client.post(
            "/gateway/listen", json=dict(path=str(path), operation_id="positive")
        ).status_code
        == 409
    )


def test_standalone_helper_binds_source_and_submitted_audio(tmp_path):
    from oida.reporting import caption
    from oida.engine_stub import StubMossEngine

    path = tmp_path / "standalone.wav"
    sf.write(path, np.zeros(16000, dtype=np.float32), 16000)
    _, result = caption(StubMossEngine(), str(path))
    receipt = result.pass_provenance[0]
    assert receipt["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["submitted_audio"]["sha256"] == receipt["source"]["sha256"]
    assert receipt["pass_id"] and receipt["source"]["chunk_index"] is None


def test_each_actual_chunk_acquires_its_own_prepared_lease(
    loaded, tmp_path, monkeypatch
):
    from oida.pass_provenance import SourceBoundEngine
    from oida.recipes import get_recipe
    import sys

    engine, _, state = loaded
    samples = np.concatenate(
        [np.zeros(12000, dtype=np.float32), np.ones(12000, dtype=np.float32) * 0.1]
    )
    source = tmp_path / "whole.wav"
    sf.write(source, samples, 24000, subtype="FLOAT")
    paths = []
    inputs = {}
    for index in range(2):
        path = tmp_path / f"chunk-{index}.wav"
        sf.write(
            path, samples[index * 12000 : (index + 1) * 12000], 24000, subtype="FLOAT"
        )
        paths.append(path)
        inputs[str(path.resolve())] = {
            "window_s": {"start": index * 0.5, "end": (index + 1) * 0.5},
            "chunk_index": index,
        }
    monkeypatch.setattr(
        sys.modules["src.audio_io"],
        "load_audio",
        lambda path, sample_rate: sf.read(path, dtype="float32")[0],
    )
    wrapped = SourceBoundEngine(
        engine,
        source,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        1.0,
        inputs=inputs,
    )
    receipts = [
        wrapped.generate(
            str(path), "fixture", get_recipe("caption_dense").settings
        ).pass_provenance[0]
        for path in paths
    ]
    assert receipts[0]["input_binding_id"] != receipts[1]["input_binding_id"]
    assert [r["source"]["chunk_index"] for r in receipts] == [0, 1]
    assert all(
        r["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        for r in receipts
    )
