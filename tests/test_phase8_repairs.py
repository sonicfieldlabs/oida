import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from oida.engine_base import EngineUnavailable
from oida.engine_mps import MpsMossEngine
from oida.listening_response import (
    compact_listening_result,
    encoded,
    result_page,
    MAX_SUMMARY_BYTES,
)
from oida.server import GatewayListenRequest, scan_moss_models
from oida.operation_control import Operations, DeadlineReached
from oida.owner_journal import OwnerJournal


def test_configured_checkpoint_outside_weights_has_unique_exact_selector(tmp_path):
    paths = [tmp_path / "one/shared-name", tmp_path / "two/shared-name"]
    for path in paths:
        path.mkdir(parents=True)
        (path / "config.json").write_text("{}")
    inventory = scan_moss_models(tmp_path / "absent", configured=[*paths, paths[0]])
    assert len(inventory) == 2 and inventory[0]["selector"] != inventory[1]["selector"]
    assert {row["path"] for row in inventory} == {str(path.resolve()) for path in paths}


def test_overlapping_roles_cannot_claim_deep_inference():
    engine = object.__new__(MpsMossEngine)
    engine.config = SimpleNamespace(
        instruct_model="/w/MOSS-Audio-4B-Instruct",
        thinking_model="/w/MOSS-Audio-4B-Instruct",
    )
    with pytest.raises(EngineUnavailable, match="one checkpoint"):
        engine._model_id(SimpleNamespace(model_kind="thinking"))
    assert (
        engine._kind_of_loaded(engine.config.instruct_model, "instruct")[0]
        == "instruct"
    )


def test_failed_native_preflight_precedes_model_module_loading():
    engine = object.__new__(MpsMossEngine)
    engine.config = SimpleNamespace(moss_audio_repo="/absent")
    with patch(
        "oida.native_decoder.probe",
        return_value={
            "status": "unavailable",
            "detail": "fixture unsupported native codec",
        },
    ):
        with pytest.raises(EngineUnavailable, match="before model loading"):
            engine._moss_modules()


def test_action_default_is_bounded_and_expansion_has_exact_changed_content_fence():
    assert GatewayListenRequest(path="/fixture").response_mode == "summary"
    event = {
        "id": "generated",
        "aggregate": "a" * 200000,
        "pass_provenance": [{"effective_input": {"sha256": "a" * 64}}],
        "routes": [],
        "session": {"events": [{"aggregate": "b" * 200000}] * 20},
    }
    reply = compact_listening_result(event)
    assert len(encoded(reply)) < MAX_SUMMARY_BYTES
    assert "session" not in reply["listening_event"]
    assert reply["listening_event"]["pass_provenance"] == event["pass_provenance"]
    page = result_page(event, limit=17)
    collected = page["text"]
    while page["has_more"]:
        page = result_page(
            event, offset=page["next_offset"], expected_sha256=page["sha256"]
        )
        collected += page["text"]
    assert json.loads(collected) == event
    with pytest.raises(ValueError, match="changed"):
        result_page(
            {**event, "id": "changed"}, offset=17, expected_sha256=page["sha256"]
        )


def test_deadline_is_settled_separately_from_requested_cancellation(tmp_path):
    import time

    operations = Operations(OwnerJournal(tmp_path / "journal"))
    with pytest.raises(DeadlineReached):
        operations.run("deadline", lambda: {}, deadline_at=time.time() - 1)
    receipt = operations.journal.get("operation", "deadline")
    assert receipt["worker_settled"] and receipt["settlement_reason"] == "deadline"
    assert receipt["cancellation_requested"] is False


def test_restart_after_unsettled_cancel_reports_unknown_without_replay(tmp_path):
    journal = OwnerJournal(tmp_path / "journal")
    ops = Operations(journal)
    ops.save(
        "interrupted",
        "cancelled",
        execution_state="cancellation_requested",
        worker_settled=False,
    )
    restored = Operations(OwnerJournal(journal.path))
    receipt = restored.journal.get("operation", "interrupted")
    assert receipt["status"] == "interrupted" and receipt["worker_settled"] is None
    assert receipt["automatic_replay"] is False


def test_retained_expansion_rechecks_current_covenant(tmp_path, monkeypatch):
    import os
    import numpy as np
    import soundfile as sf
    from fastapi.testclient import TestClient
    from oida.server import create_app
    for key in list(os.environ):
        if key.startswith(("OIDA_", "HMM_", "AEAR_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "memory"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    path = tmp_path / "generated.wav"
    sf.write(path, np.zeros(16000, dtype="float32"), 16000)
    client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    result = client.post("/gateway/listen", json=dict(path=str(path), route_preset="signal", remember=False)).json()
    identifier = result["listening_event"]["id"]
    page = client.get("/listening/results/" + identifier)
    assert page.status_code == 200, page.text
    assert len(page.content) <= 20 * 1024
    policy = client.put("/covenant", json=dict(name="page-policy", text="# Audit\n## rules\n- do not reveal: transcript\n", activate=True))
    assert policy.status_code == 200, policy.text
    assert policy.json()["parsed"]["rules"], policy.json()
    assert client.get("/listening/results/" + identifier).status_code == 423
