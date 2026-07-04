from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import soundfile as sf

from aear.config import data_dir, uploads_dir
from aear.contracts import SourceType, audio_segment_from_path, source_for_path, to_dict
from aear.dsp import AudioData, inspect_path, load_audio
from aear.storage import write_json_atomic

CAPTURE_LAST_PATTERN = "*-hmm-capture-last-*s.wav"
CAPTURE_LAST_KEEP = 12
STOPPED_SESSIONS_KEEP = 8


def _synchronized(method):
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


@dataclass
class LiveSession:
    session_id: str
    created_at: str
    updated_at: str
    ring_seconds: float
    vad_threshold_dbfs: float
    source_type: SourceType = "live_input"
    source_label: str = "Live input"
    device_id: str | None = None
    active: bool = True
    chunks: list[dict[str, Any]] = field(default_factory=list)


class LiveManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.sessions: dict[str, LiveSession] = {}

    @_synchronized
    def start(
        self,
        ring_seconds: float = 60.0,
        vad_threshold_dbfs: float = -45.0,
        *,
        source_type: str = "live_input",
        source_label: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        now = datetime.now(timezone.utc).isoformat()
        normalized_source_type = _source_type(source_type)
        session = LiveSession(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            ring_seconds=max(1.0, float(ring_seconds)),
            vad_threshold_dbfs=float(vad_threshold_dbfs),
            source_type=normalized_source_type,
            source_label=source_label or ("System audio" if normalized_source_type == "system_output" else "Live input"),
            device_id=device_id,
        )
        self.sessions[session_id] = session
        return self.status(session_id)

    @_synchronized
    def ingest_saved_upload(self, session_id: str, saved: dict[str, Any]) -> dict[str, Any]:
        session = self.ensure_active(session_id)
        path = str(saved["path"])
        dsp = inspect_path(path)
        features = dsp.get("features") if isinstance(dsp.get("features"), dict) else {}
        rms = features.get("rmsDbfs") if isinstance(features, dict) else None
        peak = features.get("peakDbfs") if isinstance(features, dict) else None
        vad_active = _vad_active(rms, peak, session.vad_threshold_dbfs)
        chunk = {
            "path": path,
            "raw_path": saved.get("raw_path"),
            "source_type": session.source_type,
            "source_label": session.source_label,
            "device_id": session.device_id,
            "duration_s": dsp.get("durationSeconds"),
            "rms_dbfs": rms,
            "peak_dbfs": peak,
            "vad_active": vad_active,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "sha256": saved.get("sha256"),
        }
        session.chunks.append(chunk)
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._trim_ring(session)
        status = self.status(session_id)
        status["latest_chunk"] = chunk
        return status

    @_synchronized
    def stop(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        session.active = False
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._write_manifest(session)
        status = self.status(session_id)
        self._evict_stopped_sessions()
        return status

    def _evict_stopped_sessions(self, keep: int = STOPPED_SESSIONS_KEEP) -> None:
        # Stopped sessions previously accumulated in self.sessions for the daemon's
        # lifetime. Their manifests are already on disk, so keep only the most
        # recently stopped few queryable and drop the rest.
        stopped = [session for session in self.sessions.values() if not session.active]
        if len(stopped) <= keep:
            return
        stopped.sort(key=lambda session: str(session.updated_at))
        for session in stopped[: len(stopped) - keep]:
            self.sessions.pop(session.session_id, None)

    @_synchronized
    def capture_last(self, session_id: str, seconds: float = 10.0) -> dict[str, Any]:
        session = self.ensure_active(session_id)
        capture_seconds = max(0.25, min(float(seconds), session.ring_seconds))
        selected = self._recent_chunks_for_duration(session, capture_seconds)
        if not selected:
            raise ValueError(f"live session has no captured chunks: {session_id}")

        output_dir = uploads_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_path = output_dir / f"{stamp}-hmm-capture-last-{math.ceil(capture_seconds)}s.wav"
        write_capture(selected, output_path, max_seconds=capture_seconds)
        _prune_capture_temp_files()
        segment = audio_segment_from_path(
            output_path,
            source=source_for_path(
                output_path,
                source_type=session.source_type,
                label=f"{session.source_label}: last {capture_seconds:g}s",
                device_id=session.device_id,
            ),
            privacy_mode="ephemeral",
            ephemeral=True,
            metadata={
                "live_session_id": session_id,
                "source_type": session.source_type,
                "source_label": session.source_label,
                "device_id": session.device_id,
                "captured_chunk_count": len(selected),
                "raw_audio_policy": "temp",
            },
        )
        return {
            "session_id": session_id,
            "capture_seconds": capture_seconds,
            "captured_chunk_count": len(selected),
            "path": str(output_path),
            "raw_audio_policy": "temp",
            "segment": to_dict(segment),
        }

    @_synchronized
    def status(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        total_duration = sum(float(chunk.get("duration_s") or 0.0) for chunk in session.chunks)
        active_chunks = sum(1 for chunk in session.chunks if chunk.get("vad_active"))
        overflow_s = max(0.0, total_duration - session.ring_seconds)
        return {
            "session_id": session.session_id,
            "active": session.active,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "source": {
                "type": session.source_type,
                "label": session.source_label,
                "device_id": session.device_id,
            },
            "ring_seconds": session.ring_seconds,
            "vad_threshold_dbfs": session.vad_threshold_dbfs,
            "chunk_count": len(session.chunks),
            "ring_duration_s": round(total_duration, 3),
            "ring_capacity_ok": overflow_s == 0,
            "ring_overflow_s": round(overflow_s, 3),
            "vad_active_chunks": active_chunks,
            "recent_chunks": session.chunks[-8:],
        }

    @_synchronized
    def signal_snapshot(self, session_id: str, *, bands: int = 14) -> dict[str, Any]:
        session = self._session(session_id)
        recent = session.chunks[-max(1, min(32, bands * 2)) :]
        latest = recent[-1] if recent else None
        levels = [_chunk_meter_value(chunk) for chunk in recent]
        band_values = _resample_levels(levels, max(4, min(64, bands)))
        peak_values = _resample_levels([_chunk_peak_value(chunk) for chunk in recent], len(band_values))
        active_chunks = sum(1 for chunk in recent if chunk.get("vad_active"))
        return {
            "version": "0.1",
            "session_id": session.session_id,
            "active": session.active,
            "updated_at": session.updated_at,
            "source": {
                "type": session.source_type,
                "label": session.source_label,
                "device_id": session.device_id,
            },
            "chunk_count": len(session.chunks),
            "recent_chunk_count": len(recent),
            "ring_seconds": session.ring_seconds,
            "ring_duration_s": round(sum(float(chunk.get("duration_s") or 0.0) for chunk in session.chunks), 3),
            "vad_active": bool(latest.get("vad_active")) if isinstance(latest, dict) else False,
            "vad_active_recent_count": active_chunks,
            "latest": _signal_chunk(latest),
            "bands": band_values,
            "peaks": peak_values,
            "meter": {
                "rms": _chunk_meter_value(latest) if isinstance(latest, dict) else 0.0,
                "peak": _chunk_peak_value(latest) if isinstance(latest, dict) else 0.0,
                "basis": "browser-uploaded-live-chunk-dsp",
            },
        }

    @_synchronized
    def ensure_active(self, session_id: str) -> LiveSession:
        session = self._session(session_id)
        if not session.active:
            raise ValueError(f"live session is stopped: {session_id}")
        return session

    def _session(self, session_id: str) -> LiveSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ValueError(f"unknown live session: {session_id}") from exc

    def _trim_ring(self, session: LiveSession) -> None:
        while len(session.chunks) > 1 and sum(float(chunk.get("duration_s") or 0.0) for chunk in session.chunks) > session.ring_seconds:
            removed = session.chunks.pop(0)
            _unlink_if_upload(removed.get("path"))
            raw_path = removed.get("raw_path")
            if raw_path != removed.get("path"):
                _unlink_if_upload(raw_path)

    def _recent_chunks_for_duration(self, session: LiveSession, seconds: float) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        total = 0.0
        for chunk in reversed(session.chunks):
            if not Path(str(chunk.get("path") or "")).exists():
                continue
            selected.append(chunk)
            total += float(chunk.get("duration_s") or 0.0)
            if total >= seconds:
                break
        return list(reversed(selected))

    def _write_manifest(self, session: LiveSession) -> Path:
        output = data_dir() / "sessions" / f"live-{session.session_id}.json"
        write_json_atomic(output, self.status(session.session_id))
        return output


def _vad_active(rms: object, peak: object, threshold_dbfs: float) -> bool:
    if isinstance(rms, (int, float)) and float(rms) >= threshold_dbfs:
        return True
    return isinstance(peak, (int, float)) and float(peak) >= threshold_dbfs + 10


def _signal_chunk(chunk: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(chunk, dict):
        return None
    return {
        "received_at": chunk.get("received_at"),
        "duration_s": chunk.get("duration_s"),
        "rms_dbfs": chunk.get("rms_dbfs"),
        "peak_dbfs": chunk.get("peak_dbfs"),
        "vad_active": chunk.get("vad_active"),
    }


def _chunk_meter_value(chunk: dict[str, Any] | None) -> float:
    if not isinstance(chunk, dict):
        return 0.0
    rms = chunk.get("rms_dbfs")
    peak = chunk.get("peak_dbfs")
    if isinstance(rms, (int, float)):
        return _dbfs_to_unit(float(rms))
    if isinstance(peak, (int, float)):
        return _dbfs_to_unit(float(peak)) * 0.85
    return 0.0


def _chunk_peak_value(chunk: dict[str, Any] | None) -> float:
    if not isinstance(chunk, dict):
        return 0.0
    peak = chunk.get("peak_dbfs")
    if isinstance(peak, (int, float)):
        return _dbfs_to_unit(float(peak))
    return _chunk_meter_value(chunk)


def _dbfs_to_unit(value: float) -> float:
    if math.isinf(value) or math.isnan(value):
        return 0.0
    return round(max(0.0, min(1.0, (value + 60.0) / 60.0)), 4)


def _resample_levels(levels: list[float], target_count: int) -> list[float]:
    if target_count <= 0:
        return []
    if not levels:
        return [0.0] * target_count
    if len(levels) == target_count:
        return [round(max(0.0, min(1.0, value)), 4) for value in levels]
    output: list[float] = []
    for index in range(target_count):
        start = int(index * len(levels) / target_count)
        end = int((index + 1) * len(levels) / target_count)
        bucket = levels[start : max(start + 1, end)]
        output.append(round(max(bucket), 4))
    return output


def _source_type(value: str) -> SourceType:
    if value in {"live_input", "system_output", "file", "buffer", "generated", "external_stream"}:
        return value  # type: ignore[return-value]
    return "live_input"


def _unlink_if_upload(path: object) -> None:
    # Only delete files that are genuinely inside the configured uploads/ directory. The
    # previous `"uploads" in parts` substring test would also match an unrelated
    # external path such as $HOME/keep.wav.
    if not isinstance(path, str) or not path:
        return
    uploads_root = uploads_dir().resolve()
    try:
        resolved = Path(path).resolve()
    except OSError:
        return
    if not resolved.is_relative_to(uploads_root):
        return
    try:
        resolved.unlink(missing_ok=True)
    except OSError:
        pass


def _prune_capture_temp_files(keep: int = CAPTURE_LAST_KEEP) -> None:
    # Bound the temporary "capture last N seconds" WAVs. They are labelled
    # raw_audio_policy:"temp"; keep only the most recent few between explicit wipes.
    directory = uploads_dir()
    if not directory.exists():
        return
    files = sorted(
        (path for path in directory.glob(CAPTURE_LAST_PATTERN) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[max(0, keep):]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def write_capture(chunks: list[dict[str, Any]], output_path: str | Path, *, max_seconds: float) -> Path:
    audio_items: list[AudioData] = []
    target_sr: int | None = None
    target_channels: int | None = None
    for chunk in chunks:
        path = chunk.get("path")
        if not isinstance(path, str):
            continue
        audio = load_audio(path, target_sr=target_sr)
        if target_sr is None:
            target_sr = audio.sample_rate
            target_channels = audio.channels
        audio_items.append(_match_channels(audio, target_channels or audio.channels))

    if not audio_items or target_sr is None:
        raise ValueError("no readable live chunks were available for capture")

    samples = np.concatenate([audio.samples for audio in audio_items], axis=0)
    max_frames = max(1, int(round(max_seconds * target_sr)))
    if samples.shape[0] > max_frames:
        samples = samples[-max_frames:]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, samples, target_sr)
    return output


def _match_channels(audio: AudioData, channels: int) -> AudioData:
    if audio.channels == channels:
        return audio
    samples = audio.samples
    if channels == 1:
        samples = samples.mean(axis=1, keepdims=True)
    elif audio.channels == 1:
        samples = np.repeat(samples, channels, axis=1)
    else:
        samples = samples[:, :channels]
    return AudioData(samples=samples.astype(np.float32), sample_rate=audio.sample_rate, channels=channels, duration_s=audio.duration_s)
