from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema
import akousma
from fastapi.testclient import TestClient

from harness.akouo.command import build_apparatus
from harness.akouo.loader import AkouoLoader
from harness.claim_mapper import map_report_to_claims
from oida.covenant import CovenantEngine, parse_covenant
from oida.gateway import GATEWAY_CONTRACT, HOST_PERCEPTION_CONTRACT, harness_host_perception, normalize_host_perception
from oida.listening_identity import LISTENING_IDENTITY_CONTRACT, ListeningIdentityStore
from oida.memory import AkousmataStore
from oida.server import create_app


def host_payload() -> dict[str, object]:
    return {
        "contract": HOST_PERCEPTION_CONTRACT,
        "host": {
            "id": "codex",
            "model": "audio-capable-fixture",
            "session_id": "session-fixture",
            "audio_input_capable": True,
        },
        "source": {
            "label": "attached harbor recording",
            "type": "file",
            "duration_s": 12.5,
            "sample_rate": 48000,
            "channels": 2,
            "audio_available_to_oida": False,
        },
        "apparatus": {
            "substrate": "host_audio_model",
            "sample_rate_hz": 48000,
            "channels": 2,
            "bandwidth_limit_hz": 24000,
            "known_blind_spots": ["The host does not expose its resampler."],
        },
        "listening_context": {
            "contract": "akouo/listening-context/v2",
            "position": {
                "relation_to_object": "host model inspecting an attached recording",
                "limitations": ["The gateway does not receive the raw audio."],
            },
            "apertures": [{
                "id": "host-audio",
                "kind": "direct_audio",
                "status": "available",
                "limits": ["Host preprocessing is opaque."],
            }],
            "auditory_scales": ["event", "scene"],
            "sources_of_listening": ["audio", "model"],
            "participants": [{
                "id": "codex",
                "type": "agent",
                "role": "host perceptual listener",
                "report_ref": "#/observations",
            }],
            "action_authority": {
                "mode": "execute_scoped",
                "scopes": ["memory.remember"],
                "requires_confirmation": False,
                "reversible": True,
            },
            "honest_absences": [],
            "listening_passes": [{
                "id": "pass-host-fixture",
                "listener_id": "codex",
                "route": ["/listen", "acoulogical-object-listening"],
                "started_at": "2026-07-27T00:00:00Z",
                "completed_at": "2026-07-27T00:00:01Z",
                "moment": {"relation": "past_capture", "scales": ["event", "scene"]},
                "source_refs": ["#/source", "#/observations"],
                "claim_refs": ["#/observations/0", "#/observations/1"],
                "decision_refs": ["decision-host-input"],
                "influenced_by": [],
            }],
            "route_decisions": [{
                "id": "decision-host-input",
                "gate": "input",
                "outcome": "proceed",
                "subject": "attached harbor recording",
                "reason": "The host declared an audio-capable input and a bounded report.",
                "decided_at": "2026-07-27T00:00:00Z",
                "authority": {
                    "mode": "execute_scoped",
                    "actor": "codex",
                    "requires_confirmation": False,
                    "reversible": True,
                },
            }],
        },
        "observations": [
            {
                "statement": "A repeating metallic impact is audible in a wide stereo field.",
                "category": "heard",
                "confidence": "medium",
                "source": "model",
                "time_range": {"start_s": 2.0, "end_s": 7.0},
            },
            {
                "statement": "Spectral centroid is 1200 Hz.",
                "category": "measured",
                "confidence": "medium",
                "source": "model",
            },
        ],
        "uncertainty": ["The impacting object is unknown."],
    }


class GatewayContractTests(unittest.TestCase):
    def test_fixture_validates_against_host_schema(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "oida" / "schemas" / "host-perception.schema.json"
        jsonschema.Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(host_payload())
        AkouoLoader().validate("listening-context", host_payload()["listening_context"])

    def test_legacy_v1_host_context_remains_readable(self) -> None:
        payload = host_payload()
        payload["listening_context"] = {"contract": "akouo/listening-context/v1"}
        schema_path = Path(__file__).resolve().parents[1] / "oida" / "schemas" / "host-perception.schema.json"
        jsonschema.Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(payload)

    def test_host_apparatus_replaces_moss_assumptions(self) -> None:
        report = normalize_host_perception(host_payload())
        apparatus = build_apparatus(report)
        claims = map_report_to_claims(report)
        heard = " ".join(item["statement"] for item in claims["heard"])
        inferred = " ".join(item["statement"] for item in claims["inferred"])
        undetermined = " ".join(item["statement"] for item in claims["undetermined"])

        self.assertEqual(apparatus["channels"], 2)
        self.assertEqual(heard, "")
        self.assertIn("wide stereo field", inferred)
        self.assertIn("Spectral centroid", inferred)
        self.assertIn("Measurement status is unsupported", undetermined)

    def test_separately_attributed_human_hearing_survives_with_its_participant_and_pass(self) -> None:
        payload = host_payload()
        context = payload["listening_context"]
        context["participants"].append({
            "id": "person-local-1",
            "type": "human",
            "role": "human listener",
            "standing": "listener",
            "report_ref": "#/observations/0",
        })
        context["listening_passes"].append({
            "id": "pass-human-1",
            "listener_id": "person-local-1",
            "route": ["human-report"],
            "started_at": "2026-07-27T00:00:00Z",
            "completed_at": "2026-07-27T00:00:02Z",
            "moment": {"relation": "past_capture", "scales": ["event"]},
            "source_refs": ["#/source"],
            "claim_refs": ["#/observations/0"],
            "decision_refs": ["decision-host-input"],
            "influenced_by": [],
        })
        payload["observations"] = [{
            "statement": "I heard a brief metallic impact.",
            "category": "heard",
            "confidence": "high",
            "source": "human",
            "listening_pass_id": "pass-human-1",
        }]

        report = normalize_host_perception(payload)
        claims = map_report_to_claims(report)

        self.assertEqual(claims["heard"][0]["listening_pass_id"], "pass-human-1")
        participants = report["listening_context"]["participants"]
        self.assertIn("person-local-1", {item["id"] for item in participants})
        passes = report["listening_context"]["listening_passes"]
        self.assertIn("pass-human-1", {item["id"] for item in passes})

    def test_human_source_without_a_resolving_human_participant_is_not_heard(self) -> None:
        payload = host_payload()
        payload["observations"] = [{
            "statement": "I heard a brief metallic impact.",
            "category": "heard",
            "confidence": "high",
            "source": "human",
            "listening_pass_id": "pass-host-fixture",
        }]

        report = normalize_host_perception(payload)
        claims = map_report_to_claims(report)

        self.assertEqual(claims["heard"], [])
        self.assertEqual(report["host_observations"][0]["category"], "inferred")

    def test_host_harness_builds_event_without_exposing_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AkousmataStore(root=Path(tmp) / "memory")
            result = harness_host_perception(host_payload(), memory=memory, remember=True)

        event = result["listening_event"]
        self.assertEqual(result["contract"], GATEWAY_CONTRACT)
        self.assertEqual(event["segment"]["data_ref"]["kind"], "none")
        self.assertEqual(event["source"]["platform"], "codex")
        self.assertIsNotNone(result["trace"])
        self.assertEqual(result["trace"]["earworm"]["session"]["app_id"], "oida.akousmata")
        self.assertEqual(result["earworm"]["version"], "0.7.0")
        context = event["listening_context"]
        self.assertEqual(context["contract"], "akouo/listening-context/v2")
        self.assertEqual(context["action_authority"]["mode"], "observe_only")
        self.assertTrue(context["action_authority"]["requires_confirmation"])
        self.assertIn("raw audio at the OÍDA gateway", {item["subject"] for item in context["honest_absences"]})
        self.assertEqual(event["contract"], "oida/listening-event/v0.3")
        self.assertEqual(event["listening_passes"], context["listening_passes"])
        self.assertEqual(event["route_decisions"], context["route_decisions"])
        self.assertTrue(event["listening_provenance"]["listening_sources"])
        event_types = [item["type"] for item in result["earworm"]["session"]["events"]]
        self.assertIn("listening.report.created", event_types)
        self.assertIn("listening.action.executed", event_types)

    def test_host_harness_attributes_only_a_matching_declared_identity_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = ListeningIdentityStore(Path(tmp) / "identity")
            identity.save("Listen as a guest. Keep origin open.")
            snapshot = identity.snapshot()
            payload = host_payload()
            payload["listening_identity"] = {
                "contract": LISTENING_IDENTITY_CONTRACT,
                "sha256": snapshot.sha256,
                "applied": True,
            }

            result = harness_host_perception(
                payload,
                listening_identity_snapshot=snapshot,
            )

        block = result["listening_event"]["listening_identity"]
        self.assertEqual(block["application"], "host_declared")
        self.assertEqual(block["applied_to"], ["host_perception"])
        self.assertFalse(block["content_included"])
        self.assertNotIn("Listen as a guest", json.dumps(result))

    def test_ignore_speech_removes_host_transcript_observation_and_caption(self) -> None:
        payload = host_payload()
        payload["observations"] = [
            {
                "statement": "The speaker says launch at dawn.",
                "category": "heard",
                "confidence": "high",
                "source": "transcript",
                "speech_content": True,
            },
            {
                "statement": "A steady 440 Hz tone is measured.",
                "category": "measured",
                "confidence": "high",
                "source": "dsp",
            },
        ]
        covenant = CovenantEngine(parse_covenant("## rules\n- ignore: speech\n"))

        result = harness_host_perception(payload, covenant_engine=covenant)

        serialized = json.dumps(result)
        self.assertNotIn("launch at dawn", serialized)
        self.assertEqual(result["perception_report"]["host_observations"][0]["source"], "dsp")
        self.assertIsNone(result["perception_report"]["caption"]["dense"])
        subjects = {item["subject"] for item in result["listening_event"]["covenant"]["withheld"]}
        self.assertIn("speech", subjects)

    def test_host_refusal_is_a_decision_not_a_listening_event(self) -> None:
        payload = host_payload()
        payload["source"]["type"] = "system_output"
        covenant = CovenantEngine(parse_covenant("## rules\n- do not listen: system output\n"))

        result = harness_host_perception(payload, covenant_engine=covenant)

        self.assertEqual(result["outcome"], "refused")
        self.assertIsNone(result["listening_event"])
        self.assertIsNone(result["perception_report"])
        self.assertNotIn("audio", result["decision_record"])
        self.assertEqual(akousma.validation_errors(result["decision_record"]), [])
        schema_path = Path(__file__).resolve().parents[1] / "oida" / "schemas" / "route-outcome.schema.json"
        jsonschema.Draft202012Validator(
            json.loads(schema_path.read_text(encoding="utf-8")),
            registry=AkouoLoader().schema_registry(),
        ).validate(result["route_outcome"])

    def test_gateway_endpoint_accepts_host_perception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"OIDA_DATA_DIR": tmp, "OIDA_AUDIO_DIR": str(Path(tmp) / "audio")},
            clear=False,
        ):
            client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
            response = client.post(
                "/gateway/harness",
                json={"perception": host_payload(), "route_preset": "basic", "remember": True},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["perception_path"], "host_supplied")
        self.assertEqual(response.json()["listening_event"]["source"]["platform"], "codex")
        self.assertEqual(response.json()["earworm"]["protocol"], "earworm")

    def test_listening_event_validates_against_published_schemas(self) -> None:
        result = harness_host_perception(host_payload())
        root = Path(__file__).resolve().parents[1]
        event_schema = json.loads((root / "oida" / "schemas" / "listening-event.schema.json").read_text())
        jsonschema.Draft202012Validator(
            event_schema,
            registry=AkouoLoader().schema_registry(),
        ).validate(result["listening_event"])

    def test_gateway_manifest_exposes_both_perception_paths(self) -> None:
        client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
        response = client.get("/gateway")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["contract"], GATEWAY_CONTRACT)
        self.assertEqual(response.json()["components"]["akouo"]["contract"], "akouo/v0.9")
        self.assertEqual(
            response.json()["memory_accounts"],
            {
                "separate_human_and_machine_records": True,
                "machine_core_immutable": True,
                "human_revisions": "additive_new_record",
                "classification_source": "auditum.listenings[].listener_type",
                "notes_imply_heard": False,
                "library": "/library/",
            },
        )
        self.assertEqual(set(response.json()["perception_paths"]), {"oida_owned", "host_supplied"})
        self.assertEqual(
            set(response.json()["schemas"]),
            {"host_perception", "listening_event", "listening_context", "route_outcome"},
        )


if __name__ == "__main__":
    unittest.main()
