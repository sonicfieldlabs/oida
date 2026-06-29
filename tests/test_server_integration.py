from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from aear.server import create_app


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
        self.assertEqual(response.json()["name"], "hmm")

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
