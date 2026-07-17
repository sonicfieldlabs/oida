"""oída→germ bridge + cross-app akousma round-trip (Phase 4 acceptance)."""
import tempfile
import unittest

import akousma
from oida import akousma_bridge


class TestGermDeepLinks(unittest.TestCase):
    def test_deep_link_format(self):
        url = akousma_bridge.germ_deep_link("akm_1", "prompt")
        self.assertIn("/import?", url)
        self.assertIn("akousma=akm_1", url)
        self.assertIn("mode=prompt", url)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            akousma_bridge.germ_deep_link("akm_1", "bogus")

    def test_origin_maps_to_earworm_source_type(self):
        rec = akousma_bridge.build_akousma_from_listen(
            audio={"asset_id": "a1"}, origin="live-input"
        )
        self.assertEqual(rec["provenance"]["source_type"], "recorded")
        self.assertEqual(rec["provenance"]["origin"], "live-input")
        self.assertEqual(rec["provenance"]["originating_app"], "oida")

    def test_location_and_capture_ride_the_record(self):
        rec = akousma_bridge.build_akousma_from_listen(
            audio={"asset_id": "a1"},
            origin="live-input",
            location={"lat": 6.2442, "lon": -75.5812, "accuracy_m": 12, "label": "río Medellín"},
            capture={"direction": "past", "seconds": 30, "trigger": "remote-ear"},
        )
        self.assertEqual(akousma.validation_errors(rec), [])
        self.assertEqual(rec["location"]["lat"], 6.2442)
        self.assertEqual(rec["location"]["source"], "gps")  # remote captures default to gps
        self.assertEqual(rec["capture"]["direction"], "past")
        self.assertEqual(rec["capture"]["seconds"], 30)
        self.assertIn("triggered_at", rec["capture"])

    def test_bad_location_is_rejected_before_the_store(self):
        with self.assertRaises(ValueError):
            akousma_bridge.build_akousma_from_listen(
                audio={"asset_id": "a1"},
                location={"lat": 123.0, "lon": 0.0},
            )
        with self.assertRaises(ValueError):
            akousma_bridge.build_akousma_from_listen(
                audio={"asset_id": "a1"},
                capture={"direction": "sideways"},
            )

    def test_listening_identity_extension_is_content_free(self):
        rec = akousma_bridge.build_akousma_from_listen(
            audio={"asset_id": "a1"},
            listening_identity={
                "contract": "oida/listening-identity/v0.1",
                "active": True,
                "sha256": "a" * 64,
                "application": "model_prompt",
                "applied_to": ["model_perception:caption"],
                "text": "This must never enter shared memory.",
            },
        )

        block = rec["extensions"]["oida.listening_identity"]
        self.assertEqual(block["sha256"], "a" * 64)
        self.assertFalse(block["content_included"])
        self.assertNotIn("text", block)


class TestCrossAppRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = akousma.AkousmataStore(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_listen_to_generate_to_lineage(self):
        # 1) oída listens to a file → akousma A, "open as prompt" hands it to germ.
        a = akousma_bridge.build_akousma_from_listen(
            audio={"asset_id": "file1", "uri": "akousmata://objects/x.wav", "duration_seconds": 8.0},
            origin="file",
            listening={"oida.signal": {"class": "tonal"}, "akouo.describe": {"summary": "struck bell"}},
        )
        handoff = akousma_bridge.handoff_to_germ(a, "prompt", store=self.store)
        A = handoff["akousma_id"]
        self.assertIn("mode=prompt", handoff["germ_url"])

        # 2) germ generates a child B whose lineage points at A.
        b = akousma.new_akousma(
            audio={"asset_id": "gen1"},
            originating_app="germ",
            source_type="generated",
            origin="generated",
            parent_akousma_ids=[A],
            operation="transform",
            prompt="make it metallic",
            model="stable-audio-3",
        )
        B = self.store.put(b)

        # 3) germ's lineage explorer walks ancestry; "explore lineage" from oída shows A→B.
        self.assertEqual(self.store.ancestors(B), [A])
        self.assertEqual(self.store.children(A), [B])

        # 4) algophony batch query retrieves germ generations from the shared store.
        germ_generations = [r["akousma_id"] for r in self.store.query(originating_app="germ")]
        self.assertIn(B, germ_generations)
        self.assertEqual(len(self.store.query()), 2)  # both A and B live in one store


if __name__ == "__main__":
    unittest.main()
