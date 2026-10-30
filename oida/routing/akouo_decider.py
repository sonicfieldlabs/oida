"""The AKOÚŌ decider: a deterministic, local decision provider that orders the offered moves by
what was heard.

It reads the retained account through AKOÚŌ's router (``akouo_contract.router.route_account``,
computed once into the prepared context as ``akouo_route``) and the host's circuit context (layer,
depth, what this fragment was already heard as, declined or repeated returns, the station rule),
and then follows one explicit rubric:

1. A station that must be left, a declined return or an undetermined reading moves on; a declined
   return first tries the same excerpt on another offered model.
2. A repeated account moves on.
3. Otherwise it focuses: re-listen with the move whose ears match the router's primary ear.
4. Then corrects: re-listen with the move whose ears match the corrective ear.
5. Then sounds, below the recursion limit: a generation whose purpose matches what was heard
   (music for a musical ear, effects otherwise).
6. Then moves on: a new fragment of this source while the station rule prefers staying, otherwise
   another source.
7. Otherwise stops.

It selects only an offered candidate id and copies that candidate's arguments; it adds the route,
the reasoning, the alternative reading and the target layer as non-executable arguments so the host
can record them in the listening circuit. No probabilities are invented.
"""

from __future__ import annotations


from oida.routing.contracts import DecisionProposal, QUESTION_VERSION
from oida.routing.providers import context_digest

#: The AKOÚŌ modes each Oída route preset listens with, read from its skills and passes.
PRESET_EARS: dict[str, set[str]] = {
    "basic": {"acoulogical-object-listening"},
    "field": {"ecological-posthuman-listening", "material-event-listening"},
    "signal": {"signal-inspection-listening", "transductive-media-listening"},
    "music": {"musical-aesthetic-listening"},
    "voice": {"voice-speech-listening"},
    "recall": {"memory-lineage-listening"},
    "remember": {"memory-lineage-listening"},
    "deep": {
        "acoulogical-object-listening",
        "ecological-posthuman-listening",
        "material-event-listening",
        "signal-inspection-listening",
    },
}
#: The AKOÚŌ modes a MOSS listening mode attends to.
MODE_EARS: dict[str, set[str]] = {
    "caption": {"acoulogical-object-listening"},
    "events": {"ecological-posthuman-listening", "material-event-listening"},
    "music": {"musical-aesthetic-listening", "material-event-listening"},
    "speech": {"voice-speech-listening"},
    "transcribe": {"voice-speech-listening"},
}
#: Correctives a listening route can actually perform, in order of preference.
AUDIBLE_CORRECTIVES = (
    "signal-inspection-listening",
    "acoulogical-object-listening",
    "transductive-media-listening",
)
_SPEECH_CONTENT = {"voice", "transcribe"}
_RELISTEN = {"relisten", "analyze"}
_MOVE_ON = {"continue", "switch_source", "branch"}


def candidate_ears(candidate: dict) -> set[str]:
    arguments = candidate.get("arguments") or {}
    ears: set[str] = set()
    preset = arguments.get("route_preset")
    mode = arguments.get("listening_mode")
    if isinstance(mode, str) and mode in MODE_EARS:
        ears |= MODE_EARS[mode]
    elif isinstance(preset, str):
        ears |= PRESET_EARS.get(preset, set())
    return ears


#: Route presets whose passes transcribe speech (voice; deep runs every pass, transcription too).
_SPEECH_PRESETS = {"voice", "deep"}


def _speech_content(candidate: dict) -> bool:
    arguments = candidate.get("arguments") or {}
    return arguments.get("route_preset") in _SPEECH_PRESETS or arguments.get("listening_mode") in _SPEECH_CONTENT


def _purpose(candidate: dict) -> str | None:
    arguments = candidate.get("arguments") or {}
    value = arguments.get("generation_purpose")
    if value in {"music", "sfx"}:
        return value
    model = str(arguments.get("generation_model") or "")
    if "music" in model:
        return "music"
    if "sfx" in model:
        return "sfx"
    return None


class AkouoDecisionProvider:
    provider_id = "akouo"
    kind = "router"
    locality = "local"

    def decide(self, context: dict, questions=None, settings=None) -> DecisionProposal:
        route = context.get("akouo_route")
        if route is None:
            from oida.routing.service import account_route

            route = account_route(context.get("evidence") or [], None, generated=bool(context.get("subject_generated")))
        host = context.get("host_context") or {}
        circuit = host.get("circuit") or {}
        allowed = set(context.get("allowed_actions") or ["stop"])
        candidates = [
            c for c in context.get("candidates") or [] if isinstance(c, dict) and c.get("action") in allowed
        ]
        if not circuit.get("allow_speech_content"):
            candidates = [c for c in candidates if not _speech_content(c)]
        heard = set(circuit.get("heard_modes") or [])
        heard_listening = set(circuit.get("heard_listening_modes") or [])
        relistens = [c for c in candidates if c.get("action") in _RELISTEN]
        move_on = [c for c in candidates if c.get("action") in _MOVE_ON]
        generate = [c for c in candidates if c.get("action") == "generate"]
        confidence = route.get("confidence") or "undetermined"
        primary, corrective = route.get("primary"), route.get("corrective")
        current_source = circuit.get("source_key")

        def fresh(c: dict) -> bool:
            arguments = c.get("arguments") or {}
            mode, preset = arguments.get("listening_mode"), arguments.get("route_preset")
            if mode and mode in heard_listening:
                return False
            if not mode and preset and preset in heard_listening:
                return False
            ears = candidate_ears(c)
            return not (ears and ears <= heard)

        def by_ear(mode: str | None) -> dict | None:
            if not mode:
                return None
            return next((c for c in relistens if mode in candidate_ears(c) and fresh(c)), None)

        declined_model = circuit.get("declined_model")

        def other_model() -> dict | None:
            """A re-listening on a model other than the one that declined."""
            for c in relistens:
                arguments = c.get("arguments") or {}
                model = arguments.get("model_id") or arguments.get("audio_model")
                if model and model != declined_model:
                    return c
            return None

        def leave() -> dict | None:
            stay = [c for c in move_on if (c.get("arguments") or {}).get("source_key") == current_source]
            away = [c for c in move_on if c not in stay]
            if circuit.get("prefer_stay") and not circuit.get("must_move"):
                return (stay or away or [None])[0]
            return (away or stay or [None])[0]

        choice, layer, why, ear = None, "general", "", None
        if circuit.get("must_move"):
            choice = leave()
            why = f"The station rule says to move on ({circuit.get('station_rule') or 'dwell reached'})."
        elif circuit.get("declined"):
            choice = other_model() or leave()
            layer = "focused" if choice in relistens else "general"
            why = "The last return was declined; the same excerpt is tried on another model, or the circuit moves on."
        elif confidence == "undetermined":
            choice = leave()
            why = "The account gives the router nothing to route by; the circuit moves on."
        elif circuit.get("repeated"):
            choice = leave()
            why = "The account repeats the one before it; another fragment may say more."
        if choice is None and not why:
            choice = by_ear(primary)
            if choice is not None:
                layer, why, ear = "focused", f"Focus with the primary ear, {primary}.", primary
            else:
                # The router's corrective may be an ear no listening route hears (critical-political,
                # for one); then the first audible corrective takes its place, and the reason says so.
                for target in (corrective, *AUDIBLE_CORRECTIVES):
                    if target == primary:
                        continue
                    choice = by_ear(target)
                    if choice is not None:
                        layer, ear = "corrective", target
                        why = f"Check the focused reading with the corrective ear, {target}" + (
                            "." if target == corrective else f", in place of {corrective}, which no offered route hears."
                        )
                        break
        if choice is None and not why and generate and int(circuit.get("depth") or 0) < int(circuit.get("max_depth") or 0):
            want = "music" if "music" in (route.get("heard_as") or []) else "sfx"
            choice = next((c for c in generate if _purpose(c) == want), generate[0])
            layer, why = "sounding", f"Answer the account with a {_purpose(choice) or 'generated'} sounding and listen to it in turn."
        if choice is None:
            choice = leave()
            if choice is not None and not why:
                why = "The layers this fragment offers are heard; the circuit moves on."
        if choice is None:
            stop = next((c for c in context.get("candidates") or [] if c.get("action") == "stop"), {"id": "stop"})
            return DecisionProposal(
                context_sha256=context_digest(context),
                action="stop",
                candidate_id=stop.get("id"),
                arguments={**(stop.get("arguments") or {}), "reason": why or "No offered move remains.", "layer": "closing", "route": route},
                question_version=QUESTION_VERSION,
            )
        reasoning = " ".join(part for part in (route.get("reasoning"), why) if part)[:2000]
        return DecisionProposal(
            context_sha256=context_digest(context),
            action=choice["action"],
            candidate_id=choice["id"],
            arguments={
                **(choice.get("arguments") or {}),
                "reason": why or str(choice.get("description") or "Router move"),
                "layer": layer,
                "ear": ear,
                "route": {key: route.get(key) for key in ("primary", "secondary", "corrective", "confidence", "heard_as", "router")},
                "reasoning": reasoning or None,
                "alternative_reading": route.get("alternative_reading"),
            },
            question_version=QUESTION_VERSION,
            requested_model=None,
            actual_model=None,
            probabilities=None,
            abstained=False,
        )
