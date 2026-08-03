from __future__ import annotations

import unittest

from harness.akouo.routing import claim_permissions_for
from harness.claim_mapper import map_report_to_claims


def base_report() -> dict[str, object]:
    return {
        "engine": {"model": "OpenMOSS-Team/MOSS-Audio-4B-Instruct"},
        "dsp": {
            "durationSeconds": 10.0,
            "sampleRate": 48000,
            "channelCount": 2,
            "features": {"bpmCandidate": 120.0, "onsetCount": 16, "integratedLufs": -18.0},
        },
        "transcript": {"present": True, "segments": [{"t0": 1.0, "t1": 2.0, "text": "hello", "confidence": "high"}]},
        "events": [{"t0": 3.0, "t1": 4.0, "label": "door close", "description": "a short impact", "corroborated_by_dsp": True}],
        "caption": {"dense": "A quiet room with a brief impact."},
        "speech": {"present": True, "dimensions": {"emotion": "tense", "clarity": "clear"}, "identity_caution": True, "notes": []},
        "music": {"present": True, "description": "A pulse-led texture.", "dsp_bpm_candidate": 120.0, "moss_bpm_candidate": 152.0, "notes": []},
        "model_uncertainty_notes": [],
        "forbidden_topics_triggered": [],
    }


class ClaimMapperTests(unittest.TestCase):
    def test_paralinguistics_are_interpreted_not_heard_or_measured(self) -> None:
        claims = map_report_to_claims(base_report())
        interpreted = " ".join(claim["statement"] for claim in claims["interpreted"])
        heard = " ".join(claim["statement"] for claim in claims["heard"])
        measured = " ".join(claim["statement"] for claim in claims["measured"])

        self.assertIn("emotion", interpreted)
        self.assertNotIn("emotion", heard)
        self.assertNotIn("emotion", measured)
        self.assertEqual(claims["heard"], [])

    def test_model_transcript_and_events_are_inferred_not_heard(self) -> None:
        claims = map_report_to_claims(base_report())
        inferred = " ".join(claim["statement"] for claim in claims["inferred"])
        self.assertEqual(claims["heard"], [])
        self.assertIn("Transcript", inferred)
        self.assertIn("Sound event", inferred)

    def test_explicit_human_report_may_remain_heard(self) -> None:
        report = base_report()
        report["host_observations"] = [{
            "statement": "I heard a brief metallic impact.",
            "category": "heard",
            "source": "human",
            "confidence": "medium",
            "basis": "Attributable embodied report",
            "listening_pass_id": "pass-human-1",
        }]
        claims = map_report_to_claims(report, claim_permissions={"heard_allowed": False})
        self.assertEqual(claims["heard"][0]["source"], "human")
        self.assertEqual(claims["heard"][0]["listening_pass_id"], "pass-human-1")

    def test_machine_attempted_heard_claim_is_demoted(self) -> None:
        report = base_report()
        report["host_observations"] = [{
            "statement": "A model reports a brief metallic impact.",
            "category": "heard",
            "source": "model",
        }]
        claims = map_report_to_claims(report)
        self.assertEqual(claims["heard"], [])
        demoted = [item for item in claims["inferred"] if "brief metallic impact" in item["statement"]]
        self.assertTrue(demoted)
        self.assertIn("attributable embodied listening pass", demoted[0]["basis"])

    def test_forbidden_high_frequency_question_is_undetermined(self) -> None:
        claims = map_report_to_claims(base_report(), question="What is happening above 8 kHz?")
        undetermined = " ".join(claim["statement"] for claim in claims["undetermined"])
        self.assertIn("above roughly 8 kHz", undetermined)

    def test_tempo_disagreement_becomes_undetermined(self) -> None:
        claims = map_report_to_claims(base_report())
        undetermined = " ".join(claim["statement"] for claim in claims["undetermined"])
        self.assertIn("Tempo is unresolved", undetermined)

    def test_permissions_move_disallowed_measured_claims_to_undetermined(self) -> None:
        claims = map_report_to_claims(base_report(), claim_permissions={"measured_allowed": False})
        self.assertEqual(claims["measured"], [])
        self.assertTrue(any(claim["statement"].startswith("Blocked measured claim") for claim in claims["undetermined"]))

    def test_forbidden_stereo_caption_is_undetermined_not_inferred(self) -> None:
        report = base_report()
        report["caption"] = {"dense": "The recording has a wide stereo field around the listener."}
        claims = map_report_to_claims(report)

        inferred = " ".join(claim["statement"] for claim in claims["inferred"])
        undetermined = " ".join(claim["statement"] for claim in claims["undetermined"])
        self.assertNotIn("wide stereo field", inferred)
        self.assertIn("wide stereo field", undetermined)

    def test_paraphrased_spatial_caption_is_undetermined(self) -> None:
        report = base_report()
        report["caption"] = {"dense": "Footsteps panned hard left across the soundstage."}
        claims = map_report_to_claims(report)
        inferred = " ".join(claim["statement"] for claim in claims["inferred"])
        undetermined = " ".join(claim["statement"] for claim in claims["undetermined"])
        self.assertNotIn("soundstage", inferred)
        self.assertIn("soundstage", undetermined)

    def test_high_frequency_numeric_caption_is_undetermined(self) -> None:
        report = base_report()
        report["caption"] = {"dense": "Shimmering highs extending to about 12 kHz."}
        claims = map_report_to_claims(report)
        inferred = " ".join(claim["statement"] for claim in claims["inferred"])
        undetermined = " ".join(claim["statement"] for claim in claims["undetermined"])
        self.assertNotIn("12 kHz", inferred)
        self.assertIn("12 kHz", undetermined)

    def test_relative_descriptor_caption_stays_inferred(self) -> None:
        # Relative perceptual descriptors are legitimate captions and must NOT be suppressed.
        report = base_report()
        report["caption"] = {"dense": "A bright, high-pitched tone with loud transients."}
        claims = map_report_to_claims(report)
        inferred = " ".join(claim["statement"] for claim in claims["inferred"])
        self.assertIn("bright, high-pitched", inferred)

    def test_forbidden_event_label_is_undetermined(self) -> None:
        report = base_report()
        report["events"] = [{"t0": 1.0, "t1": 2.0, "label": "tone panned to the right channel"}]
        claims = map_report_to_claims(report)
        heard = " ".join(claim["statement"] for claim in claims["heard"])
        undetermined = " ".join(claim["statement"] for claim in claims["undetermined"])
        self.assertNotIn("right channel", heard)
        self.assertIn("right channel", undetermined)

    def test_forensic_route_suppresses_interpreted_claims(self) -> None:
        claims = map_report_to_claims(base_report(), claim_permissions=claim_permissions_for("mixed", "/forensic"))
        self.assertEqual(claims["interpreted"], [])
        self.assertTrue(any(c["statement"].startswith("Blocked interpreted claim") for c in claims["undetermined"]))


if __name__ == "__main__":
    unittest.main()
