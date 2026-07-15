from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema
from fastapi.testclient import TestClient

from harness.akouo.command import build_apparatus
from harness.claim_mapper import map_report_to_claims
from oida.covenant import CovenantEngine, parse_covenant
from oida.gateway import GATEWAY_CONTRACT, HOST_PERCEPTION_CONTRACT, harness_host_perception, normalize_host_perception
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

    def test_host_apparatus_replaces_moss_assumptions(self) -> None:
        report = normalize_host_perception(host_payload())
        apparatus = build_apparatus(report)
        claims = map_report_to_claims(report)
        heard = " ".join(item["statement"] for item in claims["heard"])
        inferred = " ".join(item["statement"] for item in claims["inferred"])
        undetermined = " ".join(item["statement"] for item in claims["undetermined"])

        self.assertEqual(apparatus["channels"], 2)
        self.assertIn("wide stereo field", heard)
        self.assertIn("Spectral centroid", inferred)
        self.assertIn("Measurement status is unsupported", undetermined)

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
        self.assertEqual(result["earworm"]["version"], "0.2.2")

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

    def test_gateway_manifest_exposes_both_perception_paths(self) -> None:
        client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
        response = client.get("/gateway")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["contract"], GATEWAY_CONTRACT)
        self.assertEqual(response.json()["components"]["akouo"]["contract"], "akouo/v0.7")
        self.assertEqual(set(response.json()["perception_paths"]), {"oida_owned", "host_supplied"})


if __name__ == "__main__":
    unittest.main()
