from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jsonschema
import numpy as np
import soundfile as sf

from aear.engine_stub import StubMossEngine
from aear.acoustic_system import acoustic_system_manifest
from aear.akouo_skills import akouo_manifest
from aear.engine_base import EngineResult, MossEngine
from aear.parsers import parse_events
from aear.reporting import direct_analysis, report, report_to_dict
from aear.recipes import RECIPES
from aear.route_comparison import compare_route_events
from aear.server import _rerun_segment, normalize_audio, sanitize_filename, upload_processing_info
from harness.akouo.command import build_command_output, build_harness_output
from harness.akouo.loader import AkouoLoader
from harness.akouo.routing import available_harness_controls, routing_plan


class SchemaCommandTests(unittest.TestCase):
    def test_perception_report_schema_validates_stub_report(self) -> None:
        report_dict = make_report()
        schema_path = Path(__file__).resolve().parents[1] / "aear" / "schemas" / "perception-report.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(report_dict)

    def test_akouo_command_output_schema_validates(self) -> None:
        loader = AkouoLoader()
        if not loader.schemas_dir.exists():
            self.skipTest(f"external AKOUO schemas are not available: {loader.schemas_dir}")
        report_dict = make_report()
        command_output = build_command_output(report_dict, command="/field")
        loader.validate("command-output", command_output)

    def test_akouo_method_command_output_schema_validates(self) -> None:
        loader = AkouoLoader()
        if not loader.schemas_dir.exists():
            self.skipTest(f"external AKOUO schemas are not available: {loader.schemas_dir}")
        report_dict = make_report()
        command_output = build_command_output(report_dict, command="/method")
        loader.validate("command-output", command_output)
        self.assertIn("reference-layer", command_output["skills_called"])

    def test_builtin_akouo_skill_manifests_match_schema(self) -> None:
        manifest = akouo_manifest()
        skill_schema = manifest["schemas"]["skill_manifest"]
        preset_schema = manifest["schemas"]["route_preset"]

        for skill in manifest["skills"]:
            jsonschema.Draft202012Validator(skill_schema).validate(skill)
        for preset in manifest["route_presets"]:
            jsonschema.Draft202012Validator(preset_schema).validate(preset)

    def test_invalid_akouo_command_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            routing_plan("fixture.wav", command="/not-a-command")

    def test_invalid_akouo_mode_is_rejected(self) -> None:
        report_dict = make_report()
        with self.assertRaises(ValueError):
            build_harness_output(report_dict, command="/listen", mode="not-a-mode")

    def test_akouo_loader_finds_real_checkout(self) -> None:
        loader = AkouoLoader()
        if not loader.root.exists():
            self.skipTest(f"external AKOUO checkout is not available: {loader.root}")
        self.assertTrue(loader.root.exists())
        self.assertTrue((loader.root / "schemas").exists())
        self.assertTrue((loader.root / "skills").exists())

    def test_harness_controls_include_field_and_all_modes(self) -> None:
        controls = available_harness_controls()
        commands = {item["command"] for item in controls["commands"]}
        self.assertIn("/field", commands)
        self.assertIn("ecological-posthuman-listening", controls["modes"])

    def test_direct_moss_analysis_modes_exist(self) -> None:
        for mode in ("environment", "music", "soundscape", "sonic_data"):
            self.assertIn(mode, RECIPES)

    def test_direct_moss_analysis_returns_limitations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.wav"
            sample_rate = 16_000
            t = np.arange(sample_rate) / sample_rate
            samples = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            sf.write(path, samples, sample_rate)

            analysis, engine_result = direct_analysis(StubMossEngine(), str(path), "environment")

        self.assertEqual(analysis["mode"], "environment")
        self.assertEqual(analysis["source_role"], "moss_audio_direct")
        self.assertTrue(analysis["limitations"])
        self.assertEqual(engine_result.profile, "stub")

    def test_acoustic_system_manifest_covers_non_speech_modes(self) -> None:
        manifest = acoustic_system_manifest()
        self.assertEqual(manifest["status"], "operational")
        self.assertEqual(manifest["claim_layer"], "akouo_mapping_ready")
        modes = {mode["id"] for mode in manifest["modes"]}
        self.assertIn("environmental_sound", modes)
        self.assertIn("music", modes)
        self.assertIn("soundscape_research", modes)
        self.assertIn("speech_voice", modes)

    def test_report_collects_uncertainty_from_all_model_passes(self) -> None:
        report_dict = make_report()
        notes = report_dict["model_uncertainty_notes"]
        self.assertEqual(notes, ["MOSS-Audio weights are not configured"])

    def test_upload_helpers_sanitize_and_skip_wav_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            wav.write_bytes(b"RIFF")
            normalized, error = normalize_audio(wav)
            processing = upload_processing_info(wav, normalized)

        self.assertEqual(sanitize_filename("../bad name!.wav"), "bad-name-.wav")
        self.assertEqual(normalized, wav)
        self.assertIsNone(error)
        self.assertFalse(processing["decoded_to_wav"])
        self.assertIn("16 kHz mono", processing["moss_input"])

    def test_route_rerun_segment_preserves_source_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rerun.wav"
            sample_rate = 16_000
            t = np.arange(sample_rate) / sample_rate
            samples = (0.1 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
            sf.write(path, samples, sample_rate)
            event = {
                "id": "evt_rerun",
                "source": {
                    "type": "system_output",
                    "label": "Native system audio / Display system mix",
                    "device_id": "native-display-mix",
                    "details": {"capture_scope": "display_mix"},
                },
                "segment": {
                    "data_ref": {"kind": "path", "uri": str(path), "sha256": "x"},
                    "privacy_mode": "ephemeral",
                    "ephemeral": True,
                    "metadata": {"raw_audio_policy": "temp", "capture_scope": "display_mix"},
                },
                "routes": [{"route_id": "basic-listener"}],
                "privacy_mode": "ephemeral",
                "raw_audio_policy": "temp",
            }
            rerun_path, segment, privacy_mode, raw_audio_policy = _rerun_segment(event)

        self.assertEqual(rerun_path, path.resolve())
        self.assertEqual(segment.source.type, "system_output")
        self.assertEqual(segment.source.device_id, "native-display-mix")
        self.assertEqual(segment.source.details["capture_scope"], "display_mix")
        self.assertEqual(segment.metadata["route_rerun"]["from_event_id"], "evt_rerun")
        self.assertEqual(privacy_mode, "ephemeral")
        self.assertEqual(raw_audio_policy, "temp")

    def test_route_comparison_reports_route_and_signal_deltas(self) -> None:
        base = {
            "id": "evt_base",
            "source": {"label": "Native system audio"},
            "segment": {"data_ref": {"kind": "path", "uri": "/tmp/a.wav", "sha256": "abc"}},
            "routes": [{"route_id": "basic-listener"}],
            "aggregate": {"short_summary": "Initial pass.", "warnings": ["model uncertainty"]},
            "features": {"rmsDbfs": -32.0, "peakDbfs": -8.0, "spectralCentroidHz": 420.0},
        }
        current = {
            "id": "evt_current",
            "source": {"label": "Native system audio"},
            "segment": {"data_ref": {"kind": "path", "uri": "/tmp/a.wav", "sha256": "abc"}},
            "routes": [{"route_id": "signal-health"}],
            "aggregate": {"short_summary": "Signal pass.", "warnings": ["clipping risk"]},
            "features": {"rmsDbfs": -30.5, "peakDbfs": -7.0, "spectralCentroidHz": 390.0},
        }

        comparison = compare_route_events(base, current)

        self.assertTrue(comparison["same_segment"])
        self.assertEqual(comparison["added_routes"], ["signal-health"])
        self.assertEqual(comparison["removed_routes"], ["basic-listener"])
        self.assertEqual(comparison["signal_delta"]["rmsDbfs"]["delta"], 1.5)
        self.assertEqual(comparison["warning_delta"]["added"], ["clipping risk"])
        self.assertEqual(comparison["warning_delta"]["resolved"], ["model uncertainty"])
        self.assertTrue(comparison["change_flags"]["routes_changed"])
        self.assertTrue(comparison["change_flags"]["signal_changed"])

        filtered = compare_route_events(
            base,
            current,
            signal_fields=["rmsDbfs", "peakDbfs"],
            min_abs_signal_delta=1.1,
            changed_only=True,
        )

        self.assertEqual(set(filtered["signal_delta"]), {"rmsDbfs"})
        self.assertEqual(filtered["applied_filters"]["signal_fields"], ["rmsDbfs", "peakDbfs"])
        self.assertEqual(filtered["applied_filters"]["min_abs_signal_delta"], 1.1)
        self.assertTrue(filtered["applied_filters"]["changed_only"])

    def test_event_parser_preserves_hyphenated_labels(self) -> None:
        events = parse_events("0.00-1.00 low-frequency hum - steady tone")

        self.assertEqual(events[0].label, "low-frequency hum")
        self.assertEqual(events[0].description, "steady tone")

    def test_single_report_aggregates_engine_wall_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.wav"
            sample_rate = 16_000
            t = np.arange(sample_rate) / sample_rate
            samples = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            sf.write(path, samples, sample_rate)

            report_dict = report_to_dict(report(ChunkEngine(), str(path)))

        self.assertEqual(report_dict["engine"]["wall_ms"], 5)

    def test_chunked_report_offsets_and_merges_model_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "longish.wav"
            sample_rate = 16_000
            t = np.arange(int(sample_rate * 1.2)) / sample_rate
            samples = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            sf.write(path, samples, sample_rate)

            report_dict = report_to_dict(report(ChunkEngine(), str(path), chunk_seconds=0.5, overlap_seconds=0.1))

        self.assertGreater(len(report_dict["engine"]["chunks"]), 1)
        self.assertGreater(len(report_dict["transcript"]["segments"]), 1)
        self.assertGreater(report_dict["transcript"]["segments"][1]["t0"], 0.3)
        self.assertIn("Segment 1", report_dict["caption"]["dense"])
        self.assertGreater(len(report_dict["events"]), 1)


def make_report() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture.wav"
        sample_rate = 16_000
        t = np.arange(sample_rate) / sample_rate
        samples = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        sf.write(path, samples, sample_rate)
        return report_to_dict(report(StubMossEngine(), str(path)))


class ChunkEngine(MossEngine):
    profile = "chunk-test"

    def generate(self, audio_path: str, prompt: str, settings, thinking_budget: int | None = None) -> EngineResult:
        lowered = prompt.lower()
        if "transcribe" in lowered:
            text = "[0.05]chunk words[0.15]"
        elif "sound event" in lowered:
            text = "0.05-0.15 click - short test event"
        elif "speaker" in lowered:
            text = "present: false"
        elif "music" in lowered:
            text = "present: false"
        else:
            text = "chunk caption"
        return EngineResult(text=text, model="test/chunk-engine", profile=self.profile, settings=settings, wall_ms=1)


if __name__ == "__main__":
    unittest.main()
