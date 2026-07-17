from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from oida.server import create_app


_AMBIENT_ENV: dict[str, str] = {}


def setUpModule() -> None:  # noqa: N802 (unittest hook)
    # A developer's ambient oída configuration (OIDA_DATA_DIR, OIDA_AUTH_TOKEN, …)
    # must not leak into these tests: OIDA_* is read before the HMM_*/AEAR_*
    # names the tests patch.
    for name in list(os.environ):
        if name.startswith(("OIDA_", "HMM_", "AEAR_")):
            _AMBIENT_ENV[name] = os.environ.pop(name)


def tearDownModule() -> None:  # noqa: N802 (unittest hook)
    os.environ.update(_AMBIENT_ENV)
    _AMBIENT_ENV.clear()


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
    def test_service_discovery_and_shared_favicon_follow_mounted_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "HMM_DATA_DIR": str(Path(tmp) / "oida"),
                "HMM_AUDIO_DIR": str(Path(tmp) / "audio"),
                "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
            },
            clear=False,
        ):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            discovery = client.get("/api")
            favicon = client.get("/favicon.ico")
            schema = client.get("/openapi.json").json()

        self.assertEqual(discovery.status_code, 200)
        endpoints = discovery.json()["endpoints"]
        self.assertEqual(endpoints, sorted(set(endpoints)))
        self.assertTrue({"/covenant", "/remote", "/sessions"}.issubset(endpoints))
        self.assertEqual(favicon.status_code, 200)
        self.assertEqual(favicon.headers["content-type"], "image/svg+xml")
        self.assertIn("text/html", schema["paths"]["/"]["get"]["responses"]["200"]["content"])
        self.assertIn(
            "text/event-stream",
            schema["paths"]["/conversation/ask/stream"]["post"]["responses"]["200"]["content"],
        )
        error_responses = schema["paths"]["/sessions/{session_id}"]["delete"]["responses"]
        self.assertTrue({"400", "404", "422", "503"}.issubset(error_responses))

    def test_json_boolean_fields_reject_integer_coercion(self) -> None:
        cases = [
            ("/akouo/route", {"path": "unused.wav", "validate": 0}),
            ("/background/history/batch-pin", {"event_ids": [], "pinned": 0}),
            ("/background/history/clear", {"keep_pinned": 0}),
            ("/native/system-audio/cleanup", {"dry_run": 0}),
            ("/raw-audio/wipe", {"include_legacy": 0}),
        ]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "HMM_DATA_DIR": str(Path(tmp) / "oida"),
                "HMM_AUDIO_DIR": str(Path(tmp) / "audio"),
                "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
            },
            clear=False,
        ):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            for endpoint, payload in cases:
                with self.subTest(endpoint=endpoint):
                    response = client.post(endpoint, json=payload)
                    self.assertEqual(response.status_code, 422, response.text)

    def test_germ_handoff_rejects_malformed_payload_before_processing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "HMM_DATA_DIR": str(Path(tmp) / "oida"),
                "HMM_AUDIO_DIR": str(Path(tmp) / "audio"),
                "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
            },
            clear=False,
        ):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            response = client.post(
                "/germ/handoff",
                json={"audio": {}, "mode": "invalid", "location": {"source": {}}},
            )

        self.assertEqual(response.status_code, 422, response.text)

    def test_embedded_memory_can_be_renamed_and_forgotten_without_deleting_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "HMM_DATA_DIR": str(Path(tmp) / "oida"),
                "HMM_AUDIO_DIR": str(Path(tmp) / "audio"),
                "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
            },
            clear=False,
        ):
            import akousma
            from oida.akousma_bridge import build_akousma_from_listen

            record = build_akousma_from_listen(
                audio={"asset_id": "asset_memory_menu", "type": "capture"},
                listening={"oida.listen": {"event_id": "evt_memory_menu", "summary": "Original"}},
                session_id="session_memory_menu",
                summary="Original memory name",
            )
            store = akousma.AkousmataStore(Path(tmp) / "akousmata")
            try:
                memory_id = store.put(record)
            finally:
                store.close()

            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            listed = client.get("/akousmata/records")
            renamed = client.patch(f"/akousmata/records/{memory_id}", json={"summary": "Renamed memory"})
            forgotten = client.delete(f"/akousmata/records/{memory_id}")
            missing = client.get(f"/akousmata/records/{memory_id}")

        card = next(item for item in listed.json()["records"] if item["akousma_id"] == memory_id)
        self.assertEqual(card["session_id"], "session_memory_menu")
        self.assertEqual(card["event_id"], "evt_memory_menu")
        self.assertEqual(renamed.json()["record"]["summary"], "Renamed memory")
        self.assertTrue(forgotten.json()["forgotten"])
        self.assertFalse(forgotten.json()["audio_deleted"])
        self.assertEqual(missing.status_code, 404)

    def test_health_reports_oida(self) -> None:
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

    def test_report_rejects_directory_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = _client().post("/report", json={"path": tmp})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a file", response.json()["detail"])

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

    def test_listen_event_preserves_microphone_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"HMM_DATA_DIR": tmp, "HMM_AUDIO_DIR": str(Path(tmp) / "audio")},
            clear=False,
        ):
            path = _write_tone(Path(tmp) / "mic.wav")
            response = TestClient(
                create_app(profile="stub"), base_url="http://127.0.0.1"
            ).post(
                "/listen-event",
                json={
                    "path": str(path),
                    "source_type": "live_input",
                    "source_label": "Microphone · USB interface",
                    "device_id": "input-1",
                    "privacy_mode": "ephemeral",
                    "raw_audio_policy": "temp",
                },
            )

        event = response.json()["listening_event"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(event["source"]["type"], "live_input")
        self.assertEqual(event["source"]["device_id"], "input-1")
        self.assertEqual(event["raw_audio_policy"], "temp")
        self.assertTrue(event["segment"]["ephemeral"])

    def test_music_id_is_opt_in_and_gated_to_music_mode(self) -> None:
        match = {
            "provider": "shazamio",
            "matched": True,
            "title": "Test Track",
            "artist": "Test Artist",
            "checked_at": "2026-07-13T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"HMM_DATA_DIR": tmp, "HMM_AUDIO_DIR": str(Path(tmp) / "audio")},
            clear=False,
        ), patch("oida.server.identify_song", return_value=match) as identify:
            path = _write_tone(Path(tmp) / "music.wav")
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            music = client.post(
                "/listen-event",
                json={"path": str(path), "route_preset": "music", "song_id": True},
            )
            general = client.post(
                "/listen-event",
                json={"path": str(path), "route_preset": "basic", "song_id": True},
            )

        self.assertEqual(music.status_code, 200)
        self.assertEqual(music.json()["listening_event"]["music_id"]["title"], "Test Track")
        self.assertIn("music-id", music.json()["listening_event"]["tags"])
        self.assertNotIn("music_id", general.json()["listening_event"])
        identify.assert_called_once_with(path.resolve(), enabled=True)

    def test_listen_event_carries_capture_and_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"HMM_DATA_DIR": tmp, "HMM_AUDIO_DIR": str(Path(tmp) / "audio")},
            clear=False,
        ):
            path = _write_tone(Path(tmp) / "walk.wav")
            response = TestClient(
                create_app(profile="stub"), base_url="http://127.0.0.1"
            ).post(
                "/listen-event",
                json={
                    "path": str(path),
                    "source_type": "live_input",
                    "capture_direction": "past",
                    "capture_seconds": 30,
                    "capture_trigger": "floating-listener",
                    "location": {"lat": 6.2442, "lon": -75.5812, "accuracy_m": 12, "source": "gps"},
                },
            )

        self.assertEqual(response.status_code, 200)
        event = response.json()["listening_event"]
        self.assertEqual(event["capture"]["direction"], "past")
        self.assertEqual(event["capture"]["seconds"], 30)
        self.assertEqual(event["capture"]["trigger"], "floating-listener")
        self.assertIn("triggered_at", event["capture"])
        self.assertEqual(event["location"]["lat"], 6.2442)
        self.assertEqual(event["segment"]["metadata"]["capture"]["direction"], "past")
        self.assertEqual(event["segment"]["metadata"]["location"]["lon"], -75.5812)

    def test_session_and_capture_contracts_drive_the_shared_listener_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"HMM_DATA_DIR": tmp, "HMM_AUDIO_DIR": str(Path(tmp) / "audio")},
            clear=False,
        ):
            path = _write_tone(Path(tmp) / "session.wav")
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            created = client.post("/sessions", json={"name": "Street session"})
            listened = client.post(
                "/listen-event",
                json={
                    "path": str(path),
                    "source_type": "live_input",
                    "capture_direction": "future",
                    "capture_seconds": 10,
                    "capture_trigger": "dashboard",
                },
            )
            renamed = client.patch(
                f"/sessions/{created.json()['session']['id']}/events/{listened.json()['listening_event']['id']}",
                json={"title": "Edited street listen"},
            )
            sessions = client.get("/sessions")
            archived = client.post(f"/sessions/{created.json()['session']['id']}/archive")
            restored = client.post(f"/sessions/{created.json()['session']['id']}/restore")
            deleted_event = client.delete(
                f"/sessions/{created.json()['session']['id']}/events/{listened.json()['listening_event']['id']}"
            )
            disposable = client.post("/sessions", json={"name": "Disposable session"})
            disposable_session_id = disposable.json()["session"]["id"]
            deleted_session = client.delete(f"/sessions/{disposable_session_id}")
            capture = client.post(
                "/background/capture-request",
                json={
                    "seconds": 10,
                    "route_preset": "music",
                    "direction": "future",
                    "source": "mic",
                    "enabled_skill_ids": ["musicological-listener"],
                    "song_id": True,
                },
            )

        self.assertEqual(created.status_code, 200)
        session_id = created.json()["session"]["id"]
        self.assertEqual(listened.status_code, 200)
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["listening_event"]["aggregate"]["title"], "Edited street listen")
        self.assertEqual(listened.json()["listening_event"]["session"]["id"], session_id)
        self.assertEqual(sessions.json()["sessions"][0]["event_count"], 1)
        self.assertEqual(sessions.json()["sessions"][0]["events"][0]["aggregate"]["title"], "Edited street listen")
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json()["archived_sessions"][0]["event_count"], 1)
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(deleted_event.json()["deleted"])
        self.assertTrue(deleted_session.json()["deleted"])
        self.assertNotIn(disposable_session_id, {session["id"] for session in deleted_session.json()["sessions"]})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["archived_sessions"], [])
        self.assertEqual(capture.json()["capture_request"]["direction"], "future")
        self.assertEqual(capture.json()["capture_request"]["source"], "mic")
        self.assertEqual(capture.json()["capture_request"]["enabled_skill_ids"], ["musicological-listener"])
        self.assertTrue(capture.json()["capture_request"]["song_id"])

    def test_listen_event_rejects_bad_capture_and_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_tone(Path(tmp) / "tone.wav")
            client = _client()
            sideways = client.post(
                "/listen-event", json={"path": str(path), "capture_direction": "sideways"}
            )
            off_planet = client.post(
                "/listen-event", json={"path": str(path), "location": {"lat": 123, "lon": 0}}
            )
        self.assertEqual(sideways.status_code, 400)
        self.assertIn("capture_direction", sideways.json()["detail"])
        self.assertEqual(off_planet.status_code, 400)
        self.assertIn("location.lat", off_planet.json()["detail"])

    def test_remote_ear_page_and_listen_flow(self) -> None:
        import io
        import wave as wave_module

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "HMM_DATA_DIR": str(Path(tmp) / "oida"),
                "HMM_AUDIO_DIR": str(Path(tmp) / "audio"),
                "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
            },
            clear=False,
        ):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            page = client.get("/remote")
            self.assertEqual(page.status_code, 200)
            self.assertIn("remote ear", page.text)

            buffer = io.BytesIO()
            with wave_module.open(buffer, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16_000)
                handle.writeframes(b"\x00\x01" * 16_000)
            response = client.post(
                "/remote/listen",
                files={"file": ("remote-capture.wav", buffer.getvalue(), "audio/wav")},
                data={
                    "direction": "past",
                    "seconds": "30",
                    "lat": "6.2442",
                    "lon": "-75.5812",
                    "accuracy_m": "9",
                    "location_label": "río Medellín",
                    "tags": "night,walk",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            remote = body["remote"]
            self.assertEqual(remote["direction"], "past")
            self.assertNotIn("akousma_error", remote)
            self.assertIn("akousma_id", remote)
            self.assertTrue(str(remote["audio_uri"]).startswith("akousmata://objects/"))
            self.assertEqual(body["listening_event"]["location"]["label"], "río Medellín")

            import akousma

            store = akousma.AkousmataStore(Path(tmp) / "akousmata")
            try:
                record = store.get(remote["akousma_id"])
                self.assertIsNotNone(record)
                self.assertEqual(record["location"]["lat"], 6.2442)
                self.assertEqual(record["capture"]["direction"], "past")
                self.assertEqual(record["capture"]["trigger"], "remote-ear")
                self.assertIn("remote-ear", record["tags"])
                self.assertEqual(record["provenance"]["origin"], "live-input")
                audio_path = store.resolve_uri(record["audio"]["uri"])
                self.assertTrue(audio_path is not None and audio_path.exists())
            finally:
                store.close()

    def test_generation_relisten_honors_signal_preset_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"HMM_DATA_DIR": tmp, "HMM_AUDIO_DIR": str(Path(tmp) / "audio")},
            clear=False,
        ):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            source_event = {
                "id": "evt_source",
                "source": {"type": "file", "label": "source.wav"},
                "segment": {"duration_ms": 1000, "data_ref": {"kind": "path", "uri": "source.wav"}},
                "aggregate": {"title": "Source", "short_summary": "A source sound."},
                "routes": [],
                "features": {},
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            }
            generation = client.post("/generation/prompt", json={"event": source_event}).json()
            output = _write_tone(Path(tmp) / "generated.wav")
            response = client.post(
                "/generation/relisten",
                json={
                    "generation_id": generation["id"],
                    "path": str(output),
                    "route_preset": "signal",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["perception_report"]["moss_passes"], [])

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
