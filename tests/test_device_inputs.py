import shutil
import time
from unittest.mock import Mock

import pytest

from oida import device_inputs as inputs
from oida.live import LiveManager


def test_system_output_requires_actual_loopback(monkeypatch):
    monkeypatch.delenv('OIDA_SYSTEM_OUTPUT_DEVICE', raising=False)
    assert inputs.system_output_backend()['status'] == 'unavailable'
    monkeypatch.setenv('OIDA_SYSTEM_OUTPUT_DEVICE', '0')
    monkeypatch.setattr(inputs, 'audio_devices', lambda: [{'id':'0','label':'Microphone'}])
    assert inputs.system_output_backend()['status'] == 'unavailable'
    monkeypatch.setattr(inputs, 'audio_devices', lambda: [{'id':'0','label':'BlackHole 2ch'}])
    result = inputs.system_output_backend()
    assert result['status'] == 'available'
    assert result['memory'] == 'record' and result['raw_audio_policy'] == 'temp'
    assert result['monitor_playback'] is False


def test_device_inventory_excludes_screen_devices():
    text = """[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] Capture screen 0
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] BlackHole 2ch
[AVFoundation indev @ 0x1] [1] USB Audio Interface"""
    assert inputs.parse_devices(text) == [
        {"id": "0", "label": "BlackHole 2ch"},
        {"id": "1", "label": "USB Audio Interface"},
    ]


def test_stale_device_identity_never_opens_capture(monkeypatch):
    monkeypatch.setattr(
        inputs, "audio_devices", lambda: [{"id": "0", "label": "New device"}]
    )
    spawn = Mock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(inputs.subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="changed"):
        inputs.MacInputs(Mock()).start("0", "Old device")
    spawn.assert_not_called()


@pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="FFmpeg required for acquisition integration"
)
def test_buffered_acquisition_release_and_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(
        inputs, "audio_devices", lambda: [{"id": "0", "label": "Synthetic test"}]
    )
    monkeypatch.setattr(
        inputs,
        "capture_input_command",
        lambda _: [
            shutil.which("ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
        ],
    )
    manager = inputs.MacInputs(LiveManager())
    opened = manager.start("0", "Synthetic test")
    state = manager.current
    try:
        with pytest.raises(ValueError, match="already open"):
            manager.start("0", "Synthetic test")
        deadline = time.monotonic() + 12
        while (
            not manager.status(opened["id"])["chunks"] and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        status = manager.status(opened["id"])
        assert status["state"] == "receiving", status
        chunk = status["chunks"][-1]
        assert chunk["sample_rate"] == 44100
        assert chunk["channels"] == 1
        assert chunk["rms_dbfs"] < 0
        assert manager.chunk(opened["id"], chunk["sequence"])[:4] == b"RIFF"
        with manager.lock:
            state["expires"] = time.monotonic() - 1
        deadline = time.monotonic() + 5
        while manager.current is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert manager.current is None
        assert state["process"].poll() is not None
        assert not __import__("pathlib").Path(state["folder"].name).exists()
        with pytest.raises(ValueError, match="expired"):
            manager.chunk(opened["id"], 0)
    finally:
        manager.close()
