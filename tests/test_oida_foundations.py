from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from oida.akouo_skills import akouo_manifest, resolve_route_skill_ids, route_preset, validate_akouo_manifest
from oida.background import BackgroundRuntime
from oida.conversation import ConversationStore
from oida.contracts import audio_segment_from_path, source_for_path, to_dict
from oida.engine_stub import StubMossEngine
from oida.generation import GenerationStore
from oida.listening import listening_event_dict
from oida.live import LiveManager
from oida.memory import AkousmataStore
from oida.native_temp_audio import cleanup_native_system_audio_temp_files, native_system_audio_temp_status
from oida.reporting import report, report_to_dict
from oida.source_routes import native_system_audio_route_manifest, normalize_system_audio_source_route
from oida.sources import source_registry_dict
from oida.system_audio import classify_browser_audio_device, is_loopback_device_label, system_audio_status
from harness.akouo.command import build_harness_output
from harness.akouo.routing import evidence_level_for_report


class OidaFoundationTests(unittest.TestCase):
    def test_audio_segment_contract_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tone(Path(tmp) / "tone.wav", duration_s=0.25)
            segment = audio_segment_from_path(path)

        data = to_dict(segment)
        self.assertEqual(data["source"]["type"], "file")
        self.assertEqual(data["sample_rate"], 16000)
        self.assertGreater(data["duration_ms"], 200)
        self.assertEqual(data["data_ref"]["kind"], "path")

    def test_source_registry_exposes_system_audio_distinctly(self) -> None:
        registry = source_registry_dict()
        sources = {item["id"]: item for item in registry["sources"]}

        self.assertIn("system-output", sources)
        self.assertIn("native-system-output", sources)
        self.assertEqual(sources["system-output"]["type"], "system_output")
        self.assertIn("status", sources["system-output"])
        self.assertIn("capture_strategy", sources["system-output"]["details"])
        self.assertEqual(sources["native-system-output"]["type"], "system_output")
        self.assertIn("routes", sources["native-system-output"]["details"])

    def test_system_audio_status_documents_loopback_path_on_macos(self) -> None:
        status = system_audio_status("darwin")

        self.assertEqual(status.status, "needs_loopback_device")
        self.assertTrue(status.supported)
        self.assertIn("loopback", status.capture_strategy)
        self.assertEqual(status.details["native_signal_tap_raw_audio_policy"], "not_stored_until_explicit_analysis")
        self.assertEqual(status.details["native_temp_analysis_raw_audio_policy"], "temp")

    def test_browser_device_classification_detects_loopback_labels(self) -> None:
        classified = classify_browser_audio_device("BlackHole 2ch", "device-1")

        self.assertTrue(is_loopback_device_label("BlackHole 2ch"))
        self.assertEqual(classified["source_type"], "system_output")
        self.assertTrue(classified["is_loopback_candidate"])

    def test_akouo_manifest_contains_core_presets(self) -> None:
        manifest = akouo_manifest()
        presets = {preset["id"] for preset in manifest["route_presets"]}
        skills = {skill["id"] for skill in manifest["skills"]}

        self.assertIn("basic", presets)
        self.assertIn("extended-spectrum", presets)
        self.assertIn("signal-health", skills)
        self.assertTrue(manifest["schemas"]["skill_manifest"])
        self.assertEqual(validate_akouo_manifest(), [])
        self.assertEqual(route_preset("basic").akouo_command, "/listen")

    def test_akouo_skill_resolution_supports_overrides(self) -> None:
        self.assertEqual(resolve_route_skill_ids("basic", enabled_skill_ids=["signal-health"]), ["signal-health"])

        with self.assertRaises(ValueError):
            resolve_route_skill_ids("basic", enabled_skill_ids=[])

    def test_live_capture_writes_bounded_buffer_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = write_tone(Path(tmp) / "first.wav", frequency=220, duration_s=0.5)
            second = write_tone(Path(tmp) / "second.wav", frequency=440, duration_s=0.5)
            manager = LiveManager()
            started = manager.start(ring_seconds=2.0, source_type="system_output", source_label="BlackHole 2ch", device_id="loopback-1")
            manager.ingest_saved_upload(started["session_id"], {"path": str(first), "raw_path": str(first), "sha256": "a"})
            manager.ingest_saved_upload(started["session_id"], {"path": str(second), "raw_path": str(second), "sha256": "b"})
            capture = manager.capture_last(started["session_id"], seconds=0.6)
            captured_path = Path(str(capture["path"]))

        self.assertEqual(capture["raw_audio_policy"], "temp")
        self.assertLessEqual(capture["segment"]["duration_ms"], 650)
        self.assertEqual(capture["segment"]["source"]["type"], "system_output")
        self.assertIn("BlackHole", capture["segment"]["source"]["label"])
        self.assertTrue(captured_path.exists())
        captured_path.unlink(missing_ok=True)

    def test_live_signal_snapshot_reports_recent_meter_bands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = write_tone(Path(tmp) / "first.wav", frequency=220, duration_s=0.5)
            second = write_tone(Path(tmp) / "second.wav", frequency=440, duration_s=0.5)
            manager = LiveManager()
            started = manager.start(ring_seconds=2.0, source_type="live_input", source_label="Interface")
            manager.ingest_saved_upload(started["session_id"], {"path": str(first), "raw_path": str(first), "sha256": "a"})
            manager.ingest_saved_upload(started["session_id"], {"path": str(second), "raw_path": str(second), "sha256": "b"})
            snapshot = manager.signal_snapshot(started["session_id"], bands=8)

        self.assertEqual(snapshot["session_id"], started["session_id"])
        self.assertEqual(len(snapshot["bands"]), 8)
        self.assertEqual(len(snapshot["peaks"]), 8)
        self.assertGreater(snapshot["meter"]["rms"], 0)
        self.assertEqual(snapshot["meter"]["basis"], "browser-uploaded-live-chunk-dsp")

    def test_live_manager_evicts_old_stopped_sessions(self) -> None:
        from oida.live import STOPPED_SESSIONS_KEEP

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"HMM_DATA_DIR": tmp}, clear=False):
            manager = LiveManager()
            session_ids = [manager.start()["session_id"] for _ in range(STOPPED_SESSIONS_KEEP + 4)]
            for session_id in session_ids:
                manager.stop(session_id)
            stopped_kept = [session_id for session_id in session_ids if session_id in manager.sessions]

        self.assertEqual(len(stopped_kept), STOPPED_SESSIONS_KEEP)
        self.assertEqual(stopped_kept, session_ids[-STOPPED_SESSIONS_KEEP:])

    def test_listening_event_normalizes_report_and_akouo_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tone(Path(tmp) / "tone.wav")
            report_dict = report_to_dict(report(StubMossEngine(), str(path)))
            command_output = build_harness_output(report_dict, command="/listen")
            event = listening_event_dict(
                report_dict,
                command_output=command_output,
                route_preset_id="basic",
                enabled_skill_ids=["signal-health", "spectral-cartographer"],
            )

        self.assertEqual(event["source"]["type"], "file")
        self.assertEqual([route["route_id"] for route in event["routes"]], ["signal-health", "spectral-cartographer"])
        self.assertEqual(event["routes"][0]["structured"]["ui_card"], "diagnostics")
        self.assertIn("aggregate", event)
        self.assertEqual(event["raw_audio_policy"], "external_ref")

    def test_native_system_audio_event_uses_temp_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tone(Path(tmp) / "system-output.wav")
            report_dict = report_to_dict(report(StubMossEngine(), str(path)))
            command_output = build_harness_output(report_dict, command="/listen")
            source_route = normalize_system_audio_source_route({"capture_scope": "display_mix", "display_id": 1})
            segment = audio_segment_from_path(
                path,
                source=source_for_path(
                    path,
                    source_type="system_output",
                    label="Native system audio / Display system mix",
                    device_id=source_route["route_id"],
                    details={"source_route": source_route},
                ),
                privacy_mode="ephemeral",
                ephemeral=True,
                metadata={
                    "source_adapter": "macos-screencapturekit-system-audio",
                    "raw_audio_policy": "temp",
                    "source_route": source_route,
                },
            )
            event = listening_event_dict(
                report_dict,
                command_output=command_output,
                segment=segment,
                route_preset_id="basic",
                privacy_mode="ephemeral",
                raw_audio_policy="temp",
            )

        self.assertEqual(event["source"]["type"], "system_output")
        self.assertEqual(event["segment"]["metadata"]["raw_audio_policy"], "temp")
        self.assertEqual(event["source"]["details"]["source_route"]["capture_scope"], "display_mix")
        self.assertEqual(event["segment"]["metadata"]["source_route"]["model_input_policy"]["moss_audio"], "16_khz_mono")
        self.assertEqual(event["raw_audio_policy"], "temp")

    def test_listening_event_preserves_live_capture_segment_source_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = write_tone(Path(tmp) / "system-output.wav")
            manager = LiveManager()
            started = manager.start(
                ring_seconds=2.0,
                source_type="system_output",
                source_label="BlackHole 2ch",
                device_id="loopback-1",
            )
            manager.ingest_saved_upload(started["session_id"], {"path": str(source_path), "raw_path": str(source_path), "sha256": "a"})
            capture = manager.capture_last(started["session_id"], seconds=0.5)
            report_dict = report_to_dict(report(StubMossEngine(), str(capture["path"])))
            command_output = build_harness_output(report_dict, command="/listen")
            event = listening_event_dict(
                report_dict,
                command_output=command_output,
                segment=capture["segment"],
                route_preset_id="basic",
                privacy_mode="ephemeral",
                raw_audio_policy="temp",
            )
            Path(str(capture["path"])).unlink(missing_ok=True)

        self.assertEqual(event["source"]["type"], "system_output")
        self.assertIn("BlackHole", event["source"]["label"])
        self.assertEqual(event["source"]["device_id"], "loopback-1")
        self.assertEqual(event["segment"]["metadata"]["live_session_id"], started["session_id"])
        self.assertEqual(event["raw_audio_policy"], "temp")

    def test_native_system_audio_route_manifest_documents_display_mix(self) -> None:
        manifest = native_system_audio_route_manifest("darwin")
        route = manifest["routes"][0]

        self.assertTrue(manifest["supported"])
        self.assertEqual(route["route_id"], "native-display-mix")
        self.assertEqual(route["capture_scope"], "display_mix")
        self.assertEqual(route["model_input_policy"]["moss_audio"], "16_khz_mono")
        self.assertIn("Source route identifies the capture filter", route["claim_limits"][0])

    def test_akousmata_memory_keeps_source_route_metadata(self) -> None:
        source_route = normalize_system_audio_source_route({"capture_scope": "display_mix", "display_id": 9})
        with tempfile.TemporaryDirectory() as tmp:
            store = AkousmataStore(root=Path(tmp) / "akousmata")
            trace = store.remember({
                "id": "evt_route",
                "source": {
                    "type": "system_output",
                    "label": "Native system audio / Display system mix",
                    "details": {"source_route": source_route},
                },
                "segment": {"data_ref": {"kind": "path", "uri": "system.wav"}, "metadata": {"source_route": source_route}},
                "aggregate": {"title": "System mix", "short_summary": "System mix.", "detailed_summary": "System mix."},
                "routes": [{"route_id": "signal-health", "summary": "stable"}],
                "features": {"duration_s": 1.0, "rmsDbfs": -24.0},
                "privacy_mode": "ephemeral",
                "raw_audio_policy": "temp",
            })

        self.assertEqual(trace["sourceKind"], "system")
        self.assertEqual(trace["sourceRouteId"], "native-display-mix")
        self.assertEqual(trace["sourceCaptureScope"], "display_mix")

    def test_akousmata_memory_remember_search_and_forget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AkousmataStore(root=Path(tmp) / "akousmata")
            event = {
                "id": "evt_test",
                "source": {"type": "file", "label": "tone"},
                "segment": {"data_ref": {"kind": "path", "uri": "tone.wav"}},
                "aggregate": {"title": "Machine hum", "short_summary": "A steady hum.", "detailed_summary": "A steady hum."},
                "routes": [{"route_id": "signal-health", "summary": "hum"}],
                "features": {"duration_s": 1.0, "rmsDbfs": -24.0, "spectralCentroidHz": 220.0, "spectralRolloffHz": 440.0},
                "tags": ["hum"],
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            }
            trace = store.remember(event, tags=["machine"])
            matches = store.list("machine hum")
            route_matches = store.list(route="signal-health")
            exported = store.export_json(tag="machine")
            forgotten = store.forget(trace["id"])

        self.assertEqual(matches[0]["id"], trace["id"])
        self.assertEqual(route_matches[0]["id"], trace["id"])
        self.assertEqual(exported["trace_count"], 1)
        self.assertEqual(trace["audioPolicy"]["rawAudioPolicy"], "external_ref")
        self.assertEqual(forgotten["forgotten"], trace["id"])
        self.assertEqual(trace["earworm"]["version"], "0.1.0")
        self.assertEqual(trace["earworm"]["session"]["app_id"], "oida.akousmata")
        self.assertEqual(trace["earworm"]["context_bundle"]["assets"][0]["asset_id"], trace["earworm"]["session"]["assets"][0]["asset_id"])

    def test_akousmata_memory_enriches_events_with_similarity_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AkousmataStore(root=Path(tmp) / "akousmata")
            event = {
                "id": "evt_a",
                "source": {"type": "file", "label": "a"},
                "segment": {"data_ref": {"kind": "path", "uri": "a.wav"}},
                "aggregate": {"title": "Low hum", "short_summary": "Low hum.", "detailed_summary": "Low hum."},
                "routes": [{"route_id": "signal-health", "summary": "stable"}],
                "features": {"duration_s": 1.0, "rmsDbfs": -24.0, "spectralCentroidHz": 220.0},
                "tags": ["hum"],
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            }
            trace = store.remember(event)
            enriched = store.enrich_event({**event, "id": "evt_b"})

        self.assertIn(trace["id"], enriched["memory"]["similar_trace_ids"])
        self.assertEqual(enriched["memory"]["similarity"][0]["basis"], "dsp_feature_similarity")

    def test_akousmata_similarity_requires_multiple_shared_feature_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AkousmataStore(root=Path(tmp) / "akousmata")
            event = {
                "id": "evt_a",
                "source": {"type": "file", "label": "a"},
                "segment": {"data_ref": {"kind": "path", "uri": "a.wav"}},
                "aggregate": {"title": "Short", "short_summary": "Short."},
                "features": {"duration_s": 1.0},
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            }
            store.remember(event)
            similar = store.similar_to_event({**event, "id": "evt_b", "features": {"duration_s": 1.0, "rmsDbfs": -12.0}})

        self.assertEqual(similar, [])

    def test_akousmata_similarity_cache_reflects_external_trace_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AkousmataStore(root=Path(tmp) / "akousmata")
            event = {
                "id": "evt_a",
                "source": {"type": "file", "label": "a"},
                "segment": {"data_ref": {"kind": "path", "uri": "a.wav"}},
                "aggregate": {"title": "Low hum", "short_summary": "Low hum."},
                "features": {"duration_s": 1.0, "rmsDbfs": -24.0, "spectralCentroidHz": 220.0},
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            }
            trace = store.remember(event)
            self.assertEqual(store.list()[0]["title"], "Low hum")
            trace_path = Path(tmp) / "akousmata" / "traces" / f"{trace['id']}.json"
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
            payload["title"] = "Updated hum"
            trace_path.write_text(json.dumps(payload), encoding="utf-8")

            listed = store.list()

        self.assertEqual(listed[0]["title"], "Updated hum")

    def test_conversation_answers_from_event_and_memory_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AkousmataStore(root=Path(tmp) / "akousmata")
            conversations = ConversationStore(root=Path(tmp) / "conversations")
            event = {
                "id": "evt_conversation",
                "source": {"type": "file", "label": "pump.wav"},
                "segment": {"duration_ms": 2500, "data_ref": {"kind": "path", "uri": "pump.wav"}},
                "aggregate": {
                    "title": "Pump hum",
                    "short_summary": "A steady mechanical hum with a slight pulse.",
                    "signal_facts": ["RMS remains stable across the segment."],
                    "hypotheses": [{"statement": "A small motor or pump is active.", "confidence": "medium"}],
                    "warnings": ["No source identity is certain from this event alone."],
                },
                "routes": [{"route_id": "signal-health", "summary": "stable low-frequency energy"}],
                "features": {"duration_s": 2.5, "rmsDbfs": -24.0, "spectralCentroidHz": 220.0},
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            }
            trace = memory.remember({**event, "id": "evt_memory"})
            first = conversations.ask(event=event, question="What is happening in this sound?", memory=memory)
            follow_up = conversations.ask(
                event=event,
                question="Does this match memory?",
                memory=memory,
                conversation_id=first["conversation_id"],
            )
            stored = conversations.get(first["conversation_id"])

        self.assertIn("steady mechanical hum", first["turn"]["answer"])
        self.assertIn("structured listening event", first["turn"]["answer"])
        self.assertFalse(first["turn"]["remote_model"]["enabled"])
        self.assertEqual(first["turn"]["known_facts"][0], "RMS remains stable across the segment.")
        self.assertEqual(follow_up["conversation"]["turn_count"], 2)
        self.assertEqual(follow_up["turn"]["memory_context"][0]["trace_id"], trace["id"])
        self.assertIn("raw audio is not copied", first["raw_audio_policy"])
        self.assertEqual(stored["event_id"], "evt_conversation")

    def test_generation_store_creates_prompt_only_records_and_relisten_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = GenerationStore(root=Path(tmp) / "generations")
            event = {
                "id": "evt_generation",
                "source": {"type": "file", "label": "machine.wav"},
                "segment": {"duration_ms": 4200, "data_ref": {"kind": "path", "uri": "machine.wav"}},
                "aggregate": {
                    "title": "Machine pulse",
                    "short_summary": "A pulsing mechanical sound with a stable low-frequency bed.",
                    "signal_facts": ["RMS is steady while onsets repeat."],
                    "warnings": ["Source identity is not certain."],
                },
                "routes": [{"route_id": "signal-health", "summary": "steady low-frequency energy with repeated onset peaks"}],
                "features": {"duration_s": 4.2, "rmsDbfs": -22.0},
                "tags": ["machine", "pulse"],
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            }

            record = store.create_prompt(event, intent="variation")
            edited = store.create_prompt(
                event,
                prompt="Edited prompt from the operator.",
                adapter="external-generator",
                generate=True,
            )
            attached = store.attach_relisten(
                record["id"],
                output_path="/tmp/generated.wav",
                generated_event={**event, "id": "evt_generated"},
                route_comparison={"version": "0.1", "same_segment": False},
            )
            listed = store.list()

        self.assertEqual(record["status"], "prompt_ready")
        self.assertIn("Machine pulse", record["prompt"])
        self.assertIn("RMS is steady", record["evidence"][1]["value"])
        self.assertIn("raw audio is not copied", record["raw_audio_policy"])
        self.assertEqual(record["params"]["duration_s"], 4.2)
        self.assertEqual(edited["status"], "adapter_required")
        self.assertEqual(edited["adapter"], "external-generator")
        self.assertEqual(attached["status"], "relistened")
        self.assertEqual(attached["output_audio"]["raw_audio_policy"], "external_ref")
        self.assertEqual(attached["relisten"]["listening_event"]["id"], "evt_generated")
        self.assertEqual(len(listed), 2)

    def test_incognito_conversation_and_generation_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conversations = ConversationStore(root=Path(tmp) / "conversations")
            generations = GenerationStore(root=Path(tmp) / "generations")
            memory = AkousmataStore(root=Path(tmp) / "akousmata")
            event = {
                "id": "evt_incognito",
                "source": {"type": "live_input", "label": "Private microphone"},
                "segment": {"duration_ms": 1000, "data_ref": {"kind": "path", "uri": "/tmp/private.wav"}},
                "aggregate": {"title": "Private", "short_summary": "An incognito event."},
                "routes": [],
                "features": {"duration_s": 1.0},
                "privacy_mode": "incognito",
                "raw_audio_policy": "temp",
            }

            conversation = conversations.ask(
                event=event,
                question="What happened?",
                memory=memory,
            )
            generation = generations.create_prompt(event)

            self.assertFalse(conversation["persistent"])
            self.assertFalse(generation["persistent"])
            self.assertEqual(list((Path(tmp) / "conversations").glob("*.json")), [])
            self.assertEqual(generations.list(), [])

    def test_background_runtime_persists_config_and_tracks_live_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "background.json"
            runtime = BackgroundRuntime(config_path=config_path)
            runtime.update_config({"default_capture_seconds": 15.0, "default_route_preset": "signal"})
            runtime.set_active_live_session("live-1")
            runtime.pause()
            restored = BackgroundRuntime(config_path=config_path)

        self.assertEqual(restored.config.default_capture_seconds, 15.0)
        self.assertEqual(restored.config.default_route_preset, "signal")
        self.assertEqual(runtime.status()["state"]["active_live_session_id"], "live-1")
        self.assertTrue(runtime.status()["config"]["paused"])

    def test_background_runtime_normalizes_invalid_config_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "background.json"
            runtime = BackgroundRuntime(config_path=config_path)
            runtime.update_config({
                "default_capture_seconds": -5,
                "default_route_preset": "not-a-route",
                "floating_agent": {"size": "huge", "x": "nan"},
                "recent_history": {"max_events": 999},
                "upload_audio_retention": {"policy": "bad", "max_files": -1, "delete_after_days": float("nan")},
                "paused": "false",
                "native_temp_audio_retention": {"delete_after_analysis": "false"},
            })
            restored = BackgroundRuntime(config_path=config_path)

        self.assertEqual(restored.config.default_capture_seconds, 10.0)
        self.assertEqual(restored.config.default_route_preset, "basic")
        self.assertEqual(restored.config.floating_agent["size"], "compact")
        self.assertIsNone(restored.config.floating_agent["x"])
        self.assertEqual(restored.config.recent_history["max_events"], 50)
        self.assertEqual(restored.config.upload_audio_retention["policy"], "keep")
        self.assertEqual(restored.config.upload_audio_retention["delete_after_days"], 7.0)
        self.assertFalse(restored.config.paused)
        self.assertFalse(restored.config.native_temp_audio_retention["delete_after_analysis"])

    def test_background_capture_request_cancel_is_distinct_from_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = BackgroundRuntime(config_path=Path(tmp) / "background.json")
            request = runtime.request_capture(seconds=4, route_preset="signal")
            cancelled = runtime.cancel_capture_request(request["id"])

        self.assertIsNotNone(cancelled)
        assert cancelled is not None
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(runtime.claim_capture_request(request["id"]))

    def test_akouo_evidence_level_reflects_unavailable_model(self) -> None:
        report = {
            "engine": {"profile": "mac-mps", "unavailable_reason": "no weights"},
            "dsp": {"durationSeconds": 1.0, "sampleRate": 16000, "channelCount": 1, "features": {"rmsDbfs": -20.0}},
            "caption": {"dense": "stub caption"},
            "transcript": {"present": False},
        }

        self.assertEqual(evidence_level_for_report(report), "measured_signal")

    def test_background_runtime_rejects_actions_while_paused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = BackgroundRuntime(config_path=Path(tmp) / "background.json")
            runtime.pause()

            with self.assertRaises(RuntimeError):
                runtime.begin_action("capture")

    def test_background_runtime_merges_floating_agent_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "background.json"
            runtime = BackgroundRuntime(config_path=config_path)
            runtime.update_config({
                "show_floating_agent": False,
                "floating_agent": {"visible": False, "size": "medium", "x": 42, "y": 84},
            })
            restored = BackgroundRuntime(config_path=config_path)

        self.assertFalse(restored.config.show_floating_agent)
        self.assertFalse(restored.config.floating_agent["visible"])
        self.assertEqual(restored.config.floating_agent["size"], "medium")
        self.assertEqual(restored.config.floating_agent["x"], 42)
        self.assertEqual(restored.config.floating_agent["y"], 84)

    def test_background_runtime_persists_native_temp_retention_and_hotkeys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "background.json"
            runtime = BackgroundRuntime(config_path=config_path)
            runtime.update_config({
                "hotkeys": {"capture_last_buffer": "cmd+shift+h"},
                "native_temp_audio_retention": {
                    "policy": "delete_after_days",
                    "delete_after_days": 2,
                    "max_files": 3,
                    "delete_after_analysis": True,
                },
            })
            restored = BackgroundRuntime(config_path=config_path)

        self.assertEqual(restored.config.hotkeys["capture_last_buffer"], "cmd+shift+h")
        self.assertEqual(restored.config.native_temp_audio_retention["policy"], "delete_after_days")
        self.assertEqual(restored.config.native_temp_audio_retention["delete_after_days"], 2.0)
        self.assertEqual(restored.config.native_temp_audio_retention["max_files"], 3)
        self.assertTrue(restored.config.native_temp_audio_retention["delete_after_analysis"])

    def test_background_runtime_reports_native_shell_target(self) -> None:
        runtime = BackgroundRuntime()
        status = runtime.status()

        self.assertEqual(status["capabilities"]["desktop_shell_target"], "apps/macos")
        self.assertTrue(status["capabilities"]["native_shell_api"])
        self.assertTrue(status["capabilities"]["live_signal_api"])
        self.assertEqual(status["capabilities"]["daemon_supervision"], "native_shell")
        self.assertTrue(status["capabilities"]["native_system_audio_signal_tap"])
        self.assertTrue(status["capabilities"]["native_system_audio_temp_analysis"])
        self.assertTrue(status["capabilities"]["native_temp_audio_cleanup"])
        self.assertTrue(status["capabilities"]["route_rerun_api"])
        self.assertTrue(status["capabilities"]["recent_result_history"])
        self.assertTrue(status["capabilities"]["durable_recent_history"])
        self.assertTrue(status["capabilities"]["pinned_recent_results"])
        self.assertTrue(status["capabilities"]["recent_history_management"])
        self.assertTrue(status["capabilities"]["recent_history_archive"])
        self.assertTrue(status["capabilities"]["recent_history_batch_review"])
        self.assertTrue(status["capabilities"]["generation_prompt_api"])
        self.assertTrue(status["capabilities"]["generation_relisten_api"])

    def test_background_runtime_tracks_bounded_recent_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = BackgroundRuntime(config_path=Path(tmp) / "background.json")

            for index in range(14):
                runtime.finish_action({
                    "id": f"evt_{index}",
                    "aggregate": {"title": f"Event {index}"},
                    "routes": [{"route_id": "basic-listener"}],
                })
            runtime.finish_action({
                "id": "evt_12",
                "aggregate": {"title": "Event 12 updated"},
                "routes": [{"route_id": "signal-health"}],
            })
            history = runtime.history()

        self.assertEqual(history["limit"], 12)
        self.assertEqual(len(history["recent_events"]), 12)
        self.assertEqual(history["recent_events"][0]["id"], "evt_12")
        self.assertEqual(history["recent_events"][0]["routes"][0]["route_id"], "signal-health")
        self.assertEqual(sum(1 for event in history["recent_events"] if event["id"] == "evt_12"), 1)

    def test_background_runtime_persists_recent_history_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "background.json"
            runtime = BackgroundRuntime(config_path=config_path)
            runtime.finish_action({
                "id": "evt_file",
                "source": {"type": "file", "label": "Fixture"},
                "segment": {"data_ref": {"uri": "/tmp/file.wav"}},
                "aggregate": {"title": "Machine hum", "short_summary": "A low machine hum."},
                "routes": [{"route_id": "signal-health"}],
                "tags": ["machine"],
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            })
            runtime.finish_action({
                "id": "evt_private",
                "source": {"type": "system_output", "label": "Private"},
                "aggregate": {"title": "Private event"},
                "routes": [{"route_id": "basic-listener"}],
                "privacy_mode": "incognito",
                "raw_audio_policy": "temp",
            })
            restored = BackgroundRuntime(config_path=config_path)
            signal_history = restored.filtered_history(route="signal-health")
            rerunnable_history = restored.filtered_history(rerunnable=True)
            query_history = restored.filtered_history(q="machine")
            pin_response = restored.set_pinned_event("evt_file", pinned=True)
            restored_after_pin = BackgroundRuntime(config_path=config_path)
            after_pin_history = restored_after_pin.history()
            export = restored_after_pin.export_history()
            keep_pinned_response = restored_after_pin.clear_history(keep_pinned=True)
            after_keep_pinned_clear = restored_after_pin.history()
            clear_all_response = restored_after_pin.clear_history(keep_pinned=False)
            after_clear_all = restored_after_pin.history()

        self.assertEqual([event["id"] for event in restored.history()["recent_events"]], ["evt_file"])
        self.assertTrue(restored.history()["persistent"])
        self.assertEqual(signal_history["recent_events"][0]["id"], "evt_file")
        self.assertEqual(rerunnable_history["recent_events"][0]["id"], "evt_file")
        self.assertEqual(query_history["recent_events"][0]["id"], "evt_file")
        self.assertTrue(pin_response["pinned"])
        self.assertEqual(after_pin_history["pinned_events"][0]["id"], "evt_file")
        self.assertEqual(export["export_kind"], "derived_recent_result_history")
        self.assertEqual(export["pinned_events"][0]["id"], "evt_file")
        self.assertIn("raw audio is not copied", export["raw_audio_policy"])
        self.assertTrue(keep_pinned_response["keep_pinned"])
        self.assertEqual(after_keep_pinned_clear["recent_events"], [])
        self.assertEqual(after_keep_pinned_clear["pinned_events"][0]["id"], "evt_file")
        self.assertFalse(clear_all_response["keep_pinned"])
        self.assertEqual(after_clear_all["recent_events"], [])
        self.assertEqual(after_clear_all["pinned_events"], [])

    def test_background_runtime_batches_and_archives_derived_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "background.json"
            runtime = BackgroundRuntime(config_path=config_path)
            runtime.finish_action({
                "id": "evt_a",
                "source": {"type": "file", "label": "Fixture A"},
                "aggregate": {"title": "Fixture A"},
                "routes": [{"route_id": "basic-listener"}],
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            })
            runtime.finish_action({
                "id": "evt_b",
                "source": {"type": "file", "label": "Fixture B"},
                "aggregate": {"title": "Fixture B"},
                "routes": [{"route_id": "signal-health"}],
                "privacy_mode": "session",
                "raw_audio_policy": "external_ref",
            })

            batch = runtime.set_pinned_events(["evt_a", "missing", "evt_b", "evt_a"], pinned=True)
            unpin = runtime.set_pinned_events(["evt_a"], pinned=False)
            archive = runtime.archive_history(event_ids=["evt_b"], label="Native Review!")
            archive_path = Path(archive["archive_path"])
            archive_exists = archive_path.exists()
            archived_payload = json.loads(archive_path.read_text(encoding="utf-8"))

        self.assertEqual(batch["event_ids"], ["evt_a", "missing", "evt_b"])
        self.assertEqual(batch["pinned_event_ids"], ["evt_a", "evt_b"])
        self.assertEqual(batch["missing_event_ids"], ["missing"])
        self.assertEqual([event["id"] for event in batch["history"]["pinned_events"]], ["evt_a", "evt_b"])
        self.assertEqual([event["id"] for event in unpin["history"]["pinned_events"]], ["evt_b"])
        self.assertTrue(archive["archived"])
        self.assertEqual(archive["archive_label"], "native-review")
        self.assertEqual(archive["event_count"], 1)
        self.assertTrue(archive_exists)
        self.assertEqual(archived_payload["archive_kind"], "derived_recent_result_history_archive")
        self.assertEqual(archived_payload["selected_event_ids"], ["evt_b"])
        self.assertEqual(archived_payload["selected_events"][0]["id"], "evt_b")
        self.assertIn("raw audio is not copied", archived_payload["raw_audio_policy"])

    def test_incognito_event_is_not_retained_in_latest_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = BackgroundRuntime(config_path=Path(tmp) / "background.json")
            runtime.finish_action({"id": "evt_ok", "aggregate": {"title": "Visible"}, "privacy_mode": "session"})
            runtime.finish_action({"id": "evt_secret", "aggregate": {"title": "Secret"}, "privacy_mode": "incognito"})

            self.assertIsNotNone(runtime.state.latest_event)
            self.assertEqual(runtime.state.latest_event["id"], "evt_ok")
            self.assertNotIn("evt_secret", [event["id"] for event in runtime.history()["recent_events"]])

    def test_temp_policy_redacts_embedded_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AkousmataStore(root=Path(tmp) / "akousmata")
            trace = store.remember({
                "id": "evt_temp",
                "source": {"type": "system_output", "label": "temp"},
                "segment": {"data_ref": {"kind": "path", "uri": "/private/tmp/secret.wav"}},
                "aggregate": {"title": "Temp", "short_summary": "Temp."},
                "features": {"duration_s": 1.0, "rmsDbfs": -20.0},
                "privacy_mode": "ephemeral",
                "raw_audio_policy": "temp",
            })

        self.assertIsNone(trace["event"]["segment"]["data_ref"]["uri"])
        self.assertIsNone(trace["audioRef"])
        self.assertEqual(trace["privacyMode"], "session")

    def test_conversation_store_redacts_temp_event_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conversations = ConversationStore(root=Path(tmp) / "conversations")
            memory = AkousmataStore(root=Path(tmp) / "akousmata")
            event = {
                "id": "evt_temp_conversation",
                "source": {"type": "system_output", "label": "temp", "details": {"path": "/private/tmp/secret.wav"}},
                "segment": {"data_ref": {"kind": "path", "uri": "/private/tmp/secret.wav"}},
                "aggregate": {"title": "Temp", "short_summary": "Temporary capture."},
                "features": {"duration_s": 1.0, "rmsDbfs": -20.0},
                "privacy_mode": "ephemeral",
                "raw_audio_policy": "temp",
            }

            result = conversations.ask(event=event, question="What is this?", memory=memory)
            stored = conversations.get(result["conversation_id"])

        self.assertIsNone(stored["event"]["segment"]["data_ref"]["uri"])
        self.assertTrue(stored["event"]["segment"]["data_ref"]["redacted"])
        self.assertIsNone(stored["event"]["source"]["details"]["path"])

    def test_generation_store_redacts_temp_source_event_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = GenerationStore(root=Path(tmp) / "generations")
            event = {
                "id": "evt_temp_generation",
                "source": {"type": "system_output", "label": "temp", "details": {"path": "/private/tmp/secret.wav"}},
                "segment": {"duration_ms": 1000, "data_ref": {"kind": "path", "uri": "/private/tmp/secret.wav"}},
                "aggregate": {"title": "Temp", "short_summary": "Temporary capture.", "signal_facts": []},
                "routes": [{"route_id": "signal-health", "summary": "temporary signal"}],
                "features": {"duration_s": 1.0, "rmsDbfs": -20.0},
                "privacy_mode": "ephemeral",
                "raw_audio_policy": "temp",
            }

            record = store.create_prompt(event)

        self.assertIsNone(record["source_event"]["segment"]["data_ref"]["uri"])
        self.assertTrue(record["source_event"]["segment"]["data_ref"]["redacted"])
        self.assertIsNone(record["source_event"]["source"]["details"]["path"])

    def test_native_system_audio_cleanup_only_targets_native_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native = root / "20260626T000000000000Z-oida-native-system-output-10s.wav"
            other = root / "20260626T000000000000Z-user-upload.wav"
            native.write_bytes(b"native")
            other.write_bytes(b"upload")

            status = native_system_audio_temp_status(directory=root)
            cleanup = cleanup_native_system_audio_temp_files(directory=root, delete_all=True)

            self.assertEqual(status["file_count"], 1)
            self.assertEqual(cleanup["deleted_count"], 1)
            self.assertFalse(native.exists())
            self.assertTrue(other.exists())


def write_tone(path: Path, *, frequency: float = 440.0, duration_s: float = 1.0) -> Path:
    sample_rate = 16_000
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    samples = (0.2 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
    sf.write(path, samples, sample_rate)
    return path


if __name__ == "__main__":
    unittest.main()
