from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aear.config import REPO_ROOT
from aear.contracts import new_id, now_iso
from aear.privacy import redact_event_audio_for_policy
from aear.storage import write_json_atomic

FEATURE_KEYS = [
    "duration_s",
    "sample_rate",
    "channels",
    "peakDbfs",
    "rmsDbfs",
    "spectralCentroidHz",
    "spectralRolloffHz",
    "spectralFlatness",
    "silenceRatio",
    "clippedSampleRatio",
    "onsetDensityPerSec",
    "zeroCrossingRate",
    "integratedLufs",
    "loudnessRangeLu",
    "stereoWidth",
]


@dataclass(frozen=True)
class AkousmataStore:
    root: Path = REPO_ROOT / "akousmata"

    @property
    def traces_dir(self) -> Path:
        return self.root / "traces"

    def remember(self, event: dict[str, Any], *, user_notes: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        similar = self.similar_to_event(event, limit=5)
        event_id = str(event.get("id") or new_id("evt"))
        trace_id = new_id("trace")
        aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
        source = event.get("source") if isinstance(event.get("source"), dict) else {}
        segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
        source_details = source.get("details") if isinstance(source.get("details"), dict) else {}
        segment_metadata = segment.get("metadata") if isinstance(segment.get("metadata"), dict) else {}
        source_route = source_details.get("source_route") if isinstance(source_details.get("source_route"), dict) else None
        if source_route is None and isinstance(segment_metadata.get("source_route"), dict):
            source_route = segment_metadata["source_route"]
        data_ref = segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
        event_tags = event.get("tags") if isinstance(event.get("tags"), list) else []
        merged_tags = _dedupe([str(tag) for tag in event_tags + (tags or []) if tag])
        raw_audio_policy = str(event.get("raw_audio_policy") or "external_ref")
        features = event.get("features") if isinstance(event.get("features"), dict) else {}
        stored_event = redact_event_audio_for_policy(event, raw_audio_policy)
        trace = {
            "schemaVersion": "0.1",
            "id": trace_id,
            "listeningEventId": event_id,
            "createdAt": now_iso(),
            "title": str(aggregate.get("title") or "Listening event"),
            "sourceKind": _source_kind(str(source.get("type") or "file")),
            "sourceLabel": source.get("label"),
            "sourceRoute": source_route,
            "sourceRouteId": source_route.get("route_id") if isinstance(source_route, dict) else None,
            "sourceCaptureScope": source_route.get("capture_scope") if isinstance(source_route, dict) else None,
            "audioRef": data_ref if data_ref.get("uri") and raw_audio_policy in {"saved", "external_ref"} else None,
            "audioStored": raw_audio_policy == "saved",
            "audioPolicy": {
                "rawAudioPolicy": raw_audio_policy,
                "audioStored": raw_audio_policy == "saved",
                "audioRefKind": data_ref.get("kind") if isinstance(data_ref, dict) else None,
                "note": _audio_policy_note(raw_audio_policy),
            },
            "featuresRef": None,
            "features": features,
            "similarityVector": _feature_vector_from_features(features),
            "embeddingRefs": [],
            "summaries": {
                "short": str(aggregate.get("short_summary") or ""),
                "detailed": aggregate.get("detailed_summary"),
                "routeSummaries": _route_summaries(event),
            },
            "tags": merged_tags,
            "userNotes": user_notes or event.get("user_notes"),
            "links": [
                {"type": "similar", "traceId": item["trace"]["id"], "score": item["score"], "basis": "dsp_feature_similarity"}
                for item in similar
            ],
            "privacyMode": _normalize_privacy_mode(event.get("privacy_mode")),
            "retentionPolicy": "keep",
            "rawAudioPolicy": raw_audio_policy,
            "routeIds": list(_route_summaries(event)),
            "event": stored_event,
        }
        write_json_atomic(self._path(trace_id), trace)
        return trace

    def list(
        self,
        query: str | None = None,
        *,
        tag: str | None = None,
        source_kind: str | None = None,
        route: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.traces_dir.exists():
            return []
        traces = []
        for path in sorted(self.traces_dir.glob("*.json"), reverse=True):
            try:
                trace = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if query and _terms(query) and not _matches(trace, query):
                continue
            if tag and tag not in trace.get("tags", []):
                continue
            if source_kind and trace.get("sourceKind") != source_kind:
                continue
            if route and route not in trace.get("routeIds", []) and route not in trace.get("summaries", {}).get("routeSummaries", {}):
                continue
            if not _within_time(trace.get("createdAt"), since=since, until=until):
                continue
            traces.append(trace)
            if limit and len(traces) >= limit:
                break
        return traces

    def get(self, trace_id: str) -> dict[str, Any]:
        path = self._path(trace_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown Akousmata trace: {trace_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def forget(self, trace_id: str) -> dict[str, Any]:
        path = self._path(trace_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown Akousmata trace: {trace_id}")
        path.unlink()
        return {"forgotten": trace_id}

    def export_json(self, **filters: Any) -> dict[str, Any]:
        traces = self.list(**filters)
        return {
            "version": "0.1",
            "exportedAt": now_iso(),
            "trace_count": len(traces),
            "raw_audio_policy": "Akousmata exports trace metadata and event JSON; raw audio is only referenced when trace.audioPolicy permits it.",
            "traces": traces,
        }

    def similar_to_event(self, event: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
        features = event.get("features") if isinstance(event.get("features"), dict) else {}
        vector = _feature_vector_from_features(features)
        if not vector:
            return []
        matches = []
        for trace in self.list(limit=None):
            trace_vector = trace.get("similarityVector")
            if not isinstance(trace_vector, dict):
                trace_vector = _feature_vector_from_features(trace.get("features") if isinstance(trace.get("features"), dict) else {})
            score = _cosine_similarity(vector, trace_vector)
            if score > 0:
                matches.append({"trace": _trace_preview(trace), "score": round(score, 4), "basis": "dsp_feature_similarity"})
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[: max(0, limit)]

    def similar_to_trace(self, trace_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        trace = self.get(trace_id)
        event = trace.get("event") if isinstance(trace.get("event"), dict) else {"features": trace.get("features", {})}
        return [item for item in self.similar_to_event(event, limit=limit + 1) if item["trace"]["id"] != trace_id][:limit]

    def enrich_event(self, event: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
        enriched = copy.deepcopy(event)
        similar = self.similar_to_event(enriched, limit=limit)
        memory = enriched.get("memory") if isinstance(enriched.get("memory"), dict) else {}
        memory["similar_trace_ids"] = [item["trace"]["id"] for item in similar]
        notes = list(memory.get("notes") if isinstance(memory.get("notes"), list) else [])
        if similar:
            notes.append(f"{len(similar)} similar Akousmata trace(s) found from DSP feature similarity.")
        memory["notes"] = _dedupe([str(note) for note in notes])
        memory["similarity"] = similar
        enriched["memory"] = memory
        return enriched

    def _path(self, trace_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", trace_id).strip("-") or "trace"
        return self.traces_dir / f"{safe}.json"


def _normalize_privacy_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"saved", "session", "incognito"}:
        return mode
    # An ephemeral capture that is explicitly remembered is session-scoped, not the most
    # durable "saved" label it was previously coerced to.
    if mode == "ephemeral":
        return "session"
    return "saved"


def _route_summaries(event: dict[str, Any]) -> dict[str, str]:
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    summaries = {}
    for route in routes:
        if isinstance(route, dict):
            route_id = str(route.get("route_id") or route.get("routeId") or "route")
            summaries[route_id] = str(route.get("summary") or "")
    return summaries


def _feature_vector_from_features(features: dict[str, Any]) -> dict[str, float]:
    vector: dict[str, float] = {}
    for key in FEATURE_KEYS:
        value = features.get(key)
        normalized = _normalize_feature(key, value)
        if normalized is not None:
            vector[key] = normalized
    band_energy = features.get("bandEnergy") if isinstance(features.get("bandEnergy"), dict) else {}
    for band, value in band_energy.items():
        if isinstance(value, (int, float)):
            vector[f"bandEnergy.{band}"] = max(0.0, min(1.0, float(value)))
    return vector


def _normalize_feature(key: str, value: Any) -> float | None:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    number = float(value)
    if key == "duration_s":
        return max(0.0, min(1.0, number / 600.0))
    if key == "sample_rate":
        return max(0.0, min(1.0, number / 192_000.0))
    if key == "channels":
        return max(0.0, min(1.0, number / 8.0))
    if key in {"peakDbfs", "rmsDbfs", "integratedLufs"}:
        return max(0.0, min(1.0, (number + 120.0) / 120.0))
    if key in {"spectralCentroidHz", "spectralRolloffHz"}:
        return max(0.0, min(1.0, number / 24_000.0))
    if key == "zeroCrossingRate":
        return max(0.0, min(1.0, number / 10_000.0))
    if key == "onsetDensityPerSec":
        return max(0.0, min(1.0, number / 20.0))
    if key == "loudnessRangeLu":
        return max(0.0, min(1.0, number / 30.0))
    if key == "clippedSampleRatio":
        return max(0.0, min(1.0, number * 100.0))
    return max(0.0, min(1.0, number))


def _cosine_similarity(a: dict[str, float], b: Any) -> float:
    if not isinstance(b, dict):
        return 0.0
    keys = sorted(set(a) & {key for key, value in b.items() if isinstance(value, (int, float))})
    if not keys:
        return 0.0
    dot = sum(a[key] * float(b[key]) for key in keys)
    norm_a = math.sqrt(sum(a[key] ** 2 for key in keys))
    norm_b = math.sqrt(sum(float(b[key]) ** 2 for key in keys))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _trace_preview(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": trace.get("id"),
        "title": trace.get("title"),
        "createdAt": trace.get("createdAt"),
        "sourceKind": trace.get("sourceKind"),
        "sourceLabel": trace.get("sourceLabel"),
        "tags": trace.get("tags", []),
        "summary": (trace.get("summaries") if isinstance(trace.get("summaries"), dict) else {}).get("short"),
        "rawAudioPolicy": trace.get("rawAudioPolicy"),
    }


def _source_kind(source_type: str) -> str:
    return {
        "live_input": "mic",
        "system_output": "system",
        "file": "file",
        "buffer": "mic",
        "generated": "generated",
        "external_stream": "external",
    }.get(source_type, "file")


def _terms(text: str) -> list[str]:
    return [term for term in re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()) if term not in {"the", "and", "this", "that"}]


def _matches(trace: dict[str, Any], query: str) -> bool:
    terms = _terms(query)
    haystack = json.dumps(trace, ensure_ascii=False).lower()
    return all(term in haystack for term in terms)


def _within_time(value: Any, *, since: str | None, until: str | None) -> bool:
    if not since and not until:
        return True
    try:
        current = _parse_time(str(value))
    except ValueError:
        return False
    if since:
        try:
            if current < _parse_time(since):
                return False
        except ValueError:
            return False
    if until:
        try:
            if current > _parse_time(until):
                return False
        except ValueError:
            return False
    return True


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _audio_policy_note(raw_audio_policy: str) -> str:
    if raw_audio_policy == "saved":
        return "Raw audio was explicitly saved with this trace."
    if raw_audio_policy == "external_ref":
        return "Trace keeps an external local path reference; Akousmata did not copy raw audio."
    if raw_audio_policy == "temp":
        return "Trace was derived from a temporary buffer; raw audio is not retained by memory."
    return "Trace stores derived listening data without raw audio."


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
