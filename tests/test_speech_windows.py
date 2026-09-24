import json
import numpy as np
import pytest
import soundfile as sf
from oida.audio_window import audio_window
from oida.live import LiveManager


def wave(path, seconds=2, rate=8000, value=0.1):
    sf.write(path, np.full((round(seconds * rate), 2), value), rate, subtype="FLOAT")
    return path


def test_file_window_preserves_native_channels_rate_and_origin(tmp_path):
    source = wave(tmp_path / "a.wav", 40, 48000)
    before = source.read_bytes()
    with audio_window(
        source, start_seconds=12.5, seconds=25, temp_dir=tmp_path / "tmp"
    ) as (path, metadata):
        assert sf.info(path).samplerate == 48000
        assert sf.info(path).channels == 2
        assert sf.info(path).duration == 25
        assert metadata["start_seconds"] == 12.5
        assert metadata["source_duration_seconds"] == 40
    assert source.read_bytes() == before and not path.exists()
    for start, seconds in [(0, 61), (-1, 10), (0, float("nan")), (40, 2)]:
        with pytest.raises(ValueError):
            with audio_window(
                source, start_seconds=start, seconds=seconds, temp_dir=tmp_path / "tmp"
            ):
                pass


def test_live_historical_window_never_substitutes_recent_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "data"))
    manager = LiveManager()
    sid = manager.start(ring_seconds=4)["session_id"]
    for i in range(3):
        path = wave(tmp_path / f"{i}.wav", value=(i + 1) / 10)
        manager.ingest_saved_upload(sid, {"path": str(path)})
    status = manager.status(sid)
    assert status["cursor_seconds"] == 6
    assert status["available_start_seconds"] == 2
    result = manager.capture_window(sid, end_seconds=4, seconds=2)
    samples, _ = sf.read(result["path"])
    assert np.allclose(samples, 0.2, atol=1e-4)
    assert result["window"]["start_seconds"] == 2
    for end, seconds in [(2, 2), (7, 2), (4, 61)]:
        with pytest.raises(ValueError):
            manager.capture_window(sid, end_seconds=end, seconds=seconds)
    (tmp_path / "1.wav").unlink()
    with pytest.raises(ValueError, match="missing"):
        manager.capture_window(sid, end_seconds=4, seconds=2)


def test_speech_not_provisioned_preserves_other_lanes(tmp_path):
    from oida.specialists.runtime import Specialists

    path = wave(tmp_path / "a.wav")
    lanes = Specialists("missing").execute(
        path, ["transcribe", "tag_events"], asset_id="a"
    )
    assert [lane["status"] for lane in lanes] == ["unavailable", "unavailable"]


def test_longer_requests_and_legacy_defaults():
    from oida.server import GatewayWindowRequest, LiveCaptureRequest

    assert GatewayWindowRequest(path="/a").seconds == 10
    assert GatewayWindowRequest(path="/a", seconds=60).seconds == 60
    assert (
        LiveCaptureRequest(session_id="a", end_seconds=45, seconds=20).end_seconds == 45
    )
    with pytest.raises(ValueError):
        GatewayWindowRequest(path="/a", seconds=61)


def test_transcript_validation_rejects_invented_or_invalid_timings():
    from oida.specialists.speech_validation import validate_transcript

    value = dict(
        status="hypotheses",
        text="Hola",
        spans=[
            dict(
                text="Hola",
                start_seconds=1,
                end_seconds=2,
                words=[dict(text="Hola", start_seconds=1.1, end_seconds=1.8)],
            )
        ],
    )
    validate_transcript(value, 3)
    with pytest.raises(ValueError):
        validate_transcript(value, 1)
    value["spans"][0]["words"][0]["end_seconds"] = 4
    with pytest.raises(ValueError):
        validate_transcript(value, 3)
    with pytest.raises(ValueError):
        validate_transcript(dict(status="undetermined", text="invented", spans=[]), 3)


def test_ephemeral_window_and_speech_origin_are_retained(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from oida.server import create_app
    import os

    for key in list(os.environ):
        if key.startswith(("OIDA_", "HMM_", "AKOUSMATA_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    path = wave(tmp_path / "source.wav", 40)
    response = client.post(
        "/gateway/listen-window",
        json=dict(
            path=str(path),
            start_seconds=8,
            seconds=25,
            route_preset="signal",
            specialist_tasks=["transcribe"],
            ephemeral_delivery=True,
            privacy_mode="incognito",
            raw_audio_policy="not_stored",
            remember=False,
        ),
    )
    assert response.status_code == 200, response.text
    value = response.json()["listening_event"]
    assert value["segment"]["duration_ms"] == 25000
    assert value["segment"]["metadata"]["file_window"]["start_seconds"] == 8
    assert value["specialist_evidence"][0]["time_origin"]["start_seconds"] == 8
    assert value["specialist_evidence"][0]["status"] == "unavailable"


@pytest.mark.parametrize(
    "deployments", ["invalid", {"task": "transcribe"}, [None], [42]]
)
def test_malformed_optional_speech_configuration_does_not_disable_owner(
    tmp_path, monkeypatch, deployments
):
    from oida.specialists.runtime import Specialists

    path = tmp_path / "speech.json"
    path.write_text(json.dumps(dict(deployments=deployments)))
    monkeypatch.setenv("OIDA_SPEECH_CONFIG", str(path))
    runtime = Specialists("missing")
    # T5 adds the speech_quality lane; a malformed config must leave every
    # lane reported with its own unavailable reason.
    assert len(runtime.options()) == 4
    assert all(not option["available"] for option in runtime.options())
