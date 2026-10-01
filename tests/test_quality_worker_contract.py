"""Checkpoint-free worker contract; not perceptual or model qualification."""

import json
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from oida.specialists import quality_worker


def test_worker_entrypoint_and_p835_shape(tmp_path, monkeypatch):
    audio = tmp_path / "speech.wav"
    sf.write(audio, np.sin(np.arange(16000, dtype=np.float32) / 10) * 0.1, 16000)
    request = tmp_path / "request.json"
    output = tmp_path / "result.json"
    request.write_text(
        json.dumps(
            {"path": str(audio), "checkpoint": str(tmp_path / "sig_bak_ovr.onnx")}
        )
    )
    calls = []

    class Session:
        def __init__(self, checkpoint, **kwargs):
            assert checkpoint.endswith("sig_bak_ovr.onnx")

        def run(self, _, inputs):
            calls.append(inputs)
            assert inputs["input_1"].shape == (1, 144160)
            return [np.array([[3.0, 4.0, 3.5]])]

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            SessionOptions=lambda: SimpleNamespace(), InferenceSession=Session
        ),
    )
    monkeypatch.setattr(sys, "argv", ["quality_worker", str(request), str(output)])
    quality_worker.main()
    result = json.loads(output.read_text())
    row = result["result"]["hypotheses"][0]
    assert len(calls) == 1 and row["ovrl"] == 3.5
    assert row["start_seconds"] == 0 and row["end_seconds"] == 1
    assert result["peak_memory_mib"] > 0
    assert result["source_sha256"] and result["view_sha256"]
    failed = subprocess.run(
        [
            sys.executable,
            quality_worker.__file__,
            str(tmp_path / "missing"),
            str(output),
        ],
        capture_output=True,
    )
    assert failed.returncode != 0  # main is actually invoked
