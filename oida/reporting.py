from __future__ import annotations

import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from oida import __version__
from oida.chunker import Chunk, chunks_as_dicts, dedupe_events, offset_event, plan_chunks, write_chunk_audios
from oida.dsp import inspect_path
from oida.engine_base import EngineResult, MossEngine
from oida.parsers import parse_events, parse_music_bpm, parse_speech, parse_transcript
from oida.recipes import GenerationSettings, get_recipe
from oida.signal_listener import signal_reading_dict
from oida.reportschema import (
    Caption,
    ChunkInfo,
    EngineInfo,
    EngineParams,
    Event,
    Music,
    PerceptionReport,
    QaItem,
    Speech,
    SourceInfo,
    Transcript,
    TranscriptSegment,
    dump_model,
)


# Advisory report-layer forbidden scan. This is independent defense-in-depth alongside
# the claim_mapper output guard (a safety boundary should not have a single point of
# failure). Populating forbidden_topics_triggered surfaces an explicit `undetermined`
# note via claim_mapper._map_uncertainty.
_SPATIAL_TOPIC = "MOSS receives 16 kHz mono audio; stereo-image / spatial-position claims must be measured by DSP."
_HIGHFREQ_TOPIC = "MOSS receives 16 kHz mono audio and cannot hear content above roughly 8 kHz."
_LEVEL_TOPIC = "MOSS cannot know absolute physical level; use DSP or capture metadata."

FORBIDDEN_TOPIC_PATTERNS: list[tuple[str, str]] = [
    ("stereo image", _SPATIAL_TOPIC),
    ("stereo width", _SPATIAL_TOPIC),
    ("stereo field", _SPATIAL_TOPIC),
    ("stereo spread", _SPATIAL_TOPIC),
    ("spatial width", _SPATIAL_TOPIC),
    ("spatial image", _SPATIAL_TOPIC),
    ("soundstage", _SPATIAL_TOPIC),
    ("sound stage", _SPATIAL_TOPIC),
    ("left channel", _SPATIAL_TOPIC),
    ("right channel", _SPATIAL_TOPIC),
    ("hard left", _SPATIAL_TOPIC),
    ("hard right", _SPATIAL_TOPIC),
    ("panned", _SPATIAL_TOPIC),
    ("panning", _SPATIAL_TOPIC),
    ("binaural", _SPATIAL_TOPIC),
    ("surround sound", _SPATIAL_TOPIC),
    ("above 8 khz", _HIGHFREQ_TOPIC),
    ("above 8khz", _HIGHFREQ_TOPIC),
    (">8 khz", _HIGHFREQ_TOPIC),
    ("ultrasonic", _HIGHFREQ_TOPIC),
    ("absolute level", _LEVEL_TOPIC),
    ("absolute loudness", _LEVEL_TOPIC),
    ("sound pressure level", _LEVEL_TOPIC),
    ("playback level", _LEVEL_TOPIC),
    ("physical level", _LEVEL_TOPIC),
]

_FORBIDDEN_KHZ_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*k\s*hz")
_FORBIDDEN_STEREO_RE = re.compile(r"\bstereo\b")
_FORBIDDEN_SPL_RE = re.compile(r"\bspl\b")

TRANSCRIPTION_TASKS = {
    "none": "transcribe",
    "sentence": "transcribe_sentence",
    "word": "transcribe_word",
}

DEGENERATE_NOTE = (
    "MOSS output failed a text sanity check (likely long-input decode instability); "
    "the pass was discarded. Chunked reruns on shorter segments are more stable."
)


def looks_degenerate(text: str | None) -> bool:
    """Detect token-soup output (mixed-script garbage from unstable decoding).

    Legitimate captions are overwhelmingly ASCII English; a transcript may
    legitimately be non-English, so this guard is only applied to caption /
    analysis prose, never to transcripts.
    """
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 48:
        return False
    if "�" in stripped:
        return True
    ascii_wordish = sum(1 for ch in stripped if ch.isascii() and (ch.isalnum() or ch in " .,;:'\"!?()-"))
    return (ascii_wordish / len(stripped)) < 0.72


def _guard_prose(text: str | None) -> tuple[str | None, str | None]:
    if looks_degenerate(text):
        return None, DEGENERATE_NOTE
    return text, None

CAPTION_DETAILS = {"brief", "dense"}


def make_engine_info(result: EngineResult, chunks: list[dict[str, object]], thinking_budget: int | None = None) -> EngineInfo:
    return EngineInfo(
        daemon=f"oida/{__version__}",
        model=result.model,
        profile=result.profile,
        params=EngineParams(
            temperature=result.settings.temperature,
            top_p=result.settings.top_p,
            top_k=result.settings.top_k,
        ),
        thinking_budget=thinking_budget,
        chunks=[ChunkInfo(**chunk) for chunk in chunks],
        wall_ms=result.wall_ms,
        unavailable_reason=result.unavailable_reason,
    )


def source_info(path: str | Path, dsp: dict[str, object]) -> SourceInfo:
    return SourceInfo(
        path=str(Path(path).expanduser().resolve()),
        duration_s=_float_or_none(dsp.get("durationSeconds")),
        sr_native=_int_or_none(dsp.get("sampleRate")),
        channels=_int_or_none(dsp.get("channelCount")),
        sha256=str(dsp.get("sha256")) if dsp.get("sha256") else None,
    )


def transcribe(engine: MossEngine, path: str, timestamps: str = "sentence") -> tuple[object, EngineResult]:
    if timestamps not in TRANSCRIPTION_TASKS:
        valid = ", ".join(TRANSCRIPTION_TASKS)
        raise ValueError(f"unknown timestamp mode: {timestamps}. Valid modes: {valid}")
    task = TRANSCRIPTION_TASKS[timestamps]
    recipe = get_recipe(task)
    result = engine.generate(path, recipe.prompt, recipe.settings)
    return parse_transcript(result.text), result


def events(engine: MossEngine, path: str, dsp_features: dict[str, object] | None = None) -> tuple[list[Event], EngineResult]:
    recipe = get_recipe("events")
    result = engine.generate(path, recipe.prompt, recipe.settings)
    parsed = parse_events(result.text)
    return corroborate_events(parsed, dsp_features or {}), result


def caption(engine: MossEngine, path: str, detail: str = "dense") -> tuple[Caption, EngineResult]:
    if detail not in CAPTION_DETAILS:
        valid = ", ".join(sorted(CAPTION_DETAILS))
        raise ValueError(f"unknown caption detail: {detail}. Valid details: {valid}")
    brief_recipe = get_recipe("caption_brief")
    dense_recipe = get_recipe("caption_dense")
    if detail == "brief":
        brief = engine.generate(path, brief_recipe.prompt, brief_recipe.settings)
        return Caption(brief=brief.text or None, dense=None), brief
    dense = engine.generate(path, dense_recipe.prompt, dense_recipe.settings)
    return Caption(brief=None, dense=dense.text or None), dense


def speech(engine: MossEngine, path: str) -> tuple[object, EngineResult]:
    recipe = get_recipe("speech")
    result = engine.generate(path, recipe.prompt, recipe.settings)
    return parse_speech(result.text), result


def music(engine: MossEngine, path: str, dsp_bpm: float | None = None) -> tuple[Music, EngineResult]:
    recipe = get_recipe("music")
    result = engine.generate(path, recipe.prompt, recipe.settings)
    text = result.text.strip()
    present = bool(text and "present: false" not in text.lower() and not result.unavailable_reason)
    return (
        Music(
            present=present,
            description=text or None,
            tempo_feel=None,
            dsp_bpm_candidate=dsp_bpm,
            moss_bpm_candidate=parse_music_bpm(text),
            notes=[] if present else ["Music analysis unavailable or not detected."],
        ),
        result,
    )


DIRECT_ANALYSIS_MODES = {"environment", "music", "soundscape", "sonic_data"}


def direct_analysis(engine: MossEngine, path: str, mode: str = "environment", thinking_budget: int | None = None) -> tuple[dict[str, object], EngineResult]:
    if mode not in DIRECT_ANALYSIS_MODES:
        raise ValueError(f"unknown direct MOSS analysis mode: {mode}")
    recipe = get_recipe(mode)
    result = engine.generate(path, recipe.prompt, recipe.settings, thinking_budget=thinking_budget)
    analysis_text, sanity_note = _guard_prose(result.text)
    analysis = {
        "mode": mode,
        "path": str(Path(path).expanduser().resolve()),
        "analysis": analysis_text or "",
        **({"sanity_note": sanity_note} if sanity_note else {}),
        "source_role": "moss_audio_direct",
        "claim_note": "Direct MOSS-Audio output is perception evidence. AKOUO claim mapping must still decide category, confidence, and basis.",
        "limitations": [
            "MOSS-Audio receives 16 kHz mono audio.",
            "Do not use this output for stereo image, absolute physical level, or content above roughly 8 kHz.",
        ],
    }
    return analysis, result


def qa(engine: MossEngine, path: str, question: str, thinking_budget: int | None = None, context: str | None = None) -> tuple[QaItem, EngineResult]:
    context_block = f"\nConversation context:\n{context.strip()}\n" if context and context.strip() else ""
    recipe = get_recipe("qa", question=question, context_block=context_block)
    result = engine.generate(path, recipe.prompt, recipe.settings, thinking_budget=thinking_budget)
    answer, sanity_note = _guard_prose(result.text)
    return QaItem(question=question, answer=answer or (sanity_note or ""), reasoning_trace=result.reasoning_trace, thinking_budget=thinking_budget), result


def think(engine: MossEngine, path: str, instruction: str, thinking_budget: int | None = None) -> tuple[QaItem, EngineResult]:
    recipe = get_recipe("think", instruction=instruction)
    result = engine.generate(path, recipe.prompt, recipe.settings, thinking_budget=thinking_budget)
    return QaItem(question=instruction, answer=result.text, reasoning_trace=result.reasoning_trace, thinking_budget=thinking_budget), result


ALL_MOSS_PASSES = ("transcribe", "events", "caption", "speech", "music")


def _dsp_only_engine_result(engine: MossEngine) -> EngineResult:
    settings = GenerationSettings(model_kind="instruct", temperature=0.0, top_p=1.0, top_k=50, max_new_tokens=0)
    return EngineResult(text="", model="dsp-only", profile=engine.profile, settings=settings, wall_ms=0)


def _normalize_passes(passes: list[str] | tuple[str, ...] | None) -> list[str]:
    if passes is None:
        return list(ALL_MOSS_PASSES)
    unknown = [name for name in passes if name not in ALL_MOSS_PASSES]
    if unknown:
        valid = ", ".join(ALL_MOSS_PASSES)
        raise ValueError(f"unknown MOSS pass(es): {', '.join(unknown)}. Valid passes: {valid}")
    return [name for name in ALL_MOSS_PASSES if name in set(passes)]


def report(
    engine: MossEngine,
    path: str,
    profile: str = "default",
    *,
    chunk_seconds: float = 600.0,
    overlap_seconds: float = 15.0,
    passes: list[str] | tuple[str, ...] | None = None,
) -> PerceptionReport:
    if not Path(path).expanduser().exists():
        raise ValueError(f"audio path does not exist: {path}")
    selected = _normalize_passes(passes)
    dsp = inspect_path(path)
    features = dsp.get("features", {})
    signal_interpretation = signal_reading_dict(dsp)
    chunks = plan_chunks(path, chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds)
    if len(chunks) > 1:
        return chunked_report(engine, path, dsp, chunks, profile=profile, passes=selected, signal_interpretation=signal_interpretation)
    chunk_dicts = chunks_as_dicts(chunks)
    engine_results: list[EngineResult] = []
    sanity_notes: list[str] = []

    transcript_obj: Transcript = Transcript()
    if "transcribe" in selected:
        transcript_obj, transcript_result = transcribe(engine, path, timestamps="sentence")
        engine_results.append(transcript_result)
    events_obj: list[Event] = []
    if "events" in selected:
        events_obj, events_result = events(engine, path, features if isinstance(features, dict) else {})
        engine_results.append(events_result)
    caption_obj = Caption()
    if "caption" in selected:
        caption_obj, caption_result = caption(engine, path, detail="dense")
        engine_results.append(caption_result)
        dense, dense_note = _guard_prose(caption_obj.dense)
        brief, brief_note = _guard_prose(caption_obj.brief)
        caption_obj = Caption(brief=brief, dense=dense)
        for note in (dense_note, brief_note):
            if note and note not in sanity_notes:
                sanity_notes.append(note)
    speech_obj = Speech()
    if "speech" in selected:
        speech_obj, speech_result = speech(engine, path)
        engine_results.append(speech_result)
    music_obj = Music()
    if "music" in selected:
        music_obj, music_result = music(engine, path, _float_or_none(features.get("bpmCandidate")) if isinstance(features, dict) else None)
        engine_results.append(music_result)
        description, music_note = _guard_prose(music_obj.description)
        if music_note:
            music_obj = Music(
                present=False,
                description=None,
                tempo_feel=None,
                dsp_bpm_candidate=music_obj.dsp_bpm_candidate,
                moss_bpm_candidate=None,
                notes=[*music_obj.notes, music_note],
            )
            if music_note not in sanity_notes:
                sanity_notes.append(music_note)

    uncertainty = list(sanity_notes)
    for result in engine_results:
        if result.unavailable_reason and result.unavailable_reason not in uncertainty:
            uncertainty.append(result.unavailable_reason)
    aggregated = aggregate_engine_results(engine_results) if engine_results else _dsp_only_engine_result(engine)
    forbidden = _scan_forbidden_topics(caption_obj.dense or caption_obj.brief, events_obj, speech_obj, music_obj)
    return PerceptionReport(
        version="0.1",
        source=source_info(path, dsp),
        engine=make_engine_info(aggregated, chunk_dicts),
        dsp=dsp,
        transcript=transcript_obj,
        events=events_obj,
        caption=caption_obj,
        speech=speech_obj,
        music=music_obj,
        qa=[],
        model_uncertainty_notes=uncertainty,
        forbidden_topics_triggered=forbidden,
        signal_interpretation=signal_interpretation,
        moss_passes=selected,
    )


def chunked_report(
    engine: MossEngine,
    path: str,
    dsp: dict[str, object],
    chunks: list[Chunk],
    profile: str = "default",
    *,
    passes: list[str] | tuple[str, ...] | None = None,
    signal_interpretation: dict[str, object] | None = None,
) -> PerceptionReport:
    selected = _normalize_passes(passes)
    if signal_interpretation is None:
        signal_interpretation = signal_reading_dict(dsp)
    features = dsp.get("features", {})
    transcript_segments: list[TranscriptSegment] = []
    all_events: list[dict[str, object]] = []
    caption_lines: list[str] = []
    engine_results: list[EngineResult] = []
    sanity_notes: list[str] = []
    speech_obj: Speech | None = None
    music_obj: Music | None = None

    with tempfile.TemporaryDirectory(prefix="oida-chunks-") as temp_dir:
        chunk_paths = write_chunk_audios(path, chunks, temp_dir)
        for chunk, chunk_path in zip(chunks, chunk_paths):
            if "transcribe" in selected:
                transcript_obj, transcript_result = transcribe(engine, str(chunk_path), timestamps="sentence")
                engine_results.append(transcript_result)
                for segment in transcript_obj.segments:
                    transcript_segments.append(
                        TranscriptSegment(
                            t0=_offset_time(segment.t0, chunk.t0),
                            t1=_offset_time(segment.t1, chunk.t0),
                            text=segment.text,
                            confidence=segment.confidence,
                        )
                    )

            if "events" in selected:
                events_obj, events_result = events(engine, str(chunk_path))
                engine_results.append(events_result)
                for event in events_obj:
                    shifted = offset_event(dump_model(event), chunk.t0)
                    all_events.append(shifted)

            if "caption" in selected:
                caption_obj, caption_result = caption(engine, str(chunk_path), detail="dense")
                engine_results.append(caption_result)
                caption_text, caption_note = _guard_prose(caption_obj.dense or caption_obj.brief)
                if caption_note and caption_note not in sanity_notes:
                    sanity_notes.append(caption_note)
                if caption_text:
                    caption_lines.append(f"Segment {chunk.i} [{chunk.t0:.2f}-{chunk.t1:.2f}s]: {caption_text}")

        if "speech" in selected:
            speech_obj, speech_result = speech(engine, str(chunk_paths[0]))
            speech_obj.notes.append("Long-audio report: speech dimensions were evaluated on the first chunk only.")
            engine_results.append(speech_result)
        if "music" in selected:
            music_obj, music_result = music(engine, str(chunk_paths[0]), _float_or_none(features.get("bpmCandidate")) if isinstance(features, dict) else None)
            music_obj.notes.append("Long-audio report: music interpretation was evaluated on the first chunk only; DSP metrics cover the source file.")
            engine_results.append(music_result)
            description, music_note = _guard_prose(music_obj.description)
            if music_note:
                music_obj = Music(
                    present=False,
                    description=None,
                    tempo_feel=None,
                    dsp_bpm_candidate=music_obj.dsp_bpm_candidate,
                    moss_bpm_candidate=None,
                    notes=[*music_obj.notes, music_note],
                )
                if music_note not in sanity_notes:
                    sanity_notes.append(music_note)

    event_dicts = dedupe_events(all_events)
    events_obj = [Event.model_validate(item) for item in event_dicts]
    transcript_segments = dedupe_transcript_segments(transcript_segments)
    if isinstance(features, dict):
        events_obj = corroborate_events(events_obj, features)

    uncertainty: list[str] = list(sanity_notes)
    for result in engine_results:
        if result.unavailable_reason and result.unavailable_reason not in uncertainty:
            uncertainty.append(result.unavailable_reason)

    aggregated = aggregate_engine_results(engine_results) if engine_results else _dsp_only_engine_result(engine)
    forbidden = _scan_forbidden_topics("\n".join(caption_lines), events_obj, speech_obj, music_obj)
    transcript_notes: list[str] = []
    if "transcribe" in selected and not transcript_segments:
        transcript_notes.append("No transcript segments were produced from chunked inference.")
    return PerceptionReport(
        version="0.1",
        source=source_info(path, dsp),
        engine=make_engine_info(aggregated, chunks_as_dicts(chunks)),
        dsp=dsp,
        transcript=Transcript(
            present=bool(transcript_segments),
            language=None,
            segments=transcript_segments,
            notes=transcript_notes,
        ),
        events=events_obj,
        caption=Caption(brief=None, dense="\n".join(caption_lines) if caption_lines else None),
        speech=speech_obj or Speech(),
        music=music_obj or Music(),
        qa=[],
        model_uncertainty_notes=uncertainty,
        forbidden_topics_triggered=forbidden,
        signal_interpretation=signal_interpretation,
        moss_passes=selected,
    )


def aggregate_engine_results(results: list[EngineResult]) -> EngineResult:
    if not results:
        raise ValueError("cannot aggregate empty engine result list")
    first = results[0]
    unavailable = []
    wall_ms = 0
    for result in results:
        if result.unavailable_reason and result.unavailable_reason not in unavailable:
            unavailable.append(result.unavailable_reason)
        if result.wall_ms:
            wall_ms += result.wall_ms
    return EngineResult(
        text="",
        model=first.model,
        profile=first.profile,
        settings=first.settings,
        reasoning_trace=None,
        wall_ms=wall_ms or None,
        unavailable_reason="; ".join(unavailable) if unavailable else None,
    )


def dedupe_transcript_segments(segments: list[TranscriptSegment], iou_threshold: float = 0.3) -> list[TranscriptSegment]:
    kept: list[TranscriptSegment] = []
    for segment in sorted(segments, key=lambda item: float(item.t0 or 0.0)):
        text = _norm_transcript(segment.text)
        duplicate = False
        for existing in kept:
            existing_text = _norm_transcript(existing.text)
            if text and existing_text and SequenceMatcher(a=text, b=existing_text).ratio() >= 0.9 and _segment_iou(segment, existing) >= iou_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(segment)
    return kept


def _segment_iou(a: TranscriptSegment, b: TranscriptSegment) -> float:
    if a.t0 is None or a.t1 is None or b.t0 is None or b.t1 is None:
        return 0.0
    left = max(float(a.t0), float(b.t0))
    right = min(float(a.t1), float(b.t1))
    inter = max(0.0, right - left)
    union = max(float(a.t1), float(b.t1)) - min(float(a.t0), float(b.t0))
    return inter / union if union > 0 else 0.0


def _norm_transcript(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def forbidden_topics_for_text(text: str) -> list[str]:
    lowered = text.lower()
    triggered: list[str] = []
    for pattern, message in FORBIDDEN_TOPIC_PATTERNS:
        if pattern in lowered and message not in triggered:
            triggered.append(message)
    if _FORBIDDEN_STEREO_RE.search(lowered) and _SPATIAL_TOPIC not in triggered:
        triggered.append(_SPATIAL_TOPIC)
    if _FORBIDDEN_SPL_RE.search(lowered) and _LEVEL_TOPIC not in triggered:
        triggered.append(_LEVEL_TOPIC)
    khz = _FORBIDDEN_KHZ_RE.search(lowered)
    if khz and _HIGHFREQ_TOPIC not in triggered:
        try:
            if float(khz.group(1)) > 8.0:
                triggered.append(_HIGHFREQ_TOPIC)
        except ValueError:
            pass
    return triggered


def _scan_forbidden_topics(
    caption_text: object,
    events_list: list[Event] | None,
    speech_obj: object | None,
    music_obj: object | None,
) -> list[str]:
    """Collect forbidden-topic notes from MOSS-generated perceptual text.

    Transcribed human speech is intentionally excluded: a speaker saying "stereo" is
    quoted content, not a MOSS perceptual claim about the recording.
    """
    texts: list[str] = []
    if caption_text:
        texts.append(str(caption_text))
    for event in events_list or []:
        if getattr(event, "label", None):
            texts.append(str(event.label))
        if getattr(event, "description", None):
            texts.append(str(event.description))
    if speech_obj is not None and getattr(speech_obj, "present", False):
        dimensions = getattr(speech_obj, "dimensions", None) or {}
        for value in dimensions.values():
            if value:
                texts.append(str(value))
    if music_obj is not None and getattr(music_obj, "description", None):
        texts.append(str(music_obj.description))
    triggered: list[str] = []
    for text in texts:
        for message in forbidden_topics_for_text(text):
            if message not in triggered:
                triggered.append(message)
    return triggered


def corroborate_events(events_list: list[Event], dsp_features: dict[str, object]) -> list[Event]:
    onset_times = [
        float(value)
        for value in (dsp_features.get("onsetTimes") if isinstance(dsp_features.get("onsetTimes"), list) else [])
        if isinstance(value, (int, float))
    ]
    if not onset_times:
        return events_list
    for event in events_list:
        if event.t0 is not None and event.t1 is not None and _event_has_onset(event, onset_times):
            event.corroborated_by_dsp = True
            if event.confidence == "medium":
                event.confidence = "high"
    return events_list


def _event_has_onset(event: Event, onset_times: list[float], tolerance_s: float = 0.1) -> bool:
    if event.t0 is None or event.t1 is None:
        return False
    start = min(float(event.t0), float(event.t1)) - tolerance_s
    end = max(float(event.t0), float(event.t1)) + tolerance_s
    return any(start <= onset_time <= end for onset_time in onset_times)


def report_to_dict(report_obj: PerceptionReport) -> dict[str, object]:
    return dump_model(report_obj)


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _offset_time(value: float | None, offset_s: float) -> float | None:
    return round(value + offset_s, 3) if isinstance(value, (int, float)) else None
