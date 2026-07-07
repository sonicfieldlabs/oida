from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oida.config import data_dir
from oida.contracts import new_id, now_iso
from oida.privacy import redact_event_audio_for_policy
from oida.raw_audio import delete_upload_paths
from oida.storage import write_json_atomic

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
MIN_SIMILARITY_SHARED_FEATURES = 3


@dataclass(frozen=True)
class AkousmataStore:
    root: Path = field(default_factory=lambda: data_dir() / "akousmata")
    _trace_cache: dict[str, tuple[int, int, dict[str, Any]]] = field(default_factory=dict, init=False, repr=False, compare=False)

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
        trace["earworm"] = _earworm_surface(trace, event)
        trace_path = self._path(trace_id)
        write_json_atomic(trace_path, trace)
        self._cache_trace(trace_path, trace)
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
        for path in self.traces_dir.glob("*.json"):
            trace = self._load_trace(path)
            if trace is None:
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
        # filenames are trace_<uuid4>.json, so glob order is arbitrary; recency
        # has to come from the record itself or ?limit returns random traces
        traces.sort(key=lambda trace: str(trace.get("createdAt") or ""), reverse=True)
        return traces[:limit] if limit else traces

    def get(self, trace_id: str) -> dict[str, Any]:
        path = self._path(trace_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown Akousmata trace: {trace_id}")
        trace = self._load_trace(path)
        if trace is None:
            raise ValueError(f"invalid Akousmata trace JSON: {trace_id}")
        return trace

    def forget(self, trace_id: str) -> dict[str, Any]:
        path = self._path(trace_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown Akousmata trace: {trace_id}")
        trace = self._load_trace(path) or {}
        path.unlink()
        self._trace_cache.pop(_cache_key(path), None)
        raw_audio_cleanup = _cleanup_trace_audio(trace)
        return {"forgotten": trace_id, "raw_audio_cleanup": raw_audio_cleanup}

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

    def _load_trace(self, path: Path) -> dict[str, Any] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        key = _cache_key(path)
        cached = self._trace_cache.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return copy.deepcopy(cached[2])
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._trace_cache.pop(key, None)
            return None
        if not isinstance(trace, dict):
            self._trace_cache.pop(key, None)
            return None
        self._trace_cache[key] = (stat.st_mtime_ns, stat.st_size, copy.deepcopy(trace))
        return trace

    def _cache_trace(self, path: Path, trace: dict[str, Any]) -> None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._trace_cache.pop(_cache_key(path), None)
            return
        self._trace_cache[_cache_key(path)] = (stat.st_mtime_ns, stat.st_size, copy.deepcopy(trace))


def _normalize_privacy_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"saved", "session", "incognito"}:
        return mode
    # An ephemeral capture that is explicitly remembered is session-scoped, not the most
    # durable "saved" label it was previously coerced to.
    if mode == "ephemeral":
        return "session"
    return "saved"


def _earworm_surface(trace: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    trace_id = str(trace.get("id") or new_id("trace"))
    event_id = str(trace.get("listeningEventId") or event.get("id") or new_id("evt"))
    session_id = f"earworm_{trace_id}"
    asset_id = f"asset_{event_id}"
    provenance_id = f"prov_{event_id}"
    created_at = str(trace.get("createdAt") or now_iso())
    segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    data_ref = segment.get("data_ref") if isinstance(segment.get("data_ref"), dict) else {}
    features = event.get("features") if isinstance(event.get("features"), dict) else {}
    duration_s = _duration_seconds(event, features)
    asset_hash = data_ref.get("sha256") if isinstance(data_ref.get("sha256"), str) else None
    audio_uri = data_ref.get("uri") if isinstance(data_ref.get("uri"), str) and trace.get("audioStored") else None
    policy = _earworm_policy(trace)
    provenance = {
        "provenance_id": provenance_id,
        "source_type": _earworm_source_type(str(source.get("type") or trace.get("sourceKind") or "unknown")),
        "provider": "oida",
        "asset_hash": asset_hash,
        "consent_status": _consent_status(event, trace),
        "usage_constraints": _usage_constraints(event, trace),
        "created_at": created_at,
    }
    asset = {
        "asset_id": asset_id,
        "type": "audio",
        "uri": audio_uri,
        "duration_seconds": duration_s,
        "sample_rate": _int_or_none(features.get("sample_rate") or segment.get("sample_rate")),
        "channels": _int_or_none(features.get("channels") or segment.get("channels")),
        "provenance_id": provenance_id,
    }
    asset = {key: value for key, value in asset.items() if value is not None}
    events = [
        _earworm_event(
            session_id,
            "signal.packet.ingested",
            created_at,
            "system",
            {
                "packet_id": f"packet_{event_id}",
                "signal_type": "audio",
                "asset_ref": asset_id,
                "segment_id": segment.get("id"),
                "time_range": {"start": 0, "end": duration_s or 0, "unit": "seconds"},
                "context_refs": [event_id],
                "provenance_id": provenance_id,
                "tags": trace.get("tags") if isinstance(trace.get("tags"), list) else [],
                "source": source,
                "raw_audio_policy": trace.get("rawAudioPolicy"),
            },
            reversible=False,
            provenance_id=provenance_id,
        ),
        _earworm_event(
            session_id,
            "analysis.frame",
            created_at,
            "agent",
            {
                "frame_id": f"analysis_{event_id}",
                "asset_ref": asset_id,
                "time_range": {"start": 0, "end": duration_s or 0, "unit": "seconds"},
                "features": features,
                "claim_summary": _claim_summary_from_event(event),
                "routes": event.get("routes") if isinstance(event.get("routes"), list) else [],
            },
            reversible=False,
            provenance_id=provenance_id,
        ),
        _earworm_event(
            session_id,
            "agent.action.applied",
            created_at,
            "agent",
            {
                "action_id": f"remember_{trace_id}",
                "action": "akousmata.remember",
                "trace_id": trace_id,
                "listening_event_id": event_id,
                "retention_policy": trace.get("retentionPolicy"),
                "audio_policy": trace.get("audioPolicy"),
            },
            reversible=True,
            parent_event_ids=[f"analysis_{event_id}"],
            provenance_id=provenance_id,
        ),
    ]
    session = {
        "session_id": session_id,
        "app_id": "oida.akousmata",
        "created_at": created_at,
        "policy": policy,
        "assets": [asset],
        "events": events,
        "provenance": [provenance],
        "views": {
            "current_state": {
                "trace_id": trace_id,
                "listening_event_id": event_id,
                "title": trace.get("title"),
                "summary": (trace.get("summaries") if isinstance(trace.get("summaries"), dict) else {}).get("short"),
            },
            "summaries": [
                {
                    "kind": "akousmata_trace",
                    "trace_id": trace_id,
                    "title": trace.get("title"),
                    "route_ids": trace.get("routeIds") if isinstance(trace.get("routeIds"), list) else [],
                }
            ],
        },
        "indexes": {"by_time": True, "by_asset": True, "by_node": True, "by_text": True},
    }
    context_bundle = {
        "session_id": session_id,
        "selector": {"asset_id": asset_id, "summarization": "agent_safe"},
        "events": events,
        "assets": [asset],
        "provenance": [provenance],
        "summaries": session["views"]["summaries"],
    }
    return {
        "protocol": "earworm",
        "version": "0.1.0",
        "akousmata_surface": ["remember", "list", "search", "similarity", "export", "forget"],
        "session": session,
        "context_bundle": context_bundle,
    }


def _earworm_event(
    session_id: str,
    event_type: str,
    wall_clock: str,
    actor: str,
    payload: dict[str, Any],
    *,
    reversible: bool,
    parent_event_ids: list[str] | None = None,
    provenance_id: str | None = None,
) -> dict[str, Any]:
    event_id = str(payload.get("frame_id") or payload.get("packet_id") or payload.get("action_id") or new_id("ew"))
    event = {
        "event_id": event_id,
        "session_id": session_id,
        "type": event_type,
        "time": {"wall_clock": wall_clock},
        "source": {"actor": actor, "node_id": "oida"},
        "payload": payload,
        "reversible": reversible,
        "parent_event_ids": parent_event_ids or [],
    }
    if provenance_id:
        event["provenance_id"] = provenance_id
    event["event_hash"] = _stable_hash(event)
    return event


def _earworm_policy(trace: dict[str, Any]) -> dict[str, Any]:
    raw_audio_policy = str(trace.get("rawAudioPolicy") or "")
    privacy_mode = str(trace.get("privacyMode") or "")
    mode = "ephemeral" if raw_audio_policy == "temp" or privacy_mode == "incognito" else "project_lifetime"
    return {
        "mode": mode,
        "local_only": True,
        "redaction": {
            "sensitive_fields": ["event.segment.data_ref.uri", "event.source.details.path"],
            "agent_safe_omissions": ["raw_audio_bytes", "direct_personal_identifiers"],
        },
    }


def _duration_seconds(event: dict[str, Any], features: dict[str, Any]) -> float | None:
    value = features.get("duration_s")
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    segment = event.get("segment") if isinstance(event.get("segment"), dict) else {}
    duration_ms = segment.get("duration_ms")
    if isinstance(duration_ms, (int, float)):
        return max(0.0, float(duration_ms) / 1000.0)
    return None


def _earworm_source_type(source_type: str) -> str:
    if source_type in {"live_input", "system_output", "file", "buffer"}:
        return "recorded"
    if source_type == "generated":
        return "generated"
    if source_type == "external_stream":
        return "imported"
    return "unknown"


def _consent_status(event: dict[str, Any], trace: dict[str, Any]) -> str:
    consent = event.get("consent_status") or trace.get("consentStatus")
    if str(consent) in {"owned", "licensed", "public_domain", "unknown", "restricted"}:
        return str(consent)
    if trace.get("privacyMode") == "incognito":
        return "restricted"
    return "unknown"


def _usage_constraints(event: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    constraints: list[str] = ["local_only", "no_training_without_consent"]
    privacy_mode = str(trace.get("privacyMode") or event.get("privacy_mode") or "")
    if privacy_mode == "incognito":
        constraints.append("do_not_retain")
    if trace.get("rawAudioPolicy") == "temp":
        constraints.append("raw_audio_not_retained")
    return _dedupe(constraints)


def _claim_summary_from_event(event: dict[str, Any]) -> dict[str, Any]:
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    for route in routes:
        if not isinstance(route, dict):
            continue
        structured = route.get("structured") if isinstance(route.get("structured"), dict) else {}
        claims = structured.get("claim_summary")
        if isinstance(claims, dict):
            return claims
    return {}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return int(value)
    return None


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cleanup_trace_audio(trace: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(trace, dict) or trace.get("audioStored") is not True:
        return None
    audio_ref = trace.get("audioRef") if isinstance(trace.get("audioRef"), dict) else {}
    uri = audio_ref.get("uri")
    if not isinstance(uri, str) or not uri:
        return None
    return delete_upload_paths([uri])


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
    b_numeric = {key: float(value) for key, value in b.items() if isinstance(value, (int, float)) and math.isfinite(float(value))}
    shared = set(a) & set(b_numeric)
    keys = sorted(set(a) | set(b_numeric))
    if not keys or len(shared) < MIN_SIMILARITY_SHARED_FEATURES:
        return 0.0
    dot = sum(a.get(key, 0.0) * b_numeric.get(key, 0.0) for key in keys)
    norm_a = math.sqrt(sum(a.get(key, 0.0) ** 2 for key in keys))
    norm_b = math.sqrt(sum(b_numeric.get(key, 0.0) ** 2 for key in keys))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    overlap_weight = len(shared) / len(keys)
    return (dot / (norm_a * norm_b)) * overlap_weight


def _cache_key(path: Path) -> str:
    return str(path.expanduser().resolve())


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
