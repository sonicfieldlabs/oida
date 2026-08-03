from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from oida.engine_stub import StubMossEngine
from oida.listening import listening_event_dict
from oida.reporting import report, report_to_dict
from oida.signal_listener import interpret_signal
from oida.sonicfield import SonicFieldBridge, terms_from_event
from harness.akouo.command import build_harness_output


def write_sine_wav(path: Path, *, seconds: float = 2.0, freq: float = 440.0, sample_rate: int = 16000, amplitude: float = 0.5) -> None:
    frame_count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for i in range(frame_count):
            value = int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            frames += struct.pack("<h", value)
        handle.writeframes(bytes(frames))


class SignalListenerTests(unittest.TestCase):
    def test_click_track_reads_music_like(self) -> None:
        reading = interpret_signal(
            {
                "durationSeconds": 10,
                "features": {
                    "rmsDbfs": -20,
                    "peakDbfs": -3,
                    "spectralFlatness": 0.08,
                    "spectralCentroidHz": 1200,
                    "onsetDensityPerSec": 2.0,
                    "bpmCandidate": 120.0,
                    "integratedLufs": -16,
                },
            }
        )
        self.assertEqual(reading.classification, "music-like")
        self.assertTrue(any("BPM" in h["statement"] for h in reading.hypotheses))
        self.assertIn("measured signal features", reading.caption)

    def test_silence_reads_silence(self) -> None:
        reading = interpret_signal({"durationSeconds": 4, "features": {"rmsDbfs": -72, "peakDbfs": -60, "silenceRatio": 0.99}})
        self.assertEqual(reading.classification, "silence")

    def test_broadband_noise_reads_noise_like(self) -> None:
        reading = interpret_signal(
            {
                "durationSeconds": 6,
                "features": {
                    "rmsDbfs": -18,
                    "peakDbfs": -6,
                    "spectralFlatness": 0.65,
                    "spectralCentroidHz": 4000,
                    "onsetDensityPerSec": 0.1,
                    "zeroCrossingRate": 8000,
                },
            }
        )
        self.assertEqual(reading.classification, "noise-like")

    def test_empty_features_do_not_crash(self) -> None:
        reading = interpret_signal({})
        self.assertTrue(reading.caption)


class RouteScopedReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = Path(self.tmp.name) / "tone.wav"
        write_sine_wav(self.wav)
        self.engine = StubMossEngine()

    def test_dsp_only_report_skips_model_passes(self) -> None:
        perception = report(self.engine, str(self.wav), passes=[])
        data = report_to_dict(perception)
        self.assertEqual(data["moss_passes"], [])
        self.assertEqual(data["engine"]["model"], "dsp-only")
        self.assertIsNone(data["engine"]["unavailable_reason"])
        self.assertIsNotNone(data["signal_interpretation"])
        self.assertNotIn("Stub engine did not listen", json.dumps(data))

    def test_caption_only_report_runs_one_pass(self) -> None:
        perception = report(self.engine, str(self.wav), passes=["caption"])
        data = report_to_dict(perception)
        self.assertEqual(data["moss_passes"], ["caption"])
        self.assertEqual(data["model_uncertainty_notes"], ["MOSS-Audio weights are not configured"])

    def test_unknown_pass_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            report(self.engine, str(self.wav), passes=["caption", "vibes"])

    def test_listening_event_uses_signal_reading_when_model_is_absent(self) -> None:
        perception = report(self.engine, str(self.wav), passes=[])
        data = report_to_dict(perception)
        command_output = build_harness_output(data, command="/tech")
        self.assertEqual(command_output["routing_plan"]["evidence_level"], "measured_signal")
        event = listening_event_dict(data, command_output=command_output, route_preset_id="signal")
        summary = event["aggregate"]["short_summary"]
        self.assertNotIn("Stub engine", summary)
        self.assertNotIn("No confident listening summary", summary)
        self.assertTrue(event["aggregate"]["title"])
        inferred = [h["statement"] for h in event["aggregate"]["hypotheses"]]
        self.assertTrue(any("resembles" in statement for statement in inferred))


class SonicFieldBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "content" / "topics").mkdir(parents=True)
        (root / "content" / "journal").mkdir(parents=True)
        (root / "content" / "archive").mkdir(parents=True)
        (root / "data" / "runtime").mkdir(parents=True)
        (root / "config" / "taxonomy").mkdir(parents=True)
        (root / "content" / "topics" / "drone-music.mdx").write_text(
            "---\ntitle: Drone Music\nsummary: Sustained tonal practice.\ntags: [drone, duration]\naliases: [sustained-tone]\n---\nLong tones and stasis.\n"
        )
        (root / "content" / "journal" / "listening-to-hums.mdx").write_text(
            "---\ntitle: Listening To Hums\nsummary: Machine hum as keynote.\ntags: [hum, infrastructure]\n---\nThe refrigerator hum is a drone of the domestic.\n"
        )
        (root / "content" / "archive" / "drone-and-duration.mdx").write_text(
            "---\ntitle: Drone And Duration\ntype: paper\nsummary: On sustained sound.\ntags: [drone]\n---\nBody.\n"
        )
        (root / "data" / "runtime" / "wiki-pages.json").write_text(
            json.dumps(
                [
                    {
                        "meta": {"slug": "drone", "title": "Drone", "type": "concept", "tags": ["duration", "sustain"]},
                        "content": "A drone is a sustained tone. It anchors listening.",
                    }
                ]
            )
        )
        (root / "config" / "taxonomy" / "topic-aliases.json").write_text(json.dumps({"sustained-tone": "drone"}))
        self.bridge = SonicFieldBridge(root)

    def test_explore_finds_across_surfaces(self) -> None:
        result = self.bridge.explore(["drone", "sustained-tone"])
        self.assertGreaterEqual(result["total"], 3)
        self.assertIn("wiki", result["groups"])
        self.assertIn("topics", result["groups"])
        self.assertIn("library", result["groups"])
        self.assertIn("drone", result["query_terms"])

    def test_terms_from_event_extracts_tags_and_title(self) -> None:
        event = {
            "tags": ["tonal-sustained"],
            "aggregate": {
                "title": "Sustained tonal material",
                "primary_tags": ["drone"],
                "short_summary": "Sustained tonal material: quiet, dark.",
                "hypotheses": [{"statement": "The signal most resembles sustained tonal material."}],
            },
        }
        terms = terms_from_event(event, extra_query="hum")
        self.assertIn("hum", terms)
        joined = " ".join(terms)
        self.assertIn("drone", joined)

    def test_missing_root_reports_unavailable(self) -> None:
        bridge = SonicFieldBridge(Path(self.tmp.name) / "missing")
        self.assertFalse(bridge.available)
        with self.assertRaises(ValueError):
            bridge.explore(["drone"])

    def test_index_failure_does_not_expose_exception_details(self) -> None:
        self.bridge._load_aliases = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("private path: /Users/listener/secret")
        )
        self.bridge.ensure_index()
        self.assertEqual(self.bridge.status()["error"], "Sonic Field index unavailable")
        self.assertNotIn("secret", json.dumps(self.bridge.status()))


if __name__ == "__main__":
    unittest.main()
