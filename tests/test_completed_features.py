from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import numpy as np
import soundfile as sf

from oida.engine_stub import StubMossEngine
from oida.live import LiveManager
from oida.reporting import caption, transcribe
from harness.akoe_cli import run_chat
from harness.benchmark import summarize
from harness.corpus import answer_timeline_question, timeline_entry, write_timeline
from harness.dialog import append_turn, context_text, default_session_id, load_session, save_session
from harness.mcp_server.server import TOOLS, handle


class CompletedFeatureTests(unittest.TestCase):
    def test_dialog_session_retains_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = load_session(tmp, default_session_id("field.wav"), "field.wav")
            append_turn(session, "What happens first?", {"qa": {"answer": "A click happens first."}})
            save_session(tmp, session)
            restored = load_session(tmp, session["session_id"], "field.wav")

        context = context_text(restored)
        self.assertIn("What happens first?", context)
        self.assertIn("A click happens first.", context)

    def test_corpus_timeline_qa_returns_matching_entry(self) -> None:
        report = {
            "source": {"path": "rain.wav", "duration_s": 2.0},
            "caption": {"dense": "Rain and distant traffic."},
            "events": [{"label": "rain", "description": "steady water texture"}],
            "transcript": {"segments": []},
        }
        timeline = {"entries": [timeline_entry("rain.wav", report)]}
        answer = answer_timeline_question(timeline, "Where is rain present?")

        self.assertEqual(answer["matches"][0]["path"], "rain.wav")

    def test_corpus_timeline_qa_uses_token_matches_not_substrings(self) -> None:
        timeline = {
            "entries": [
                {"source_path": "training.wav", "caption": "A training room tone.", "events": [], "transcript_segments": []},
                {"source_path": "rain.wav", "caption": "Rain falls steadily.", "events": [], "transcript_segments": []},
            ]
        }
        answer = answer_timeline_question(timeline, "rain")

        self.assertEqual(answer["matches"][0]["path"], "rain.wav")

    def test_timeline_writer_persists_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = write_timeline(Path(tmp) / "timeline.json", [{"path": "a.wav"}])

        self.assertEqual(output.name, "timeline.json")

    def test_benchmark_summary_tracks_high_water(self) -> None:
        summary = summarize([
            {"client_wall_s": 1.0, "server_max_rss_mb_after": 100.0, "approx_output_tokens_per_engine_s": 12.0},
            {"client_wall_s": 3.0, "server_max_rss_mb_after": 120.0, "approx_output_tokens_per_engine_s": 6.0},
        ])

        self.assertEqual(summary["server_high_water_rss_mb"], 120.0)
        self.assertEqual(summary["mean_client_wall_s"], 2.0)

    def test_live_manager_ingests_audio_and_marks_vad_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.wav"
            sample_rate = 16_000
            t = np.arange(sample_rate // 2) / sample_rate
            samples = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            sf.write(path, samples, sample_rate)

            manager = LiveManager()
            started = manager.start(ring_seconds=2, vad_threshold_dbfs=-45)
            status = manager.ingest_saved_upload(started["session_id"], {"path": str(path), "raw_path": str(path), "sha256": "x"})

        self.assertEqual(status["chunk_count"], 1)
        self.assertTrue(status["latest_chunk"]["vad_active"])
        self.assertTrue(status["ring_capacity_ok"])

    def test_live_manager_reports_single_chunk_ring_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.wav"
            sample_rate = 16_000
            t = np.arange(sample_rate * 2) / sample_rate
            samples = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            sf.write(path, samples, sample_rate)

            manager = LiveManager()
            started = manager.start(ring_seconds=0.1, vad_threshold_dbfs=-45)
            status = manager.ingest_saved_upload(started["session_id"], {"path": str(path), "raw_path": str(path), "sha256": "x"})

        self.assertEqual(status["chunk_count"], 1)
        self.assertFalse(status["ring_capacity_ok"])
        self.assertGreater(status["ring_overflow_s"], 0)

    def test_transcribe_and_caption_reject_invalid_modes(self) -> None:
        engine = StubMossEngine()
        with self.assertRaises(ValueError):
            transcribe(engine, "fixture.wav", timestamps="paragraph")
        with self.assertRaises(ValueError):
            caption(engine, "fixture.wav", detail="verbose")

    def test_chat_requires_at_least_one_question(self) -> None:
        args = Namespace(question=[], path="fixture.wav")
        with self.assertRaises(SystemExit):
            run_chat(args)

    def test_mcp_server_prefers_aear_tool_aliases(self) -> None:
        names = {tool["name"] for tool in TOOLS}
        self.assertIn("aear_report", names)
        self.assertIn("aear_transcribe", names)
        self.assertIn("aear_qa", names)
        self.assertIn("ear_report", names)

    def test_mcp_server_ignores_notifications(self) -> None:
        response = handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

        self.assertIsNone(response)

    def test_mcp_server_rejects_invalid_request_shape(self) -> None:
        response = handle([])

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["error"]["code"], -32600)

    def test_mcp_server_returns_tool_error_result_for_missing_args(self) -> None:
        response = handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "aear_report", "arguments": {}}})

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["id"], 7)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("missing required argument: path", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
