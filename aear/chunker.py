from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import soundfile as sf

from aear.dsp import AudioData, load_audio


@dataclass(frozen=True)
class Chunk:
    i: int
    t0: float
    t1: float
    source_path: str | None = None


def plan_chunks(path: str | Path, chunk_seconds: float = 600.0, overlap_seconds: float = 15.0) -> list[Chunk]:
    duration = audio_duration(path)
    if duration <= 0:
        return [Chunk(i=0, t0=0.0, t1=0.0, source_path=str(path))]
    if duration <= chunk_seconds:
        return [Chunk(i=0, t0=0.0, t1=duration, source_path=str(path))]

    chunks: list[Chunk] = []
    start = 0.0
    index = 0
    step = chunk_seconds - overlap_seconds
    if step <= 0:
        step = chunk_seconds
    step = max(0.001, step)
    while start < duration:
        end = min(duration, start + chunk_seconds)
        chunks.append(Chunk(i=index, t0=round(start, 3), t1=round(end, 3), source_path=str(path)))
        if end >= duration:
            break
        start += step
        index += 1
    return chunks


def offset_event(event: dict[str, object], offset_s: float) -> dict[str, object]:
    shifted = dict(event)
    for key in ("t0", "t1"):
        if isinstance(shifted.get(key), (int, float)):
            shifted[key] = round(float(shifted[key]) + offset_s, 3)
    return shifted


def time_iou(a: dict[str, object], b: dict[str, object]) -> float:
    a0 = a.get("t0")
    a1 = a.get("t1")
    b0 = b.get("t0")
    b1 = b.get("t1")
    if not all(isinstance(value, (int, float)) for value in (a0, a1, b0, b1)):
        return 0.0
    left = max(float(a0), float(b0))
    right = min(float(a1), float(b1))
    inter = max(0.0, right - left)
    union = max(float(a1), float(b1)) - min(float(a0), float(b0))
    return inter / union if union > 0 else 0.0


def dedupe_events(events: list[dict[str, object]], iou_threshold: float = 0.5) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: float(item.get("t0") or 0.0)):
        label = str(event.get("label") or "").lower()
        duplicate = False
        for existing in kept:
            existing_label = str(existing.get("label") or "").lower()
            if label and existing_label and label == existing_label and time_iou(event, existing) >= iou_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(event)
    return kept


def chunks_as_dicts(chunks: list[Chunk]) -> list[dict[str, object]]:
    return [asdict(chunk) for chunk in chunks]


def write_chunk_audio(path: str | Path, chunk: Chunk, output_dir: str | Path) -> Path:
    audio = load_audio(path)
    return _write_chunk_from_audio(audio, chunk, output_dir)


def write_chunk_audios(path: str | Path, chunks: list[Chunk], output_dir: str | Path) -> list[Path]:
    audio = load_audio(path)
    return [_write_chunk_from_audio(audio, chunk, output_dir) for chunk in chunks]


def _write_chunk_from_audio(audio: AudioData, chunk: Chunk, output_dir: str | Path) -> Path:
    start = max(0, int(round(chunk.t0 * audio.sample_rate)))
    end = min(audio.samples.shape[0], int(round(chunk.t1 * audio.sample_rate)))
    output_path = Path(output_dir) / f"chunk-{chunk.i:04d}.wav"
    sf.write(output_path, audio.samples[start:end], audio.sample_rate)
    return output_path


def audio_duration(path: str | Path) -> float:
    try:
        info = sf.info(str(path))
        if info.samplerate > 0 and info.frames >= 0:
            return float(info.frames / info.samplerate)
    except Exception:
        pass
    return load_audio(path).duration_s
