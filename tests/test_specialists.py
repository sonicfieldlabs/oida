import numpy as np
import pytest
import soundfile as sf
from oida.specialists.runtime import Specialists, digest
from oida.operation_control import Control, controlled, OperationCancelled


def source(tmp_path):
    path = tmp_path / "audio.wav"
    sf.write(path, np.zeros((48000, 2)), 48000)
    return path


def admitted(monkeypatch):
    runtime = Specialists("missing-file")
    manifest = dict(
        contract="earworm/model-deployment/v1",
        id="test",
        owner="oida",
        adapter="efficientat-mn04",
        capabilities=["tag_events"],
        components=[dict(id="weights", revision="a" * 64, sha256="a" * 64)],
        runtime_revision="b" * 64,
        license_review="test",
        validation_receipt="test",
        enabled=True,
        provisioned=True,
        max_input_seconds=10,
        max_output_seconds=0,
        measured_peak_memory_mib=100,
    )
    runtime.registry.register_adapter(
        "efficientat-mn04", ["tag_events"], verify=lambda manifest: []
    )
    runtime.registry.admit(manifest)
    # T5: entries are keyed by (task, deployment id); the manifest id is the
    # deployment identity, and the task default points at it.
    runtime.entries[("tag_events", manifest["id"])] = {"manifest": manifest}
    runtime.defaults["tag_events"] = manifest["id"]
    return runtime


def output(path):
    return dict(
        duration_seconds=1,
        view_sha256="b" * 64,
        sample_rate_hz=32000,
        channels=1,
        transformations=["mono", "resample"],
        result=dict(status="undetermined", labels=[]),
        limitations=["synthetic test"],
        source_sha256=digest(path),
        wall_seconds=0.1,
        peak_memory_mib=100,
    )


def test_typed_evidence_is_separate_and_source_is_unchanged(tmp_path, monkeypatch):
    path = source(tmp_path)
    before = path.read_bytes()
    runtime = admitted(monkeypatch)
    monkeypatch.setattr(runtime, "run_worker", lambda *a: output(path))
    lanes = runtime.execute(
        path, ["tag_events", "track_beats"], asset_id="source-asset"
    )
    assert lanes[0]["evidence"]["evidence_kind"] == "undetermined"
    assert lanes[0]["evidence"]["view"]["asset_id"] == "source-asset"
    assert lanes[0]["frame"]["frame_id"] == lanes[0]["evidence"]["frame_id"]
    assert lanes[1]["status"] == "unavailable"
    assert path.read_bytes() == before


def test_failure_and_covenant_do_not_invent_evidence(tmp_path, monkeypatch):
    from oida.covenant import CovenantEngine, parse_covenant

    runtime = admitted(monkeypatch)
    path = source(tmp_path)

    def fail(*args):
        raise RuntimeError("worker failed")

    monkeypatch.setattr(runtime, "run_worker", fail)
    assert runtime.execute(path, ["tag_events"], asset_id="a")[0]["status"] == "failed"
    # Broken output from one worker must stay an explicit failed lane.
    monkeypatch.setattr(runtime, "run_worker", lambda *a: {})
    malformed = runtime.execute(path, ["tag_events", "track_beats"], asset_id="a")
    assert [lane["status"] for lane in malformed] == ["failed", "unavailable"]
    covenant = CovenantEngine(parse_covenant("# test\n- ignore: speech"))
    lane = runtime.execute(path, ["tag_events"], asset_id="a", covenant=covenant)[0]
    assert lane["status"] == "withheld" and "evidence" not in lane
    with pytest.raises(ValueError):
        runtime.execute(path, ["tag_events", "tag_events"], asset_id="a")


def test_cancel_is_not_converted_to_model_failure(tmp_path, monkeypatch):
    runtime = admitted(monkeypatch)
    path = source(tmp_path)
    control = Control()

    def cancel(*args):
        control.cancel()
        raise OperationCancelled()

    monkeypatch.setattr(runtime, "run_worker", cancel)
    with controlled(control), pytest.raises(OperationCancelled):
        runtime.execute(path, ["tag_events"], asset_id="a")


def test_partial_interpretation_keeps_dsp_but_legacy_failure_remains(monkeypatch):
    from oida.specialists import dispatch
    from oida.engine_base import EngineUnavailable

    calls = []

    def report(*args, **kw):
        calls.append(kw["passes"])
        if kw["passes"]:
            raise EngineUnavailable("missing")
        return {"dsp": "retained"}

    monkeypatch.setattr(dispatch, "report", report)
    result, status = dispatch.interpret(
        None,
        "path",
        "oida",
        passes=["caption"],
        chunk_seconds=10,
        overlap_seconds=0,
        partial=True,
    )
    assert result == {"dsp": "retained"} and status["status"] == "failed"
    with pytest.raises(EngineUnavailable):
        dispatch.interpret(
            None,
            "path",
            "oida",
            passes=["caption"],
            chunk_seconds=10,
            overlap_seconds=0,
        )


def test_gateway_retains_specialist_receipts_in_new_record(tmp_path, monkeypatch):
    from oida.server import create_app
    from fastapi.testclient import TestClient
    import os

    for key in list(os.environ):
        if key.startswith(("OIDA_", "HMM_", "AKOUSMATA_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    path = source(tmp_path)
    before = digest(path)
    runtime = admitted(monkeypatch)
    monkeypatch.setattr(runtime, "run_worker", lambda *a: output(path))
    monkeypatch.setattr("oida.specialists.runtime.Specialists", lambda: runtime)
    client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    response = client.post(
        "/gateway/listen",
        json=dict(
            path=str(path),
            route_preset="signal",
            remember=True,
            specialist_tasks=["tag_events"],
            operation_id="specialist-test",
            response_mode="summary",
        ),
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["listening_event"]["specialist_evidence"][0]["status"] == "complete"
    record = client.get("/owner/records/" + value["akousma_id"]).json()
    # The endpoint wraps the canonical record.
    record = record.get("record", record)
    assert "oida.specialist.tag_events" in record["listening"]
    assert digest(path) == before


def test_cancel_terminates_worker_process_and_releases_queue(tmp_path, monkeypatch):
    import subprocess
    import sys
    import threading
    import oida.specialists.runtime as module

    runtime = admitted(monkeypatch)
    path = source(tmp_path)
    marker = tmp_path / "started"
    (tmp_path / "worker.py").write_text(
        "from pathlib import Path\nimport time\nPath("
        + repr(str(marker))
        + ").touch()\ntime.sleep(60)\n"
    )
    monkeypatch.setattr(module, "__file__", str(tmp_path / "runtime.py"))
    monkeypatch.setattr(runtime, "verify", lambda entry: [])
    entry = {
        **runtime.entries[("tag_events", "test")],
        "python": sys.executable,
        "repository": str(tmp_path),
        "checkpoint": str(tmp_path / "unused"),
    }
    children = []
    spawn = subprocess.Popen

    def start(*args, **kw):
        child = spawn(*args, **kw)
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, "Popen", start)
    event = threading.Event()
    control = Control(
        event=event, recheck=lambda: event.set() if marker.exists() else None
    )
    with controlled(control), pytest.raises(OperationCancelled):
        runtime.run_worker(path, "tag_events", entry)
    assert children and children[0].poll() is not None
    assert runtime.slot.acquire(blocking=False)
    runtime.slot.release()
    for _ in range(4):
        assert runtime.waiters.acquire(blocking=False)


def test_source_mutation_cannot_commit_compound_evidence(tmp_path, monkeypatch):
    path = source(tmp_path)
    runtime = admitted(monkeypatch)
    value = output(path)

    def mutate(*args):
        path.write_bytes(b"changed")
        return value

    monkeypatch.setattr(runtime, "run_worker", mutate)
    with pytest.raises(ValueError, match="Source changed"):
        runtime.execute(path, ["tag_events"], asset_id="source")


# --- T5: explicit specialist deployment selection ----------------------------


def two_deployments(tmp_path):
    """One task with two admitted deployments; mn04 is the configured default."""
    from oida.specialists.runtime import Specialists

    runtime = Specialists(config_path=str(tmp_path / "missing.json"))
    manifests = {}
    for name, revision in (
        ("efficientat-mn04", "a" * 64),
        ("efficientat-mn10", "c" * 64),
    ):
        manifest = dict(
            contract="earworm/model-deployment/v1",
            owner="oida",
            id=name,
            adapter=name,
            capabilities=["tag_events"],
            components=[dict(id="weights", revision=revision, sha256="0" * 64)],
            runtime_revision="b" * 64,
            license_review="test",
            validation_receipt="test",
            enabled=True,
            provisioned=True,
            max_input_seconds=10,
            max_output_seconds=0,
            measured_peak_memory_mib=100,
        )
        runtime.registry.register_adapter(name, ["tag_events"], verify=lambda entry: [])
        runtime.registry.admit(manifest)
        runtime.entries[("tag_events", name)] = {"manifest": manifest}
        manifests[name] = manifest
    runtime.defaults["tag_events"] = "efficientat-mn04"
    return runtime, manifests


def test_two_deployments_list_with_the_first_as_default(tmp_path):
    runtime, manifests = two_deployments(tmp_path)
    task = next(t for t in runtime.options() if t["id"] == "tag_events")
    ids = [d["id"] for d in task["deployments"]]
    assert ids == ["efficientat-mn04", "efficientat-mn10"]
    assert task["deployments"][0]["default"] is True
    assert task["deployments"][1]["default"] is False


def test_explicit_deployment_selects_its_own_manifest(tmp_path, monkeypatch):
    runtime, manifests = two_deployments(tmp_path)
    path = source(tmp_path)
    monkeypatch.setattr(runtime, "run_worker", lambda *a, **k: output(path))
    selected = runtime.execute(
        path,
        ["tag_events"],
        asset_id="a",
        deployments={"tag_events": "efficientat-mn10"},
    )[0]
    assert selected["status"] == "complete"
    assert selected["deployment_id"] == "efficientat-mn10"
    assert selected["evidence"]["deployment_id"] == "efficientat-mn10"
    assert selected["evidence"]["model_revision"] == "c" * 64


def test_task_only_requests_keep_the_configured_default(tmp_path, monkeypatch):
    runtime, manifests = two_deployments(tmp_path)
    path = source(tmp_path)
    monkeypatch.setattr(runtime, "run_worker", lambda *a, **k: output(path))
    selected = runtime.execute(path, ["tag_events"], asset_id="a")[0]
    assert selected["deployment_id"] == "efficientat-mn04"
    assert selected["evidence"]["model_revision"] == "a" * 64


def test_unknown_explicit_deployment_is_unavailable_not_a_finding(tmp_path):
    runtime, manifests = two_deployments(tmp_path)
    path = source(tmp_path)
    selected = runtime.execute(
        path,
        ["tag_events"],
        asset_id="a",
        deployments={"tag_events": "efficientat-mn99"},
    )[0]
    assert selected["status"] == "unavailable"
    assert "not admitted" in selected["reason"]
    assert "no analysis was substituted" in selected["reason"]
    assert "evidence" not in selected


def test_deployment_selection_for_an_unselected_task_is_invalid(tmp_path):
    runtime, _ = two_deployments(tmp_path)
    path = source(tmp_path)
    with pytest.raises(ValueError, match="not selected"):
        runtime.execute(
            path,
            ["track_beats"],
            asset_id="a",
            deployments={"tag_events": "efficientat-mn04"},
        )
