from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from oida.contracts import new_id, now_iso
from oida.covenant import CovenantStore
from oida.dsp import audio_info
from oida.engine_base import EngineUnavailable, MossEngine
from oida.recipes import TARGETED_RELISTEN_REASONING
from oida.reporting import forbidden_topics_for_text


RELISTEN_CONTRACT = "oida/relisten/v0.1"


class RelistenUnavailable(RuntimeError):
    """A targeted local pass cannot be run without violating its contract."""


@dataclass(frozen=True)
class TargetedRelistener:
    """Run one question-specific local audio pass without changing its event.

    The caller owns the one-pass limit.  This object deliberately has no cloud
    provider hook: raw audio can only reach the already-configured local MOSS
    engine (direct MPS or a loopback SGLang endpoint).
    """

    engine: MossEngine
    covenant_store: CovenantStore | None = None
    model_resolver: Callable[[str], str | None] | None = None
    _model_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    def run(
        self,
        *,
        event: dict[str, Any],
        question: str,
        conversation_id: str,
        turn_id: str,
        model_id: str | None = None,
        time_range: dict[str, float] | None = None,
        allow_speech_content: bool = False,
        parent_question: str | None = None,
    ) -> dict[str, Any]:
        normalized_question = " ".join(str(question or "").split())
        normalized_parent = " ".join(str(parent_question or "").split())
        if not normalized_question:
            raise RelistenUnavailable("targeted re-listening needs a question")
        unsupported = forbidden_topics_for_text(normalized_question)
        if unsupported:
            raise RelistenUnavailable("; ".join(unsupported))
        policy_question = " ".join(value for value in (normalized_parent, normalized_question) if value)
        active_engine = self.covenant_store.engine() if self.covenant_store is not None else None
        if active_engine is not None:
            source = event.get("source") if isinstance(event.get("source"), dict) else {}
            refusal = active_engine.refuse_source(str(source.get("type") or "file"))
            refusal = refusal or active_engine.refuse_quiet_hours()
            if refusal:
                raise RelistenUnavailable("the active listening covenant refuses this new pass")
        blocked = _blocked_covenant_subject(event, normalized_question, self.covenant_store)
        if blocked is None and normalized_parent:
            blocked = _blocked_covenant_subject(event, normalized_parent, self.covenant_store)
        if blocked:
            raise RelistenUnavailable(f"the listening covenant withholds {blocked}")
        if not allow_speech_content and _requests_speech_content(policy_question):
            raise RelistenUnavailable(
                "targeted re-listening cannot reproduce speech content without transcript permission"
            )
        _require_local_engine(self.engine)

        audio_path = _event_audio_path(event)
        if audio_path is None:
            raise RelistenUnavailable("the original local audio is unavailable or was released")
        caps = [
            cap
            for cap in (_historical_max_window(event), _active_max_window(active_engine))
            if cap is not None
        ]
        if caps:
            cap = min(caps)
            info = audio_info(audio_path)
            actual_seconds = (
                float(info.get("durationSeconds") or 0.0)
                if isinstance(info, dict)
                else 0.0
            )
            requested_seconds = (
                float(time_range["end_s"]) - float(time_range["start_s"])
                if time_range is not None
                else actual_seconds
            )
            # The current MOSS adapter receives a path, not a decoded slice.
            # Prompting it to focus on a range does not bound what it hears.
            if (
                actual_seconds <= 0
                or actual_seconds > cap + 1e-6
                or requested_seconds > cap + 1e-6
            ):
                raise RelistenUnavailable(
                    f"targeted re-listening requires audio already bounded to the covenant max window of {cap:g} seconds"
                )

        range_instruction = ""
        if time_range is not None:
            start = float(time_range["start_s"])
            end = float(time_range["end_s"])
            range_instruction = (
                f" Focus the observation on {start:.3f}-{end:.3f} seconds; "
                "the full local clip may still be supplied for acoustic context."
            )
        prompt = (
            "Targeted re-listening pass. Answer only the question below from audible evidence in this clip. "
            "Give a concise observation, include a time range when possible, state uncertainty, and do not infer "
            "speaker identity, exact source identity, location, absolute SPL, stereo image, or content above 8 kHz."
            + (
                " Spoken words may be described only when transcript sharing is explicitly allowed."
                if allow_speech_content
                else " Do not quote, repeat, transcribe, or paraphrase any spoken words."
            )
            + f"{range_instruction}\n\n"
            + f"Question: {normalized_question}"
        )
        # A selected checkpoint is a temporary override on a shared engine.
        # Keep selection, inference, and restoration atomic across concurrent
        # conversation turns, including turns that use the configured default.
        with self._model_lock:
            previous_model: str | None = None
            switched_model = False
            if model_id and self.model_resolver is not None:
                resolved = self.model_resolver(model_id)
                if resolved is None:
                    raise RelistenUnavailable(f"unknown local targeted re-listen model: {model_id}")
                assignments = self.engine.runtime_status().get("assignments")
                supports_selection = isinstance(assignments, dict) and bool(assignments)
                if supports_selection and assignments.get("targeted_relisten"):
                    previous_model = str(assignments["targeted_relisten"])
                if supports_selection:
                    try:
                        self.engine.set_model("targeted_relisten", resolved)
                        switched_model = True
                    except ValueError as exc:
                        raise RelistenUnavailable(str(exc)) from exc
                elif model_id not in {"thinking", "instruct"}:
                    raise RelistenUnavailable(
                        f"the {self.engine.profile} engine does not support selecting checkpoint {model_id!r}"
                    )
            try:
                result = self.engine.generate(str(audio_path), prompt, TARGETED_RELISTEN_REASONING)
            except EngineUnavailable as exc:
                raise RelistenUnavailable(str(exc)) from exc
            finally:
                if switched_model and previous_model and self.model_resolver is not None:
                    restore = self.model_resolver(previous_model) or previous_model
                    try:
                        self.engine.set_model("targeted_relisten", restore)
                    except ValueError:
                        pass
        if result.unavailable_reason:
            raise RelistenUnavailable(result.unavailable_reason)
        observation = str(result.text or "").strip()
        if not observation:
            raise RelistenUnavailable(result.unavailable_reason or "the local listening model returned no observation")
        if not allow_speech_content and _looks_like_speech_content(observation):
            raise RelistenUnavailable(
                "the local re-listening result may contain speech content, so it was withheld"
            )

        segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
        data_ref = segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
        segment_ref = str(segment.get("id") or data_ref.get("sha256") or event.get("id") or "segment")
        sidecar = {
            "contract": RELISTEN_CONTRACT,
            "id": new_id("relisten"),
            "base_event_id": event.get("id"),
            "segment_ref": segment_ref,
            "segment_hash": data_ref.get("sha256"),
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "question": normalized_question,
            "parent_question": normalized_parent or None,
            "time_range": dict(time_range) if time_range is not None else None,
            "engine": result.profile,
            "model": result.model,
            "observation": observation,
            "limitations": [
                "One local targeted pass was run; the original listening event was not edited.",
                "MOSS-Audio receives 16 kHz mono audio, so stereo, content above 8 kHz, and absolute physical level are outside this observation.",
            ],
            "created_at": now_iso(),
        }
        sidecar["sha256"] = hashlib.sha256(
            json.dumps(sidecar, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return sidecar


def _require_local_engine(engine: MossEngine) -> None:
    base_url = getattr(engine, "base_url", None)
    if base_url is None:
        return
    if not isinstance(base_url, str):
        raise RelistenUnavailable("targeted re-listening requires a loopback local MOSS engine")
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise RelistenUnavailable("targeted re-listening requires a loopback local MOSS engine")
    if host == "localhost":
        return
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise RelistenUnavailable("targeted re-listening requires a loopback local MOSS engine")


def _active_max_window(engine: Any | None) -> float | None:
    if engine is None:
        return None
    cap = engine.max_window_seconds()
    return float(cap) if cap is not None and math.isfinite(float(cap)) and float(cap) > 0 else None


def _historical_max_window(event: dict[str, Any]) -> float | None:
    covenant = event.get("covenant") if isinstance(event.get("covenant"), dict) else {}
    applied = covenant.get("rules_applied") if isinstance(covenant.get("rules_applied"), list) else []
    caps: list[float] = []
    for raw in applied:
        token = str(raw)
        if not token.startswith("max_window:"):
            continue
        try:
            cap = float(token.split(":", 1)[1])
        except ValueError:
            continue
        if math.isfinite(cap) and cap > 0:
            caps.append(cap)
    return min(caps) if caps else None


def _event_audio_path(event: dict[str, Any]) -> Path | None:
    segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
    data_ref = segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
    uri = data_ref.get("uri")
    # Listening events created from host/file routes may label an existing
    # local file as either ``path`` or ``external``.  In both cases we still
    # require resolution to a real local file; URLs and released references
    # cannot reach the local MOSS engine.
    if data_ref.get("kind") not in {"path", "external"} or not isinstance(uri, str) or not uri.strip():
        return None
    if "://" in uri:
        return None
    candidate = Path(uri).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _blocked_covenant_subject(
    event: dict[str, Any], question: str, covenant_store: CovenantStore | None
) -> str | None:
    block = event.get("covenant") if isinstance(event.get("covenant"), dict) else {}
    subjects: set[str] = set()
    for entry in block.get("withheld") or []:
        if isinstance(entry, dict) and entry.get("subject"):
            subjects.add(str(entry["subject"]))
    for applied in block.get("rules_applied") or []:
        if isinstance(applied, str) and ":" in applied:
            subjects.add(applied.split(":", 1)[1])

    if covenant_store is not None:
        active = covenant_store.active()
        if active is not None:
            for rule in active.rules:
                if rule.get("verb") in {"do_not_reveal", "ignore"}:
                    subjects.update(str(value) for value in rule.get("subjects") or [])

    free_form_sensitive = {
        "transcript",
        "speech",
        "speaker-identity",
        "affect",
        "location",
        "song-identity",
        "events",
        "spectral-detail",
        "music",
    }
    blocked = sorted(subjects & free_form_sensitive)
    if blocked:
        # A MOSS re-listen is free-form text. It cannot prove that an unnamed
        # withheld class is absent, so the strongest safe policy is no new
        # free-form pass whenever one of those classes is governed.
        return blocked[0]

    lowered = question.lower()
    keywords = {
        "transcript": ("transcript", "what was said", "words", "quote", "speech"),
        "speech": ("speech", "voice", "speaker", "said", "words"),
        "speaker-identity": ("who", "speaker", "identity", "name"),
        "affect": ("emotion", "affect", "mood", "feeling"),
        "location": ("where", "location", "place", "address"),
        "song-identity": ("song", "track", "artist", "title"),
        "events": ("event", "happened", "occurs", "timeline"),
        "spectral-detail": ("spectrum", "spectral", "frequency", "hertz", "khz"),
        "music": ("music", "song", "instrument", "melody"),
    }
    for subject in sorted(subjects):
        if any(term in lowered for term in keywords.get(subject, (subject,))):
            return subject
    return None


def _requests_speech_content(value: str) -> bool:
    lowered = value.lower()
    return any(
        term in lowered
        for term in (
            "transcript",
            "what was said",
            "what words",
            "words spoken",
            "spoken words",
            "audible phrase",
            "every phrase",
            "exact words",
            "exact wording",
            "exact sentence",
            "repeat the sentence",
            "repeat exactly",
            "quote",
            "verbatim",
            "transcribe",
            "utterance",
            "dialogue",
            "read back",
            "word for word",
        )
    )


def _looks_like_speech_content(value: str) -> bool:
    lowered = value.lower()
    if _requests_speech_content(lowered):
        return True
    return bool(
        re.search(
            r"\b(?:person|speaker|someone|somebody|voice)\s+(?:says?|said|speaks?|spoke|utters?|uttered)\b",
            lowered,
        )
    )
