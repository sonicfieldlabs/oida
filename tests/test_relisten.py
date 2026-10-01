from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
import wave
from pathlib import Path

from oida.covenant import CovenantStore
from oida.engine_base import EngineResult, MossEngine
from oida.listening_identity import ListeningIdentitySnapshot
from oida.recipes import THINKING_REASONING
from oida.relisten import RELISTEN_CONTRACT, RelistenUnavailable, TargetedRelistener


class _Engine(MossEngine):
    profile = "test-local"

    def __init__(self, *, unavailable_reason: str | None = None) -> None:
        self.unavailable_reason = unavailable_reason
        self.calls: list[tuple[str, str]] = []

    def generate(self, audio_path: str, prompt: str, settings, thinking_budget=None) -> EngineResult:
        self.calls.append((audio_path, prompt))
        return EngineResult(
            text="[1.2-1.8] A short metallic impact is audible; confidence medium.",
            model="MOSS-Audio-4B-Thinking",
            profile=self.profile,
            settings=THINKING_REASONING,
            reasoning_trace="private chain of thought",
            unavailable_reason=self.unavailable_reason,
        )


class _RemoteEngine(_Engine):
    base_url = "https://moss.example.test"


class _SelectableEngine(_Engine):
    def __init__(self) -> None:
        super().__init__()
        self.assignments = {
            "thinking": "default-thinking",
            "targeted_relisten": "default-targeted",
        }
        self.model_during_generate: str | None = None
        self.set_calls: list[tuple[str, str]] = []

    def runtime_status(self) -> dict[str, object]:
        return {"assignments": dict(self.assignments)}

    def set_model(self, model_kind: str, model_id: str) -> None:
        self.set_calls.append((model_kind, model_id))
        self.assignments[model_kind] = model_id

    def generate(self, audio_path: str, prompt: str, settings, thinking_budget=None) -> EngineResult:
        self.model_during_generate = self.assignments[settings.model_kind]
        return super().generate(audio_path, prompt, settings, thinking_budget)


class TargetedRelistenerTests(unittest.TestCase):
    def _event(self, path: Path) -> dict:
        return {
            "id": "evt_anchor",
            "segment": {
                "id": "seg_anchor",
                "data_ref": {"kind": "path", "uri": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None},
            },
            "privacy_mode": "session",
            "raw_audio_policy": "external_ref",
        }

    def test_local_pass_returns_sidecar_without_mutating_event_or_reasoning_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF")
            event = self._event(audio)
            before = copy.deepcopy(event)
            engine = _Engine()
            sidecar = TargetedRelistener(engine).run(
                event=event,
                question="What happens near the middle?",
                conversation_id="conv_1",
                turn_id="turn_1",
                listening_identity_snapshot=ListeningIdentitySnapshot(
                    text="Listen for recurrence.",
                    sha256="1" * 64,
                ),
            )

        self.assertEqual(event, before)
        self.assertEqual(sidecar["contract"], RELISTEN_CONTRACT)
        self.assertEqual(sidecar["base_event_id"], "evt_anchor")
        self.assertEqual(sidecar["segment_hash"], before["segment"]["data_ref"]["sha256"])
        self.assertEqual(sidecar["source_binding"]["status"], "verified")
        self.assertNotIn("reasoning_trace", sidecar)
        self.assertEqual(sidecar["listening_identity"]["sha256"], "1" * 64)
        self.assertEqual(sidecar["listening_identity"]["application"], "available_not_applied")
        self.assertEqual(len(engine.calls), 1)

    def test_changed_or_malformed_original_hash_refuses_before_inference(self):
        for recorded in ("0" * 64, "not-a-hash"):
            with self.subTest(recorded=recorded), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "clip.wav"
                path.write_bytes(b"fixture")
                event = self._event(path)
                event["segment"]["data_ref"]["sha256"] = recorded
                engine = _Engine()
                with self.assertRaises(RelistenUnavailable):
                    TargetedRelistener(engine).run(event=event, question="Is there a pulse?",
                        conversation_id="c", turn_id="t")
                self.assertEqual(engine.calls, [])

    def test_unknown_original_hash_is_not_promoted_to_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.wav"
            path.write_bytes(b"fixture")
            event = self._event(path)
            del event["segment"]["data_ref"]["sha256"]
            sidecar = TargetedRelistener(_Engine()).run(event=event, question="Is there a pulse?",
                conversation_id="c", turn_id="t")
            self.assertIsNone(sidecar["segment_hash"])
            self.assertEqual(sidecar["source_binding"]["status"], "unverified_original")
            self.assertEqual(sidecar["source_binding"]["observed_sha256"], hashlib.sha256(b"fixture").hexdigest())

    def test_source_mutation_during_inference_discards_observation(self):
        for restore in (False, True):
            with self.subTest(restore=restore), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "clip.wav"
                path.write_bytes(b"fixture")
                event = self._event(path)
                class Mutating(_Engine):
                    def generate(self, *args, **kwargs):
                        path.write_bytes(b"changed")
                        if restore:
                            path.write_bytes(b"fixture")
                        return super().generate(*args, **kwargs)
                with self.assertRaises(RelistenUnavailable):
                    TargetedRelistener(Mutating()).run(event=event, question="Is there a pulse?",
                        conversation_id="c", turn_id="t")

    def test_pass_receipts_are_retained_independently_and_bound_into_sidecar_hash(self):
        import json
        from dataclasses import replace

        receipt = {"contract": "oida/pass-provenance/v1", "model": "fixture",
                   "effective_input": {"status": "unknown"}}
        class WithReceipt(_Engine):
            def generate(self, *args, **kwargs):
                return replace(super().generate(*args, **kwargs), pass_provenance=[receipt])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.wav"
            path.write_bytes(b"fixture")
            sidecar = TargetedRelistener(WithReceipt()).run(event=self._event(path),
                question="Is there a pulse?", conversation_id="c", turn_id="t")
        digest = sidecar.pop("sha256")
        self.assertEqual(digest, hashlib.sha256(json.dumps(sidecar, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())
        self.assertEqual(sidecar["pass_provenance"], [receipt])
        sidecar["pass_provenance"][0]["effective_input"]["status"] = "changed"
        self.assertEqual(receipt["effective_input"]["status"], "unknown")
        self.assertNotIn("reasoning_trace", sidecar)

    def test_missing_audio_and_covenant_withholding_are_explicit(self) -> None:
        engine = _Engine()
        missing = self._event(Path("/no/such/audio.wav"))
        with self.assertRaisesRegex(RelistenUnavailable, "unavailable or was released"):
            TargetedRelistener(engine).run(
                event=missing,
                question="What happens?",
                conversation_id="conv_1",
                turn_id="turn_1",
            )

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF")
            event = self._event(audio)
            event["covenant"] = {"withheld": [{"rule": "do_not_reveal", "subject": "transcript"}]}
            with self.assertRaisesRegex(RelistenUnavailable, "withholds transcript"):
                TargetedRelistener(engine).run(
                    event=event,
                    question="What words were said?",
                    conversation_id="conv_1",
                    turn_id="turn_1",
                )

    def test_degraded_stub_result_is_not_treated_as_a_relisten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF")
            with self.assertRaisesRegex(RelistenUnavailable, "runtime is cold"):
                TargetedRelistener(_Engine(unavailable_reason="runtime is cold")).run(
                    event=self._event(audio),
                    question="Is there a pulse?",
                    conversation_id="conv_1",
                    turn_id="turn_1",
                )

    def test_time_range_reaches_prompt_and_fixed_server_alias_needs_no_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF")
            engine = _Engine()
            sidecar = TargetedRelistener(
                engine,
                model_resolver=lambda model_id: "configured-thinking" if model_id == "thinking" else None,
            ).run(
                event=self._event(audio),
                question="Is there a metallic impact?",
                parent_question="What happens in this listening?",
                conversation_id="conv_1",
                turn_id="turn_1",
                model_id="thinking",
                time_range={"start_s": 1.0, "end_s": 2.0},
            )

        self.assertIn("1.000-2.000 seconds", engine.calls[0][1])
        self.assertEqual(sidecar["time_range"], {"start_s": 1.0, "end_s": 2.0})
        self.assertEqual(sidecar["parent_question"], "What happens in this listening?")

    def test_external_moss_endpoint_and_transcript_off_request_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF")
            event = self._event(audio)
            with self.assertRaisesRegex(RelistenUnavailable, "loopback local MOSS"):
                TargetedRelistener(_RemoteEngine()).run(
                    event=event,
                    question="Is there a pulse?",
                    conversation_id="conv_1",
                    turn_id="turn_1",
                )
            with self.assertRaisesRegex(RelistenUnavailable, "transcript permission"):
                TargetedRelistener(_Engine()).run(
                    event=event,
                    question="Return everything word for word",
                    conversation_id="conv_1",
                    turn_id="turn_1",
                )

    def test_selected_checkpoint_applies_to_targeted_role_and_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF")
            engine = _SelectableEngine()
            resolver = {
                "deep-model": "/models/deep-model",
                "default-targeted": "/models/default-targeted",
            }.get

            TargetedRelistener(engine, model_resolver=resolver).run(
                event=self._event(audio),
                question="Is there a metallic impact?",
                conversation_id="conv_1",
                turn_id="turn_1",
                model_id="deep-model",
            )

        self.assertEqual(engine.model_during_generate, "/models/deep-model")
        self.assertEqual(
            engine.set_calls,
            [
                ("targeted_relisten", "/models/deep-model"),
                ("targeted_relisten", "/models/default-targeted"),
            ],
        )
        self.assertEqual(engine.assignments["thinking"], "default-thinking")

    def test_active_max_window_refuses_unsliced_audio_before_moss_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "long.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(b"\0\0" * 16_000 * 2)
            store = CovenantStore(Path(tmp) / "covenants")
            store.save("short", "## rules\n- max window: 1 s\n")
            store.activate("short")
            engine = _Engine()

            with self.assertRaisesRegex(RelistenUnavailable, "max window of 1 seconds"):
                TargetedRelistener(engine, store).run(
                    event=self._event(audio),
                    question="Is there a pulse?",
                    conversation_id="conv_1",
                    turn_id="turn_1",
                )

        self.assertEqual(engine.calls, [])


if __name__ == "__main__":
    unittest.main()
