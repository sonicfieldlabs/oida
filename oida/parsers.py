from __future__ import annotations

import re

from oida.reportschema import Event, Speech, Transcript, TranscriptSegment


TIMESTAMP_SEGMENT_RE = re.compile(r"\[(?P<t0>\d+(?:\.\d+)?)\](?P<text>.*?)\[(?P<t1>\d+(?:\.\d+)?)\]", re.DOTALL)
EVENT_RE = re.compile(r"^\s*(?:\[)?(?P<t0>\d+(?:\.\d+)?)\s*[-–]\s*(?P<t1>\d+(?:\.\d+)?)(?:\])?\s*(?P<body>.+?)\s*$")
EVENT_DETAIL_RE = re.compile(r"\s+[-—]\s+|:\s+")


def parse_transcript(text: str) -> Transcript:
    stripped = text.strip()
    if not stripped:
        return Transcript(present=False, language=None, segments=[], notes=["MOSS transcript unavailable or empty."])
    matches = list(TIMESTAMP_SEGMENT_RE.finditer(stripped))
    if matches:
        return Transcript(
            present=True,
            language=None,
            segments=[
                TranscriptSegment(
                    t0=float(match.group("t0")),
                    t1=float(match.group("t1")),
                    text=" ".join(match.group("text").split()),
                    confidence="high",
                )
                for match in matches
            ],
            notes=[],
        )
    return Transcript(present=True, language=None, segments=[TranscriptSegment(t0=None, t1=None, text=stripped, confidence="medium")], notes=[])


def parse_events(text: str) -> list[Event]:
    events: list[Event] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = EVENT_RE.match(line)
        if match:
            parts = EVENT_DETAIL_RE.split(match.group("body").strip(), maxsplit=1)
            label = " ".join(parts[0].strip().split())
            desc = " ".join(parts[1].strip().split()) if len(parts) > 1 else ""
            events.append(
                Event(
                    t0=float(match.group("t0")),
                    t1=float(match.group("t1")),
                    label=label,
                    description=desc,
                    corroborated_by_dsp=False,
                    confidence="medium",
                )
            )
        else:
            events.append(Event(t0=None, t1=None, label=line[:80], description=line, confidence="low"))
    return events


def parse_speech(text: str) -> Speech:
    stripped = text.strip()
    if not stripped or "present: false" in stripped.lower():
        return Speech(present=False, dimensions={}, identity_caution=True, notes=["Speech not detected or not available."])
    dimensions: dict[str, str] = {}
    for line in stripped.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            dimensions[key.strip().lower().replace(" ", "_")] = value.strip()
    if not dimensions:
        dimensions["summary"] = stripped
    return Speech(present=True, dimensions=dimensions, identity_caution=True, notes=["MOSS paralinguistic dimensions are interpreted, not identity proof."])


def parse_music_bpm(text: str) -> float | None:
    match = re.search(r"(?P<bpm>\d+(?:\.\d+)?)\s*(?:bpm|beats per minute)", text, flags=re.IGNORECASE)
    return float(match.group("bpm")) if match else None
