"""AKOÚŌ v0.6 + Earworm v0.2 integration tests.

Covers the v0.6 surface oída now implements: the /remember command and
memory-lineage-listening mode, per-claim source/time_range tagging, apparatus
and listener declarations, the earworm spec v1.1 bridge (listening envelopes,
summaries, recurrence relations), and the drift check against the published
akouo.manifest.json contract.
"""
from __future__ import annotations

import tempfile
import unittest

import akousma

from harness.akouo.command import build_command_output
from harness.akouo.manifest import drift_errors, load_manifest, load_presets
from harness.akouo.routing import claim_permissions_for, route_for_command
from harness.claim_mapper import map_report_to_claims
from oida.akousma_bridge import build_akousma_from_listen, handoff_to_germ
from oida.akouo_skills import AKOUO_CONTRACT_VERSION, akouo_manifest, route_preset


def _report() -> dict:
    return {
        "source": {"path": "/tmp/example.wav"},
        "engine": {"model": "moss-audio-test", "profile": "mac-mps"},
        "dsp": {
            "durationSeconds": 4.2,
            "sampleRate": 48000,
            "channelCount": 2,
            "features": {"rmsDbfs": -21.0},
        },
        "caption": {"dense": "A steady tonal hum with slow pulses."},
        "events": [
            {"label": "hum", "t0": 0.0, "t1": 4.2, "confidence": "medium"},
        ],
        "transcript": {"present": False},
        "speech": {"present": False},
        "music": {"present": False},
    }


class TestAkouoV06Contract(unittest.TestCase):
    def test_contract_version_is_v07(self) -> None:
        self.assertEqual(AKOUO_CONTRACT_VERSION, "v0.7")
        manifest = akouo_manifest()
        self.assertEqual(manifest["version"], "0.7-oida.1")
        self.assertIn("/remember", manifest["public_commands"])
        self.assertIn("/covenant", manifest["public_commands"])
        self.assertEqual(manifest["errors"], [])

    def test_remember_preset_exists(self) -> None:
        preset = route_preset("remember")
        self.assertEqual(preset.akouo_command, "/remember")
        self.assertIn("comparative-memory", preset.skill_ids)

    def test_legacy_preset_aliases_resolve(self) -> None:
        self.assertEqual(route_preset("environment").id, "field")
        self.assertEqual(route_preset("speech").id, "voice")
        self.assertEqual(route_preset("memory").id, "recall")
        manifest = akouo_manifest()
        self.assertEqual(
            manifest["preset_aliases"],
            {"environment": "field", "speech": "voice", "memory": "recall"},
        )

    def test_remember_route(self) -> None:
        route = route_for_command("/remember")
        self.assertEqual(route.modes[0], "memory-lineage-listening")
        self.assertIn("signal-inspection-listening", route.modes)

    def test_fiction_grants_speculative(self) -> None:
        permissions = claim_permissions_for("mixed", "/fiction")
        self.assertTrue(permissions["speculative_allowed"])
        strict = claim_permissions_for("mixed", "/forensic")
        self.assertFalse(strict["interpreted_allowed"])
        self.assertFalse(strict["speculative_allowed"])

    def test_no_drift_against_published_manifest(self) -> None:
        errors = drift_errors()
        if errors is None:
            self.skipTest("akouo.manifest.json not available (pre-v0.6 checkout)")
        self.assertEqual(errors, [])

    def test_upstream_presets_load(self) -> None:
        manifest = load_manifest()
        if manifest is None:
            self.skipTest("akouo.manifest.json not available (pre-v0.6 checkout)")
        presets = load_presets()
        ids = {p["id"] for p in presets}
        self.assertIn("remember", ids)
        self.assertIn("recall", ids)
        # oída's preset vocabulary is a strict subset of the upstream portable set
        oida_ids = {p["id"] for p in akouo_manifest()["route_presets"]}
        self.assertLessEqual(oida_ids, ids, f"oída presets outside upstream vocabulary: {oida_ids - ids}")


class TestClaimInstrumentation(unittest.TestCase):
    def test_claim_sources_and_time_ranges(self) -> None:
        claims = map_report_to_claims(_report())
        measured_sources = {claim.get("source") for claim in claims["measured"]}
        self.assertEqual(measured_sources, {"dsp"})
        heard_events = [claim for claim in claims["heard"] if "Sound event" in claim["statement"]]
        self.assertTrue(heard_events)
        self.assertEqual(heard_events[0]["source"], "model")
        self.assertEqual(heard_events[0]["time_range"], {"start_s": 0.0, "end_s": 4.2})
        inferred_sources = {claim.get("source") for claim in claims["inferred"]}
        self.assertIn("model", inferred_sources)

    def test_command_output_declares_apparatus(self) -> None:
        output = build_command_output(_report(), command="/remember")
        self.assertEqual(output["akouo_version"], "0.6")
        self.assertEqual(output["command"], "/remember")
        modes = [item["listening_mode"] for item in output["outputs"]]
        self.assertIn("memory-lineage-listening", modes)
        first = output["outputs"][0]
        self.assertEqual(first["akouo_version"], "0.6")
        self.assertEqual(first["listener"], {"type": "agent", "process": "agent_automated"})
        apparatus = first["apparatus"]
        self.assertEqual(apparatus["substrate"], "hybrid_agent_stack")
        self.assertEqual(apparatus["model_ids"], ["moss-audio-test"])
        self.assertIn("MOSS-Audio caption pass", apparatus["perception_sources"])
        self.assertTrue(apparatus["known_blind_spots"])


class TestEarwormV02Bridge(unittest.TestCase):
    def test_listening_envelope_and_summary(self) -> None:
        record = build_akousma_from_listen(
            audio={"asset_id": "a1", "content_hash": "sha256:abc"},
            listening={
                "oida.signal": {"class": "music-like", "caption": "steady hum with pulses"},
                "akouo.memory-lineage-listening": {"main_reading": "recurrence of the hum"},
            },
            origin="live_input",
        )
        self.assertEqual(akousma.validation_errors(record), [])
        self.assertEqual(record["summary"], "steady hum with pulses")
        signal_entry = record["listening"]["oida.signal"]
        self.assertEqual(signal_entry["payload"]["class"], "music-like")
        self.assertIn("created_at", signal_entry)
        akouo_entry = record["listening"]["akouo.memory-lineage-listening"]
        self.assertEqual(akouo_entry["contract"], "akouo/v0.7")
        self.assertEqual(akouo_entry["summary"], "recurrence of the hum")

    def test_recurrence_relation_on_same_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = akousma.AkousmataStore(tmp)
            try:
                first = build_akousma_from_listen(
                    audio={"asset_id": "a1", "content_hash": "sha256:same"},
                    listening={"oida.signal": {"caption": "first pass"}},
                )
                handoff_to_germ(first, "sound", store=store)
                second = build_akousma_from_listen(
                    audio={"asset_id": "a2", "content_hash": "sha256:same"},
                    listening={"oida.signal": {"caption": "second pass"}},
                )
                result = handoff_to_germ(second, "prompt", store=store)
                stored = store.get(result["akousma_id"])
                relations = stored["lineage"].get("relations", [])
                self.assertEqual(len(relations), 1)
                self.assertEqual(relations[0]["type"], "same_source_as")
                self.assertEqual(relations[0]["target_akousma_id"], first["akousma_id"])
                self.assertEqual(
                    store.related(first["akousma_id"]),
                    [{"type": "same_source_as", "akousma_id": second["akousma_id"], "direction": "incoming"}],
                )
            finally:
                store.close()

    def test_no_relation_for_new_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = akousma.AkousmataStore(tmp)
            try:
                record = build_akousma_from_listen(
                    audio={"asset_id": "a1", "content_hash": "sha256:unique"},
                    listening={"oida.signal": {"caption": "solo"}},
                )
                result = handoff_to_germ(record, "lineage", store=store)
                stored = store.get(result["akousma_id"])
                self.assertNotIn("relations", stored["lineage"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
