import json
from oida.situated_listener import Move, Settings
from oida.reasoning_context import retained_event
from oida.reasoning.evidence import EvidencePacketBuilder
import pytest


def test_legacy_move_and_settings_remain_valid():
    move = Move(action="stop", reason="No evidence", evidence_refs=["a"])
    assert move.segment is None and move.analysis_tasks is None
    assert (
        not Settings().rules.adaptive_analysis and not Settings().rules.adaptive_windows
    )
    with pytest.raises(ValueError):
        Move(action="shell", reason="bad", evidence_refs=["a"])
    with pytest.raises(ValueError):
        Move(
            action="relisten",
            reason="bad",
            evidence_refs=["a"],
            segment={"start_seconds": 0, "seconds": 61},
        )


def test_specialist_packet_is_attributed_bounded_and_path_free():
    record = {
        "akousma_id": "akm_abc",
        "auditum": {},
        "listening": {
            "oida.listen": {
                "payload": {
                    "specialist_evidence": [
                        {
                            "status": "complete",
                            "task": "transcribe",
                            "evidence": {
                                "deployment_id": "qwen-pinned",
                                "model_revision": "abc123",
                                "result": {
                                    "status": "hypotheses",
                                    "text": "Hola /private/secret.wav https://secret.test",
                                },
                                "files": {"/private/secret": "ignore"},
                            },
                        }
                    ]
                }
            }
        },
    }
    before = json.dumps(record, sort_keys=True)
    event = retained_event(record)
    builder = EvidencePacketBuilder()
    hidden = builder.build(event=event, question="Next?", include_transcript=False)
    assert not any(i.kind == "transcript" for i in hidden.items)
    shown = builder.build(event=event, question="Next?", include_transcript=True)
    item = next(i for i in shown.items if i.kind == "transcript")
    assert item.source == "qwen-pinned" and item.category == "interpreted"
    assert (
        "/private/" not in item.model_dump_json()
        and "secret.test" not in item.model_dump_json()
    )
    assert json.dumps(record, sort_keys=True) == before


def test_malformed_or_disabled_local_gateway_does_not_replace_builtins(
    tmp_path, monkeypatch
):
    from oida.reasoning.registry import build_provider_registry

    monkeypatch.setenv("OIDA_LOCAL_REASONING_CONFIG", str(tmp_path / "missing"))
    registry = build_provider_registry()
    assert (
        registry.get("local_structured") is not None
        and registry.get("ollama") is not None
    )
    assert registry.get("local_ecology") is None
