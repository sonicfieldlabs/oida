from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oida.config import data_dir
from oida.contracts import new_id, now_iso
from oida.memory import AkousmataStore
from oida.privacy import redact_event_audio_for_policy
from oida.storage import write_json_atomic


@dataclass(frozen=True)
class ConversationStore:
    root: Path = field(default_factory=lambda: data_dir() / "sessions" / "conversations")

    def ask(
        self,
        *,
        event: dict[str, Any],
        question: str,
        memory: AkousmataStore,
        conversation_id: str | None = None,
        include_memory: bool = True,
        allow_remote_model: bool = False,
        provider: str = "local_structured",
    ) -> dict[str, Any]:
        question = str(question or "").strip()
        if not question:
            raise ValueError("conversation question is required")
        if not isinstance(event, dict) or not event.get("id"):
            raise ValueError("conversation requires a listening event")

        raw_audio_policy = str(event.get("raw_audio_policy") or "external_ref")
        stored_event = redact_event_audio_for_policy(event, raw_audio_policy)
        conversation = self._load_or_create(conversation_id, stored_event)
        similar = memory.similar_to_event(event, limit=3) if include_memory else []
        turn = _build_turn(
            event=event,
            question=question,
            similar=similar,
            allow_remote_model=allow_remote_model,
            provider=provider,
        )
        turns = list(conversation.get("turns") if isinstance(conversation.get("turns"), list) else [])
        turns.append(turn)
        conversation.update(
            {
                "updated_at": turn["created_at"],
                "event_id": event.get("id"),
                "event": stored_event,
                "raw_audio_policy": raw_audio_policy,
                "turns": turns[-50:],
            }
        )
        self._write(conversation)
        return {
            "version": "0.1",
            "mode": "event_grounded_conversation",
            "conversation_id": conversation["id"],
            "event_id": event.get("id"),
            "raw_audio_policy": "Conversation stores derived event JSON and turn text only; raw audio is not copied.",
            "turn": turn,
            "conversation": {
                "id": conversation["id"],
                "event_id": conversation.get("event_id"),
                "turn_count": len(conversation["turns"]),
                "updated_at": conversation.get("updated_at"),
            },
        }

    def get(self, conversation_id: str) -> dict[str, Any]:
        path = self._path(conversation_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown conversation: {conversation_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_or_create(self, conversation_id: str | None, event: dict[str, Any]) -> dict[str, Any]:
        if conversation_id:
            try:
                return self.get(conversation_id)
            except FileNotFoundError:
                pass
        now = now_iso()
        aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
        return {
            "version": "0.1",
            "id": new_id("conv"),
            "created_at": now,
            "updated_at": now,
            "title": str(aggregate.get("title") or "Listening event conversation"),
            "event_id": event.get("id"),
            "event": event,
            "raw_audio_policy": event.get("raw_audio_policy"),
            "turns": [],
        }

    def _write(self, conversation: dict[str, Any]) -> None:
        write_json_atomic(self._path(str(conversation["id"])), conversation)

    def _path(self, conversation_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(conversation_id)).strip("-") or "conversation"
        return self.root / f"{safe}.json"


def _build_turn(
    *,
    event: dict[str, Any],
    question: str,
    similar: list[dict[str, Any]],
    allow_remote_model: bool,
    provider: str,
) -> dict[str, Any]:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
    features = event.get("features") if isinstance(event.get("features"), dict) else {}

    known_facts = _known_facts(event)
    hypotheses = _hypotheses(aggregate)
    uncertainty = _uncertainty_notes(aggregate)
    evidence = _evidence(event, similar)
    answer = _answer_text(
        question=question,
        aggregate=aggregate,
        routes=routes,
        source=source,
        segment=segment,
        features=features,
        known_facts=known_facts,
        hypotheses=hypotheses,
        uncertainty=uncertainty,
        similar=similar,
    )
    turn = {
        "id": new_id("turn"),
        "created_at": now_iso(),
        "question": question,
        "answer": answer,
        "known_facts": known_facts,
        "hypotheses": hypotheses,
        "evidence": evidence,
        "uncertainty_notes": uncertainty,
        "memory_context": [_memory_context_item(item) for item in similar],
        "provider": provider if provider else "local_structured",
        "remote_model": {
            # Remote model execution is not implemented in this local daemon; responses are
            # always generated from local structured event data. `requested` records intent.
            "enabled": False,
            "requested": bool(allow_remote_model),
            "provider": provider,
            "note": (
                "Remote model execution is not configured in this local daemon; a local structured response was returned."
                if allow_remote_model and provider not in {"", "local_structured"}
                else "Remote model calls are opt-in; this response was generated from local structured event data."
            ),
        },
    }
    return turn


def _answer_text(
    *,
    question: str,
    aggregate: dict[str, Any],
    routes: list[Any],
    source: dict[str, Any],
    segment: dict[str, Any],
    features: dict[str, Any],
    known_facts: list[str],
    hypotheses: list[str],
    uncertainty: list[str],
    similar: list[dict[str, Any]],
) -> str:
    q = question.lower()
    summary = str(aggregate.get("short_summary") or aggregate.get("detailed_summary") or aggregate.get("title") or "The event has no textual summary.")
    parts: list[str] = []

    if any(term in q for term in ["duration", "how long", "length"]):
        duration = segment.get("duration_ms")
        if isinstance(duration, (int, float)):
            parts.append(f"The structured event duration is {float(duration) / 1000:.2f} seconds.")
        else:
            parts.append("The structured event does not include a duration.")
    elif any(term in q for term in ["source", "where", "input", "route from"]):
        label = source.get("label") or "unknown source"
        source_type = source.get("type") or "unknown"
        parts.append(f"The event source is {label} ({source_type}).")
    elif any(term in q for term in ["memory", "similar", "remember", "before"]):
        if similar:
            memory_bits = [
                f"{item.get('trace', {}).get('title') or item.get('trace', {}).get('id')} ({round(float(item.get('score') or 0) * 100)}% DSP similarity)"
                for item in similar[:3]
            ]
            parts.append("Akousmata found similar derived traces: " + "; ".join(memory_bits) + ".")
        else:
            parts.append("No similar Akousmata traces were found from the available derived features.")
    elif any(term in q for term in ["route", "skill", "analysis"]):
        route_bits = []
        for route in routes:
            if isinstance(route, dict):
                route_bits.append(f"{route.get('route_id') or 'route'}: {route.get('summary') or 'no summary'}")
        parts.append("Route summaries: " + ("; ".join(route_bits[:5]) if route_bits else "none available."))
    elif any(term in q for term in ["uncertain", "confidence", "sure", "hypothesis", "guess"]):
        if hypotheses:
            parts.append("The event hypotheses are: " + "; ".join(hypotheses[:4]) + ".")
        if uncertainty:
            parts.append("Uncertainty notes: " + "; ".join(uncertainty[:4]) + ".")
    else:
        parts.append(summary)

    if known_facts:
        parts.append("Known derived facts: " + "; ".join(known_facts[:4]) + ".")
    elif features:
        parts.append("The available evidence is mostly numeric DSP features and route summaries.")
    if hypotheses and not any(term in q for term in ["uncertain", "confidence", "hypothesis", "guess"]):
        parts.append("Hypotheses: " + "; ".join(hypotheses[:3]) + ".")
    parts.append("This answer is grounded in the current structured listening event; it does not run a new audio pass.")
    return " ".join(part for part in parts if part)


def _known_facts(event: dict[str, Any]) -> list[str]:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    facts = [str(item) for item in aggregate.get("signal_facts", []) if item] if isinstance(aggregate.get("signal_facts"), list) else []
    features = event.get("features") if isinstance(event.get("features"), dict) else {}
    for key, label, suffix in [
        ("rmsDbfs", "RMS", " dBFS"),
        ("peakDbfs", "Peak", " dBFS"),
        ("spectralCentroidHz", "Spectral centroid", " Hz"),
        ("onsetDensityPerSec", "Onset density", " per second"),
    ]:
        value = features.get(key)
        if isinstance(value, (int, float)):
            facts.append(f"{label}: {value:.2f}{suffix}")
    return _dedupe(facts)


def _hypotheses(aggregate: dict[str, Any]) -> list[str]:
    raw = aggregate.get("hypotheses")
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            statement = item.get("statement")
            confidence = item.get("confidence")
            if statement:
                values.append(f"{statement} ({confidence})" if confidence else str(statement))
        elif item:
            values.append(str(item))
    return _dedupe(values)


def _uncertainty_notes(aggregate: dict[str, Any]) -> list[str]:
    warnings = aggregate.get("warnings")
    values = [str(item) for item in warnings if item] if isinstance(warnings, list) else []
    values.append("No new audio analysis was run for this answer.")
    return _dedupe(values)


def _evidence(event: dict[str, Any], similar: list[dict[str, Any]]) -> list[dict[str, str]]:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    evidence = [
        {"kind": "event", "label": "summary", "value": str(aggregate.get("short_summary") or aggregate.get("title") or "")},
        {"kind": "source", "label": str(source.get("type") or "source"), "value": str(source.get("label") or "")},
    ]
    for route in routes[:4]:
        if isinstance(route, dict):
            evidence.append({"kind": "route", "label": str(route.get("route_id") or "route"), "value": str(route.get("summary") or "")})
    for item in similar[:3]:
        trace = item.get("trace") if isinstance(item.get("trace"), dict) else {}
        evidence.append({"kind": "memory", "label": str(trace.get("id") or "trace"), "value": str(trace.get("title") or "")})
    return [item for item in evidence if item["value"]]


def _memory_context_item(item: dict[str, Any]) -> dict[str, Any]:
    trace = item.get("trace") if isinstance(item.get("trace"), dict) else {}
    return {
        "trace_id": trace.get("id"),
        "title": trace.get("title"),
        "score": item.get("score"),
        "basis": item.get("basis"),
    }


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
