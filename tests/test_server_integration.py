from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from oida.server import create_app


def _client() -> TestClient:
    # base_url sets a loopback Host header so the loopback guard admits the request.
    # No context manager: avoid firing the shutdown handler against the real repo.
    return TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")


def _write_tone(path: Path) -> Path:
    sample_rate = 16_000
    t = np.arange(sample_rate) / sample_rate
    sf.write(path, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), sample_rate)
    return path


class ServerSecurityTests(unittest.TestCase):
    def test_health_reports_hmm(self) -> None:
        response = _client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "oida")

    def test_cross_origin_request_is_refused(self) -> None:
        response = _client().get("/health", headers={"origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_same_origin_request_is_allowed(self) -> None:
        response = _client().get("/health", headers={"origin": "http://127.0.0.1:8765"})
        self.assertEqual(response.status_code, 200)

    def test_null_origin_is_refused(self) -> None:
        response = _client().get("/health", headers={"origin": "null"})
        self.assertEqual(response.status_code, 403)

    def test_listen_event_missing_path_returns_400(self) -> None:
        response = _client().post("/listen-event", json={"path": "/no/such/file.wav"})
        self.assertEqual(response.status_code, 400)

    def test_path_endpoints_missing_path_return_400(self) -> None:
        client = _client()
        for endpoint, payload in [
            ("/events", {"path": "/no/such/file.wav"}),
            ("/speech", {"path": "/no/such/file.wav"}),
            ("/music", {"path": "/no/such/file.wav"}),
            ("/qa", {"path": "/no/such/file.wav", "question": "What is here?"}),
            ("/think", {"path": "/no/such/file.wav", "instruction": "Describe it."}),
        ]:
            with self.subTest(endpoint=endpoint):
                response = client.post(endpoint, json=payload)
                self.assertEqual(response.status_code, 400)

    def test_wildcard_bind_requires_bearer_token(self) -> None:
        with patch.dict("os.environ", {"HMM_AUTH_TOKEN": "", "AEAR_AUTH_TOKEN": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                create_app(profile="stub", host="0.0.0.0")

        with patch.dict("os.environ", {"HMM_AUTH_TOKEN": "secret"}, clear=False):
            client = TestClient(create_app(profile="stub", host="0.0.0.0"), base_url="http://127.0.0.1")
            self.assertEqual(client.get("/health").status_code, 401)
            self.assertEqual(client.get("/health", headers={"authorization": "Bearer secret"}).status_code, 200)

    def test_sample_tone_uses_configured_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"HMM_DATA_DIR": tmp}, clear=False):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            response = client.get("/sample-tone")
            body = response.json()
            exists = Path(body["path"]).exists()
            in_data_dir = Path(body["path"]).resolve().is_relative_to(Path(tmp).resolve())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(exists)
        self.assertTrue(in_data_dir)

    def test_raw_audio_wipe_deletes_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"HMM_DATA_DIR": tmp, "HMM_AUDIO_DIR": str(Path(tmp) / "uploads")}, clear=False
        ):
            upload = Path(tmp) / "uploads" / "old.wav"
            upload.parent.mkdir(parents=True)
            upload.write_bytes(b"raw")
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            status = client.get("/raw-audio/status")
            wipe = client.post("/raw-audio/wipe", json={"delete_all": True})
            exists_after = upload.exists()

        self.assertEqual(status.json()["file_count"], 1)
        self.assertEqual(wipe.json()["deleted_count"], 1)
        self.assertFalse(exists_after)

    def test_raw_audio_wipe_covers_legacy_uploads_only_on_request(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as legacy_tmp,
            patch.dict(
                "os.environ", {"HMM_DATA_DIR": tmp, "HMM_AUDIO_DIR": str(Path(tmp) / "uploads")}, clear=False
            ),
        ):
            upload = Path(tmp) / "uploads" / "new.wav"
            upload.parent.mkdir(parents=True)
            upload.write_bytes(b"raw")
            legacy_dir = Path(legacy_tmp) / "uploads"
            legacy_dir.mkdir(parents=True)
            legacy_file = legacy_dir / "june-recording.wav"
            legacy_file.write_bytes(b"raw")
            with patch("oida.raw_audio.legacy_uploads_dir", return_value=legacy_dir):
                client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
                status = client.get("/raw-audio/status").json()
                default_wipe = client.post("/raw-audio/wipe", json={"delete_all": True}).json()
                legacy_survives_default = legacy_file.exists()
                legacy_wipe = client.post(
                    "/raw-audio/wipe", json={"delete_all": True, "include_legacy": True}
                ).json()
                legacy_exists_after = legacy_file.exists()

        self.assertEqual(status["legacy_directory"], str(legacy_dir))
        self.assertEqual(status["legacy_file_count"], 1)
        self.assertEqual(default_wipe["deleted_count"], 1)
        self.assertTrue(legacy_survives_default)
        self.assertEqual(legacy_wipe["deleted_count"], 1)
        self.assertFalse(legacy_exists_after)

    def test_report_runs_on_stub_for_real_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_tone(Path(tmp) / "tone.wav")
            response = _client().post("/report", json={"path": str(path)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "0.1")

    def test_qa_forbidden_topic_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_tone(Path(tmp) / "tone.wav")
            response = _client().post("/qa", json={"path": str(path), "question": "what is happening above 8 kHz?"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["forbidden_topics_triggered"])
        self.assertEqual(body["qa"]["answer"], "")


if __name__ == "__main__":
    unittest.main()
