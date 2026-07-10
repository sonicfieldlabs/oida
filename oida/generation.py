from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oida.config import data_dir
from oida.contracts import new_id, now_iso
from oida.privacy import redact_event_audio_for_policy
from oida.storage import write_json_atomic


@dataclass(frozen=True)
class GenerationStore:
    root: Path = field(default_factory=lambda: data_dir() / "generations")

    @property
    def records_dir(self) -> Path:
        return self.root / "records"

    def create_prompt(
        self,
        event: dict[str, Any],
        *,
        intent: str = "transform",
        prompt: str | None = None,
        negative_prompt: str | None = None,
        adapter: str = "prompt_only",
        duration_s: float | None = None,
        generate: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(event, dict) or not event.get("id"):
            raise ValueError("generation prompt requires a listening event")
        created_at = now_iso()
        raw_audio_policy = str(event.get("raw_audio_policy") or "external_ref")
        stored_event = redact_event_audio_for_policy(event, raw_audio_policy)
        persistent = event.get("privacy_mode") != "incognito"
        prompt_text = str(prompt).strip() if prompt else _prompt_from_event(event, intent=intent, duration_s=duration_s)
        negative_text = str(negative_prompt).strip() if negative_prompt else _negative_prompt_from_event(event)
        record = {
            "version": "0.1",
            "id": new_id("gen"),
            "created_at": created_at,
            "updated_at": created_at,
            "persistent": persistent,
            "source_event_id": event.get("id"),
            "source_event": stored_event,
            "status": "adapter_required" if generate and adapter != "prompt_only" else "prompt_ready",
            "adapter": adapter or "prompt_only",
            "adapter_status": {
                "configured": False,
                "generation_enabled": False,
                "note": "Prompt-only bridge. Configure a separate generator adapter to render audio.",
            },
            "intent": intent or "transform",
            "prompt": prompt_text,
            "negative_prompt": negative_text,
            "params": {
                "duration_s": _duration_from_event(event, duration_s),
                "source_event_id": event.get("id"),
                "raw_audio_policy": raw_audio_policy,
            },
            "source_summary": _source_summary(event),
            "evidence": _evidence(event),
            "output_audio": None,
            "generated_audio_policy": "No generated audio is stored by the prompt-only adapter.",
            "raw_audio_policy": "Generation records store derived event JSON and prompt text only; source raw audio is not copied.",
            "relisten": None,
            "notes": [
                "MOSS-Audio is used only as the listening/understanding side of the workflow.",
                "Audio generation is optional and delegated to a separate adapter.",
            ],
        }
        if persistent:
            self._write(record)
        return record

    def list(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.records_dir.exists():
            return []
        records = []
        for path in self.records_dir.glob("*.json"):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        records.sort(key=lambda record: str(record.get("created_at") or record.get("updated_at") or ""), reverse=True)
        return records[:limit] if limit else records

    def get(self, generation_id: str) -> dict[str, Any]:
        path = self._path(generation_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown generation record: {generation_id}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid generation record JSON: {generation_id}") from exc

    def attach_relisten(
        self,
        generation_id: str,
        *,
        output_path: str,
        generated_event: dict[str, Any],
        route_comparison: dict[str, Any] | None,
        persist: bool = True,
    ) -> dict[str, Any]:
        record = self.get(generation_id)
        record["updated_at"] = now_iso()
        record["persistent"] = persist
        record["status"] = "relistened"
        record["output_audio"] = {
            "kind": "path",
            "uri": output_path,
            "raw_audio_policy": "external_ref",
            "note": "Generated audio is referenced for re-listening; it is not copied into the generation record.",
        }
        record["generated_audio_policy"] = "Generated audio is externally referenced only."
        record["relisten"] = {
            "listening_event": redact_event_audio_for_policy(generated_event),
            "route_comparison": route_comparison,
            "updated_at": record["updated_at"],
        }
        if persist:
            self._write(record)
        return record

    def _write(self, record: dict[str, Any]) -> None:
        write_json_atomic(self._path(str(record["id"])), record)

    def _path(self, generation_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(generation_id)).strip("-") or "generation"
        return self.records_dir / f"{safe}.json"


def _prompt_from_event(event: dict[str, Any], *, intent: str, duration_s: float | None) -> str:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    title = str(aggregate.get("title") or "listening event")
    summary = str(aggregate.get("short_summary") or aggregate.get("detailed_summary") or title)
    tags = event.get("tags") if isinstance(event.get("tags"), list) else []
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    route_summaries = [
        str(route.get("summary"))
        for route in routes
        if isinstance(route, dict) and route.get("summary")
    ][:4]
    duration = _duration_from_event(event, duration_s)
    tag_text = ", ".join(str(tag) for tag in tags[:6]) if tags else "machine listening texture"
    route_text = "; ".join(route_summaries) if route_summaries else summary
    if intent == "variation":
        verb = "Create a variation of"
    elif intent == "counterpoint":
        verb = "Create a contrasting response to"
    elif intent == "sonification":
        verb = "Create a sonification inspired by"
    else:
        verb = "Create an audio transformation inspired by"
    return (
        f"{verb} the source event \"{title}\" as a {duration:.1f} second sound piece. "
        f"Preserve these derived listening qualities: {summary}. "
        f"Use this route evidence as material guidance: {route_text}. "
        f"Texture tags: {tag_text}. "
        "Emphasize audible structure, gesture, density, and temporal evolution rather than speech-assistant cues."
    )


def _negative_prompt_from_event(event: dict[str, Any]) -> str:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    warnings = aggregate.get("warnings") if isinstance(aggregate.get("warnings"), list) else []
    warning_text = "; ".join(str(item) for item in warnings[:3])
    base = "Avoid humanoid assistant voices, mascot cues, exaggerated cinematic impacts, and unsupported ultrasonic or stereo claims."
    return f"{base} Respect uncertainty from the source event: {warning_text}." if warning_text else base


def _duration_from_event(event: dict[str, Any], override: float | None) -> float:
    if isinstance(override, (int, float)) and override > 0:
        return max(0.5, min(600.0, float(override)))
    segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
    duration_ms = segment.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        return max(0.5, min(600.0, float(duration_ms) / 1000.0))
    return 10.0


def _source_summary(event: dict[str, Any]) -> dict[str, Any]:
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    return {
        "title": aggregate.get("title"),
        "summary": aggregate.get("short_summary") or aggregate.get("detailed_summary"),
        "source_label": source.get("label"),
        "source_type": source.get("type"),
    }


def _evidence(event: dict[str, Any]) -> list[dict[str, str]]:
    evidence = []
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    if aggregate.get("short_summary"):
        evidence.append({"kind": "summary", "value": str(aggregate["short_summary"])})
    signal_facts = aggregate.get("signal_facts") if isinstance(aggregate.get("signal_facts"), list) else []
    for fact in signal_facts:
        evidence.append({"kind": "signal_fact", "value": str(fact)})
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    for route in routes:
        if isinstance(route, dict) and route.get("summary"):
            evidence.append({"kind": str(route.get("route_id") or "route"), "value": str(route["summary"])})
    return evidence[:8]
