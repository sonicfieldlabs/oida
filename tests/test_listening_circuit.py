"""The AKOÚŌ decider, the circuit context, declared circuits and the Thinking close."""

from copy import deepcopy

import pytest
import torch

from oida.akousma_bridge import (
    apply_declared_circuit,
    build_akousma_from_listen,
    complete_declared_circuit,
    declared_circuit_errors,
)
from oida.engine_mps import ThinkingBudget, split_reasoning
from oida.routing.akouo_decider import AkouoDecisionProvider, candidate_ears
from oida.routing.contracts import DecisionContext
from oida.routing.service import KNOWN_DECISION_PROVIDERS, account_route, host_context_view

MUSIC = [{"kind": "summary", "ref": "event:e1:summary", "value": "A looped melody over a steady pulse, with a bass drone."}]
FOREST = [{"kind": "summary", "ref": "event:e1:summary", "value": "Birds and wind in a forest soundscape; a river runs underneath."}]
SPOKEN = [{"kind": "transcript", "ref": "event:e1:t", "value": "music music music"}]


def candidates(*, generate=True):
    found = [
        {"id": "relisten_route_0", "action": "relisten", "description": "music route", "arguments": {"route_preset": "music"}},
        {"id": "relisten_route_1", "action": "relisten", "description": "field route", "arguments": {"route_preset": "field"}},
        {"id": "relisten_route_2", "action": "relisten", "description": "signal route", "arguments": {"route_preset": "signal"}},
        {"id": "relisten_route_3", "action": "relisten", "description": "voice route", "arguments": {"route_preset": "voice"}},
        {"id": "relisten_model_0", "action": "relisten", "description": "other model", "arguments": {"route_preset": "basic", "model_id": "thinking"}},
        {"id": "fragment", "action": "continue", "description": "new window here", "arguments": {"source_key": "here"}},
        {"id": "source_1", "action": "switch_source", "description": "another station", "arguments": {"source_key": "there"}},
        {"id": "stop", "action": "stop", "description": "stop", "arguments": {}},
    ]
    if generate:
        found += [
            {"id": "generate_music", "action": "generate", "description": "Small Music", "arguments": {"generation_model": "sm-music", "generation_purpose": "music"}},
            {"id": "generate_sfx", "action": "generate", "description": "Small SFX", "arguments": {"generation_model": "sm-sfx", "generation_purpose": "sfx"}},
        ]
    return found


def context(evidence, **circuit):
    circuit = {"source_key": "here", "layer": "general", "depth": 0, "max_depth": 2, **circuit}
    return {
        "evidence": evidence,
        "candidates": candidates(),
        "allowed_actions": ["continue", "generate", "relisten", "stop", "switch_source"],
        "akouo_route": account_route(evidence, DecisionContext(circuit=circuit).circuit),
        "host_context": {"circuit": circuit},
        "subject_generated": False,
    }


def decide(ctx):
    return AkouoDecisionProvider().decide(ctx)


def test_the_router_reads_the_account_not_the_transcript():
    assert account_route(MUSIC)["primary"] == "musical-aesthetic-listening"
    assert account_route(SPOKEN)["confidence"] == "undetermined"


def test_layers_go_focus_then_correct_then_sound_then_move_on():
    first = decide(context(MUSIC))
    assert (first.candidate_id, first.arguments["layer"]) == ("relisten_route_0", "focused")
    assert first.arguments["route_preset"] == "music" and first.arguments["route"]["primary"] == "musical-aesthetic-listening"
    assert first.arguments["alternative_reading"].startswith("The same excerpt could be heard through")
    heard = ["musical-aesthetic-listening"]
    second = decide(context(MUSIC, heard_modes=heard, layer="focused"))
    assert second.arguments["layer"] == "corrective" and "corrective ear" in second.arguments["reason"]
    heard.append(second.arguments["route"]["corrective"])
    for c in candidates():
        if c["action"] == "relisten":
            heard.extend(candidate_ears(c))
    third = decide(context(MUSIC, heard_modes=sorted(set(heard)), layer="corrective"))
    assert (third.candidate_id, third.arguments["layer"]) == ("generate_music", "sounding")
    fourth = decide(context(MUSIC, heard_modes=sorted(set(heard)), layer="sounding", depth=2))
    assert fourth.candidate_id == "source_1"


def test_a_soundscape_sounds_as_effects_and_staying_is_honoured():
    ctx = context(FOREST, heard_modes=["ecological-posthuman-listening", "material-event-listening", "signal-inspection-listening",
                                        "transductive-media-listening", "acoulogical-object-listening", "musical-aesthetic-listening",
                                        "voice-speech-listening"], heard_listening_modes=[])
    assert decide(ctx).candidate_id == "generate_sfx"
    ctx = context(FOREST, heard_modes=ctx["host_context"]["circuit"]["heard_modes"], depth=2, prefer_stay=True)
    assert decide(ctx).candidate_id == "fragment"


def test_station_rules_declines_repeats_and_silence_move_on():
    moved = decide(context(MUSIC, must_move=True, station_rule="dwell 4 of 2-4"))
    assert moved.candidate_id == "source_1" and "dwell 4 of 2-4" in moved.arguments["reason"]
    declined = decide(context(MUSIC, declined=True))
    assert declined.candidate_id == "relisten_model_0"
    assert decide(context(MUSIC, repeated=True)).candidate_id == "source_1"
    assert decide(context([{"kind": "summary", "ref": "x", "value": ""}])).candidate_id == "source_1"


def test_speech_content_routes_need_the_hosts_permission():
    voiced = [{"kind": "summary", "ref": "x", "value": "A voice speaks; a speaker talks in an interview."}]
    assert decide(context(voiced)).arguments.get("route_preset") != "voice"
    assert decide(context(voiced, allow_speech_content=True)).arguments.get("route_preset") == "voice"


def test_only_offered_and_allowed_moves_are_chosen():
    ctx = context(MUSIC)
    ctx["allowed_actions"] = ["stop"]
    proposal = decide(ctx)
    assert proposal.action == "stop" and proposal.arguments["layer"] == "closing"
    for ctx in (context(MUSIC), context(FOREST, must_move=True)):
        proposal = decide(ctx)
        chosen = next(c for c in ctx["candidates"] if c["id"] == proposal.candidate_id)
        for key, value in chosen["arguments"].items():
            assert proposal.arguments[key] == value


def test_the_provider_is_known_and_offered():
    assert "akouo" in KNOWN_DECISION_PROVIDERS


def test_an_absent_circuit_leaves_the_journaled_host_context_as_it_was():
    legacy = DecisionContext()
    assert "circuit" not in host_context_view(legacy)
    assert host_context_view(DecisionContext(circuit={"layer": "focused"}))["circuit"]["layer"] == "focused"
    with pytest.raises(Exception):
        DecisionContext(circuit={"frame": "field"})


def _circuit():
    return {
        "contract": "earworm/listening-circuit/v1",
        "run": {"session_id": "ses_khipu_1", "source_key": "here"},
        "steps": [
            {
                "step_id": "stp_1", "at": "2026-10-30T03:00:00Z", "move": "relisten_same",
                "from_akousma_id": "akm_previous", "from_listening_id": "lst_x",
                "offered": [{"id": "relisten_route_0", "move": "relisten_same", "action": "relisten"},
                            {"id": "stop", "move": "stop", "action": "stop"}],
                "chosen": "relisten_route_0",
                "decider": {"kind": "router", "provider": "akouo"},
                "route": {"primary": "musical-aesthetic-listening", "confidence": "medium"},
                "layer": "focused", "depth": 0, "outcome": {"status": "executed"},
            }
        ],
    }


def _record():
    return build_akousma_from_listen(
        audio={"asset_id": "cap_1"},
        listening={"oida.listen": {"payload": {"summary": "A looped melody."}}},
    )


def test_a_declared_circuit_is_completed_validated_and_recorded():
    assert declared_circuit_errors(_circuit()) == []
    bad = _circuit()
    bad["steps"][0]["chosen"] = "elsewhere"
    assert declared_circuit_errors(bad) != []
    assert declared_circuit_errors("nope") != []
    record = _record()
    before = deepcopy(_circuit())
    declared = _circuit()
    apply_declared_circuit(record, declared)
    assert declared == before, "the caller's circuit is not changed"
    step = record["extensions"]["earworm_listening_circuit"]["steps"][0]
    first = record["auditum"]["listenings"][0]["listening_id"]
    assert step["to_listening_id"] == first
    from akousma.listening_circuit import circuit_record_errors
    assert circuit_record_errors(record) == []


def test_a_sounding_lands_in_the_record_of_what_was_sounded():
    circuit = _circuit()
    step = circuit["steps"][0]
    step.update(move="sound", layer="sounding", chosen="gen", depth=1)
    step["offered"].append({"id": "gen", "move": "sound", "action": "generate"})
    filled = complete_declared_circuit(circuit, listening_id="lst_1", akousma_id="akm_this")
    assert filled["steps"][0]["to_akousma_id"] == "akm_this"


def test_the_record_builder_takes_a_circuit():
    record = build_akousma_from_listen(
        audio={"asset_id": "cap_2"},
        listening={"oida.listen": {"payload": {"summary": "A looped melody."}}},
        circuit=_circuit(),
    )
    assert record["extensions"]["earworm_listening_circuit"]["run"]["session_id"] == "ses_khipu_1"
    broken = _circuit()
    broken["steps"][0]["layer"] = "sounding"
    with pytest.raises(ValueError, match="declared circuit refused"):
        build_akousma_from_listen(audio={"asset_id": "cap_3"}, listening={"oida.listen": {"payload": {}}}, circuit=broken)


def test_thinking_answers_are_separated_and_unanswered_passes_stay_empty():
    assert split_reasoning("weighing it\n</think>\n\nA hum.") == ("weighing it", "A hum.")
    assert split_reasoning("<think>a</think> b") == ("a", "b")
    assert split_reasoning("x</think>y</think>z") == ("x</think>y", "z")
    assert split_reasoning("still weighing", thinking=True, stop="max_new_tokens") == ("still weighing", "")
    assert split_reasoning("<think>open") == ("open", "")
    assert split_reasoning("A plain answer.") == (None, "A plain answer.")
    assert split_reasoning("A plain answer.", thinking=True, stop="eos") == (None, "A plain answer.")


def test_the_thinking_budget_forces_a_close_then_lets_the_answer_run():
    scores = torch.zeros(1, 12)
    closer = ThinkingBudget(2, 3, [7, 8, 9], [8])
    assert closer(torch.tensor([[1, 1, 5]]), scores).min() == 0
    assert int(closer(torch.tensor([[1, 1, 5, 5, 5]]), scores).argmax()) == 7
    assert int(closer(torch.tensor([[1, 1, 5, 5, 5, 7]]), scores).argmax()) == 8
    assert int(closer(torch.tensor([[1, 1, 5, 5, 5, 7, 8]]), scores).argmax()) == 9
    assert closer(torch.tensor([[1, 1, 5, 5, 5, 7, 8, 9, 3, 3]]), scores).min() == 0
    assert closer.forced
    natural = ThinkingBudget(2, 3, [7, 8, 9], [8])
    assert natural(torch.tensor([[1, 1, 5, 8, 3, 3, 3]]), scores).min() == 0
    assert not natural.forced
    with pytest.raises(ValueError):
        ThinkingBudget(2, 3, [])


def test_a_radio_capture_carries_its_declared_circuit():
    from oida.source_api import AcquisitionRequest

    request = AcquisitionRequest(acquisition_id="acq_1", seconds=10.0, remember=True, declared_circuit=_circuit())
    assert request.declared_circuit["run"]["source_key"] == "here"
    assert AcquisitionRequest(acquisition_id="acq_2", seconds=10.0).declared_circuit is None


def test_heard_presets_count_as_heard_and_the_targeted_ear_is_named():
    first = decide(context(MUSIC))
    assert first.arguments["ear"] == "musical-aesthetic-listening"
    again = decide(context(MUSIC, heard_listening_modes=["music"]))
    assert again.arguments.get("route_preset") != "music"
    assert again.arguments["layer"] == "corrective" and again.arguments["ear"]


def test_a_declined_return_tries_a_model_other_than_the_one_that_declined():
    ctx = context(MUSIC, declined=True, declined_model="thinking")
    ctx["candidates"] = [
        {"id": "deliberate", "action": "relisten", "description": "thinking", "arguments": {"listening_mode": "caption", "model_id": "thinking"}},
        {"id": "brief", "action": "relisten", "description": "instruct", "arguments": {"listening_mode": "caption", "model_id": "instruct"}},
        {"id": "stop", "action": "stop", "description": "stop", "arguments": {}},
    ]
    ctx["allowed_actions"] = ["relisten", "stop"]
    assert decide(ctx).candidate_id == "brief"
