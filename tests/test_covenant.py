"""The sovereignty layer: covenant parsing, the four gates, and the wire."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import akousma
import soundfile as sf
from fastapi.testclient import TestClient

from oida.covenant import CovenantEngine, CovenantStore, parse_covenant
from oida.server import create_app

RIVER_COVENANT = """# river covenant
covenant: river-covenant/2
extends: algophonya/v7

## rules
- do not listen: system output
- do not reveal: transcript, speaker identity
- do not retain: raw audio
- coarsen: location to 1 km
- max window: 30 s
- sing to the river every equinox

## commitments
- the river is a neighbor, not a resource

## because
computation is fast; care must be faster.
"""

_AMBIENT_ENV: dict[str, str] = {}


def setUpModule() -> None:  # noqa: N802 (unittest hook)
    for name in list(os.environ):
        if name.startswith(("OIDA_", "HMM_", "AEAR_")):
            _AMBIENT_ENV[name] = os.environ.pop(name)


def tearDownModule() -> None:  # noqa: N802 (unittest hook)
    os.environ.update(_AMBIENT_ENV)
    _AMBIENT_ENV.clear()


def _write_tone(path: Path) -> Path:
    sample_rate = 16_000
    t = np.arange(sample_rate) / sample_rate
    sf.write(path, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), sample_rate)
    return path


class CovenantParserTests(unittest.TestCase):
    def test_rules_and_commitments_separate(self):
        cov = parse_covenant(RIVER_COVENANT)
        self.assertEqual(cov.id, "river-covenant/2")
        self.assertEqual(cov.version, "2")
        self.assertEqual(cov.extends, ["algophonya/v7"])
        verbs = sorted({rule["verb"] for rule in cov.rules})
        self.assertEqual(verbs, ["coarsen", "do_not_listen", "do_not_retain", "do_not_reveal", "max_window"])
        # the unexecutable line moved to commitments — the bridge, not the cage
        self.assertIn("sing to the river every equinox", cov.commitments)
        self.assertIn("the river is a neighbor, not a resource", cov.commitments)
        self.assertIn("care must be faster", cov.because)

    def test_bilingual_verbs(self):
        cov = parse_covenant("## reglas\n- no escuchar: sistema\n- ignorar: música\n- no revelar: transcripción\n")
        verbs = sorted({rule["verb"] for rule in cov.rules})
        self.assertEqual(verbs, ["do_not_listen", "do_not_reveal", "ignore"])

    def test_bare_text_is_all_commitments(self):
        cov = parse_covenant("listen like a guest\nleave no trace you were not given\n")
        self.assertEqual(cov.rules, [])
        self.assertEqual(len(cov.commitments), 2)

    def test_numeric_rules_reject_unbounded_numbers(self):
        cov = parse_covenant("## rules\n- coarsen: location to " + ("0" * 100_000) + " km\n")
        self.assertEqual(cov.rules, [])

    def test_reference_carries_identity_not_content(self):
        cov = parse_covenant(RIVER_COVENANT)
        ref = cov.reference()
        self.assertEqual(ref["id"], "river-covenant/2")
        self.assertEqual(ref["commitments"], 2)
        self.assertNotIn("rules", ref)
        self.assertNotIn("because", ref)


class CovenantEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = CovenantEngine(parse_covenant(RIVER_COVENANT))

    def test_source_gate(self):
        self.assertIsNotNone(self.engine.refuse_source("system_output"))
        self.assertIsNone(self.engine.refuse_source("live_input"))

    def test_pass_filtering_never_computes_withheld_aspects(self):
        passes, applied = self.engine.filter_passes(["transcribe", "caption", "speech", "music", "events"])
        self.assertNotIn("transcribe", passes)
        self.assertNotIn("speech", passes)
        self.assertIn("caption", passes)
        self.assertIn("do_not_reveal:transcript", applied)

    def test_perception_redaction_is_attributed(self):
        perception = {"transcript": {"present": True, "segments": ["hello"]}, "music": {"present": True}}
        redacted, withheld = self.engine.redact_perception(perception)
        self.assertTrue(redacted["transcript"]["withheld"])
        self.assertFalse(redacted["transcript"]["present"])
        subjects = {item["subject"] for item in withheld}
        self.assertIn("transcript", subjects)

    def test_location_coarsening_raises_accuracy_floor(self):
        loc, withheld = self.engine.apply_location({"lat": 6.24421, "lon": -75.58123, "accuracy_m": 10.0})
        self.assertGreaterEqual(loc["accuracy_m"], 1000.0)
        self.assertNotEqual(loc["lat"], 6.24421)
        self.assertEqual(withheld[0]["rule"], "coarsen")

    def test_quiet_hours_wrap_midnight(self):
        import time as _time

        engine = CovenantEngine(parse_covenant("## rules\n- quiet hours: 22:00-06:00\n"))
        inside = _time.struct_time((2026, 7, 12, 23, 30, 0, 0, 0, -1))
        outside = _time.struct_time((2026, 7, 12, 12, 0, 0, 0, 0, -1))
        self.assertIsNotNone(engine.refuse_quiet_hours(now=inside))
        self.assertIsNone(engine.refuse_quiet_hours(now=outside))


class CovenantStoreTests(unittest.TestCase):
    def test_save_activate_deactivate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CovenantStore(Path(tmp))
            self.assertEqual(store.list(), [])
            self.assertIsNone(store.engine())
            store.save("river", RIVER_COVENANT)
            self.assertEqual(store.list(), ["river"])
            self.assertIsNone(store.engine())  # saved is not active: opted into
            store.activate("river")
            engine = store.engine()
            self.assertIsNotNone(engine)
            self.assertEqual(engine.covenant.id, "river-covenant/2")
            store.activate(None)
            self.assertIsNone(store.engine())
            self.assertTrue(store.delete("river"))
            self.assertEqual(store.list(), [])


class CovenantEndpointTests(unittest.TestCase):
    def _client_env(self, tmp: str) -> dict[str, str]:
        return {
            "HMM_DATA_DIR": str(Path(tmp) / "oida"),
            "HMM_AUDIO_DIR": str(Path(tmp) / "audio"),
            "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
            "AKOUSMATA_WATCHER": "0",
        }

    def test_covenant_rest_lifecycle_and_listen_gates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self._client_env(tmp), clear=False):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")

            status = client.get("/covenant").json()
            self.assertIsNone(status["active"])

            saved = client.put("/covenant", json={"name": "river", "text": RIVER_COVENANT, "activate": True})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["active"], "river")
            parsed = saved.json()["parsed"]
            self.assertIn("sing to the river every equinox", parsed["commitments"])

            path = _write_tone(Path(tmp) / "tone.wav")

            # source gate: the covenant refuses system output before perception
            refused = client.post(
                "/listen-event", json={"path": str(path), "source_type": "system_output"}
            )
            self.assertEqual(refused.status_code, 423)
            self.assertIn("river-covenant/2", refused.json()["detail"])

            # allowed source: event carries the covenant block; window clamped
            response = client.post(
                "/listen-event",
                json={
                    "path": str(path),
                    "source_type": "live_input",
                    "capture_direction": "past",
                    "capture_seconds": 120,
                    "location": {"lat": 6.24421, "lon": -75.58123, "source": "gps"},
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            event = response.json()["listening_event"]
            block = event["covenant"]
            self.assertEqual(block["id"], "river-covenant/2")
            self.assertEqual(block["commitments"], 2)
            self.assertIn("max_window:30", block["rules_applied"])
            self.assertEqual(event["capture"]["seconds"], 30.0)
            self.assertGreaterEqual(event["location"]["accuracy_m"], 1000.0)
            subjects = {item["subject"] for item in block.get("withheld", [])}
            self.assertIn("location", subjects)

            # deactivate: the layer is empty again
            client.post("/covenant/activate", json={"name": None})
            clean = client.post(
                "/listen-event", json={"path": str(path), "source_type": "system_output"}
            )
            self.assertEqual(clean.status_code, 200)
            self.assertNotIn("covenant", clean.json()["listening_event"])

    def test_max_window_refuses_an_unsliced_file_before_perception(self):
        short_window = "# short window\n## rules\n- max window: 0.1 s\n"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self._client_env(tmp), clear=False):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            client.put(
                "/covenant",
                json={"name": "short-window", "text": short_window, "activate": True},
            )
            path = _write_tone(Path(tmp) / "one-second.wav")
            with patch("oida.server.report") as report:
                response = client.post(
                    "/listen-event",
                    json={"path": str(path), "source_type": "file"},
                )

            self.assertEqual(response.status_code, 423, response.text)
            self.assertIn("already be bounded to 0.1 seconds", response.json()["detail"])
            report.assert_not_called()

    def test_gateway_refusal_returns_and_optionally_retains_a_decision_only_record(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self._client_env(tmp), clear=False):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            client.put("/covenant", json={"name": "river", "text": RIVER_COVENANT, "activate": True})
            path = _write_tone(Path(tmp) / "tone.wav")

            ephemeral = client.post(
                "/gateway/listen",
                json={"path": str(path), "source_type": "system_output", "remember": False},
            )
            self.assertEqual(ephemeral.status_code, 200, ephemeral.text)
            first = ephemeral.json()
            self.assertEqual(first["status"], "complete")
            self.assertEqual(first["outcome"], "refused")
            self.assertIsNone(first["listening_event"])
            self.assertIsNone(first["perception_report"])
            self.assertEqual(first["route_outcome"]["memory"]["status"], "not_requested")
            self.assertNotIn("audio", first["decision_record"])
            self.assertEqual(akousma.validation_errors(first["decision_record"]), [])

            retained = client.post(
                "/gateway/listen",
                json={"path": str(path), "source_type": "system_output", "remember": True},
            )
            self.assertEqual(retained.status_code, 200, retained.text)
            second = retained.json()
            self.assertEqual(second["route_outcome"]["memory"]["status"], "retained")
            akousma_id = second["route_outcome"]["memory"]["akousma_id"]
            self.assertEqual(akousma_id, second["decision_record"]["akousma_id"])
            store = akousma.AkousmataStore(Path(tmp) / "akousmata")
            try:
                self.assertEqual(store.get(akousma_id)["auditum"]["contract"], "earworm/auditum/v2")
            finally:
                store.close()

    def test_gateway_listen_memory_retention_gate(self):
        memory_covenant = "# quiet house\n## rules\n- do not retain: memory\n"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self._client_env(tmp), clear=False):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            client.put("/covenant", json={"name": "quiet-house", "text": memory_covenant, "activate": True})
            path = _write_tone(Path(tmp) / "tone.wav")
            result = client.post(
                "/gateway/listen",
                json={"path": str(path), "source_type": "live_input", "remember": True},
            ).json()
            self.assertIsNone(result["trace"])
            withheld = result["listening_event"]["covenant"]["withheld"]
            self.assertIn(
                {"rule": "do_not_retain", "subject": "memory", "count": 1}, withheld
            )

    def test_route_rerun_is_a_new_covenant_governed_hearing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self._client_env(tmp), clear=False):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            path = _write_tone(Path(tmp) / "tone.wav")
            original = client.post(
                "/listen-event",
                json={"path": str(path), "route_preset": "basic"},
            ).json()["listening_event"]
            client.put(
                "/covenant",
                json={
                    "name": "no-speech",
                    "text": "# no speech\n## rules\n- ignore: speech\n",
                    "activate": True,
                },
            )
            client.put(
                "/listening",
                json={"text": "Attend closely to every voice and quote it whenever possible."},
            )

            response = client.post(
                "/listen-event/rerun",
                json={"event": original, "route_preset": "voice"},
            )

            self.assertEqual(response.status_code, 200, response.text)
            rerun = response.json()["listening_event"]
            self.assertIn("covenant", rerun)
            self.assertTrue(
                any(rule.startswith("ignore:speech") for rule in rerun["covenant"]["rules_applied"])
            )
            self.assertFalse(response.json()["perception_report"]["transcript"]["present"])
            self.assertEqual(rerun["listening_identity"]["application"], "not_applied")
            self.assertEqual(rerun["listening_identity"]["applied_to"], [])

    def test_remote_listen_under_covenant_files_covenant_akousma(self):
        import io
        import wave as wave_module

        text = "# walk covenant\ncovenant: walk/1\n## rules\n- coarsen: location to 1 km\n## commitments\n- listen like a guest\n"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self._client_env(tmp), clear=False):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            client.put("/covenant", json={"name": "walk", "text": text, "activate": True})
            buffer = io.BytesIO()
            with wave_module.open(buffer, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16_000)
                handle.writeframes(b"\x00\x01" * 16_000)
            response = client.post(
                "/remote/listen",
                files={"file": ("remote-capture.wav", buffer.getvalue(), "audio/wav")},
                data={"direction": "past", "seconds": "30", "lat": "6.24421", "lon": "-75.58123"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            remote = response.json()["remote"]
            self.assertIn("akousma_id", remote)

            import akousma

            store = akousma.AkousmataStore(Path(tmp) / "akousmata")
            try:
                record = store.get(remote["akousma_id"])
                self.assertEqual(record["covenant"]["id"], "walk/1")
                self.assertEqual(record["covenant"]["commitments"], 1)
                self.assertGreaterEqual(record["location"]["accuracy_m"], 1000.0)
                by_covenant = store.query(covenant_id="walk/1")
                self.assertEqual([r["akousma_id"] for r in by_covenant], [record["akousma_id"]])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
