"""HTTP selection against configured checkpoints outside the scanned tree.

Empty passes and a stub engine test admission only, never model inference.
"""

import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from oida.engine_stub import StubMossEngine
from oida.reasoning.audio_selection import selector
from oida.reasoning.model_catalog import find_model_spec
from oida.server import create_app


@pytest.mark.parametrize(
    "requested, expected",
    [
        ("instruct", 200),
        ("OpenMOSS-Team/MOSS-Audio-4B-Instruct", 200),
        ("OpenMOSS-Team/MOSS-Audio-4B-Thinking", 400),
    ],
)
@pytest.mark.parametrize("profile", ["mac-mps", "stub"])
@pytest.mark.parametrize("installed", [True, False])
def test_http_resolves_explicitly_configured_external_checkpoint(
    tmp_path, monkeypatch, requested, expected, profile, installed
):
    import os

    for key in list(os.environ):
        if key.startswith(("OIDA_", "HMM_", "AEAR_", "LISTENINGSTACK_WORKSPACE_")):
            monkeypatch.delenv(key)
    checkpoint = tmp_path / "external" / "MOSS-Audio-4B-Instruct"
    checkpoint.mkdir(parents=True)
    if installed:
        (checkpoint / "config.json").write_text(json.dumps({"fixture": True}))
    expected = expected if profile == "mac-mps" and installed else 400
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("OIDA_TRIAL_DIR", str(tmp_path / "trial"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    monkeypatch.setenv("OIDA_MOSS_AUDIO_REPO", str(tmp_path / "no-scanned-models"))
    monkeypatch.setenv("OIDA_MOSS_INSTRUCT_MODEL", str(checkpoint))
    monkeypatch.setenv("OIDA_MOSS_THINKING_MODEL", str(tmp_path / "missing-thinking"))
    monkeypatch.setenv("OIDA_MOSS_PREWARM", "0")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr("oida.server.build_engine", lambda config: StubMossEngine())
    monkeypatch.setattr("oida.server.scan_moss_models", lambda root, configured=(): [])
    audio = tmp_path / "fixture.wav"
    sf.write(audio, np.zeros(16000, dtype=np.float32), 16000)
    client = TestClient(create_app(profile=profile), base_url="http://127.0.0.1")
    options = client.get("/listening/options").json()
    option = next(
        row
        for row in options["models"]
        if row.get("audio_model", {}).get("model_id") == requested
    )
    assert option["available"] == (expected == 200)
    if requested == "OpenMOSS-Team/MOSS-Audio-4B-Instruct":
        assert option["installed"] is installed
        assert option["name"].endswith(
            "registered ID / local checkpoint" if installed else "pinned Hugging Face revision"
        )
        assert option["inference_tested"] is False
        if expected == 200:
            assert option["qualification_level"] == "local_runtime_detected"
    result = client.post(
        "/gateway/listen",
        json={
            "path": str(audio),
            "passes": [],
            "remember": False,
            "privacy_mode": "incognito",
            "raw_audio_policy": "not_stored",
            "audio_model": selector(
                find_model_spec("oida_moss", requested)
            ).model_dump(),
        },
    )
    client.close()
    assert result.status_code == expected, result.text
