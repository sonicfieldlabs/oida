"""A caller's deadline binds listening work, and every pass says where its time went (V04).

On 24 September a one-second caption-only listening spent 395 s inside one MOSS pass while its
caller's 360-second window expired, and nothing retained could say whether the time was spent
waiting for the engine or generating. Passes now record wait and generation time, and a
deadline sent by the caller is honoured while waiting, before generating and during generation.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from test_input_binding import loaded  # noqa: F401

from oida.operation_control import (
    Control,
    DeadlineReached,
    Operations,
    controlled,
    current_deadline,
)
from oida.recipes import get_recipe


class Journal:
    producer_id = "test"

    def __init__(self):
        self.rows = {}

    def snapshots(self, **_):
        return {"snapshots": [], "has_more": False, "high_water_sequence": 0}

    def save(self, kind, identifier, value):
        self.rows[(kind, identifier)] = value

    def get(self, kind, identifier):
        return self.rows.get((kind, identifier))


def test_every_pass_records_wait_generation_and_why_it_stopped(loaded):  # noqa: F811
    engine, _, _ = loaded
    result = engine.generate("fixture.wav", "fixture", get_recipe("caption_dense").settings)
    generation = result.pass_provenance[0]["generation"]
    assert generation["stop_reason"] == "eos"
    assert generation["new_tokens"] == 2 and generation["deadline_bound"] is False
    assert generation["engine_wait_ms"] >= 0 and generation["generate_ms"] >= 0


def test_a_deadline_reaches_the_generation_as_max_time(loaded):  # noqa: F811
    engine, _, _ = loaded
    seen = {}
    model, processor = engine._load_pair("x")

    def generate(**kwargs):
        seen.update(kwargs)
        return np.zeros((1, 5), dtype=int)

    model.generate = generate
    with controlled(Control(deadline_at=time.time() + 30)):
        assert current_deadline() is not None
        result = engine.generate("fixture.wav", "fixture", get_recipe("caption_dense").settings)
    assert 25 < seen["max_time"] <= 30
    assert result.pass_provenance[0]["generation"]["deadline_bound"] is True


def test_a_generation_stopped_by_the_deadline_is_discarded(loaded):  # noqa: F811
    engine, _, _ = loaded
    model, _ = engine._load_pair("x")
    deadline = time.time() + 2

    def generate(**kwargs):
        # transformers' max_time: stop when the time is up, without an end-of-sequence token.
        while time.time() < deadline:
            time.sleep(0.02)
        return np.ones((1, 6), dtype=int)

    model.generate = generate
    with controlled(Control(deadline_at=deadline)):
        with pytest.raises(DeadlineReached, match="during generation"):
            engine.generate("fixture.wav", "fixture", get_recipe("caption_dense").settings)


def test_waiting_for_the_engine_is_bounded_by_the_deadline(loaded):  # noqa: F811
    engine, _, state = loaded
    engine._lock.acquire()
    try:
        with controlled(Control(deadline_at=time.time() + 1.2)):
            started = time.time()
            with pytest.raises(DeadlineReached, match="waiting for the audio model"):
                engine.generate("fixture.wav", "fixture", get_recipe("caption_dense").settings)
        assert time.time() - started < 3
    finally:
        engine._lock.release()
    assert state["calls"] == 0, "nothing was generated"


def test_too_little_time_left_refuses_before_generating(loaded):  # noqa: F811
    engine, _, state = loaded
    with controlled(Control(deadline_at=time.time() + 0.5)):
        with pytest.raises(DeadlineReached, match="before generation"):
            engine.generate("fixture.wav", "fixture", get_recipe("caption_dense").settings)
    assert state["calls"] == 0


def test_the_operation_receipt_says_the_deadline_stopped_it():
    operations = Operations(Journal())

    def work():
        time.sleep(0.15)
        from oida.operation_control import checkpoint

        checkpoint()
        return {"outcome": "complete"}

    with pytest.raises(DeadlineReached):
        operations.run("op-deadline", work, deadline_at=time.time() + 0.1)
    receipt = operations.journal.get("operation", "op-deadline")
    assert receipt["status"] == "cancelled" and receipt["reason"] == "deadline"
    assert operations.run(None, lambda: {"ok": True}) == {"ok": True}
    with pytest.raises(DeadlineReached):
        operations.run(None, work, deadline_at=time.time() + 0.1)


def test_without_a_deadline_admitted_work_still_runs_to_completion(loaded):  # noqa: F811
    engine, _, _ = loaded
    assert current_deadline() is None
    result = engine.generate("fixture.wav", "fixture", get_recipe("caption_dense").settings)
    assert result.pass_provenance[0]["generation"]["deadline_bound"] is False


def test_listen_routes_refuse_a_passed_or_distant_deadline_and_honour_a_current_one(
    tmp_path, monkeypatch
):
    import soundfile as sf
    from fastapi.testclient import TestClient

    from oida.server import create_app

    for key in list(__import__("os").environ):
        if key.startswith(("OIDA_", "HMM_", "AEAR_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    path = tmp_path / "tone.wav"
    sf.write(path, np.zeros(16_000, dtype=np.float32), 16_000)
    body = dict(
        path=str(path),
        seconds=1,
        route_preset="signal",
        ephemeral_delivery=True,
        privacy_mode="incognito",
        raw_audio_policy="not_stored",
        remember=False,
    )
    passed = client.post("/gateway/listen-window", json={**body, "deadline_at": time.time() - 5})
    assert passed.status_code == 409 and "deadline" in passed.text
    distant = client.post(
        "/gateway/listen-window", json={**body, "deadline_at": time.time() + 5 * 3600}
    )
    assert distant.status_code == 400
    current = client.post(
        "/gateway/listen-window",
        json={**body, "deadline_at": time.time() + 120, "operation_id": "op-window-deadline"},
    )
    assert current.status_code == 200, current.text
