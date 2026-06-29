from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def timeline_entry(audio_path: str | Path, report: dict[str, Any]) -> dict[str, Any]:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    caption = report.get("caption") if isinstance(report.get("caption"), dict) else {}
    transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
    return {
        "path": str(audio_path),
        "source_path": source.get("path", str(audio_path)),
        "duration_s": source.get("duration_s"),
        "caption": caption.get("brief") or caption.get("dense"),
        "events": report.get("events", []) if isinstance(report.get("events"), list) else [],
        "transcript_segments": transcript.get("segments", []) if isinstance(transcript.get("segments"), list) else [],
        "music": report.get("music", {}) if isinstance(report.get("music"), dict) else {},
        "speech": report.get("speech", {}) if isinstance(report.get("speech"), dict) else {},
        "model_uncertainty_notes": report.get("model_uncertainty_notes", []),
    }


def write_timeline(path: str | Path, entries: list[dict[str, Any]]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "entries": entries,
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def load_timeline(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def answer_timeline_question(timeline: dict[str, Any], question: str, max_results: int = 8) -> dict[str, Any]:
    terms = _terms(question)
    entries = timeline.get("entries") if isinstance(timeline.get("entries"), list) else []
    scored = []
    for entry in entries:
        entry_terms = _entry_terms(entry)
        score = sum(entry_terms.get(term, 0) for term in terms)
        if score > 0 or not terms:
            scored.append((score, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    matches = [render_match(entry, score) for score, entry in scored[:max_results]]
    if matches:
        answer = f"Found {len(matches)} matching timeline entries for: {question}"
    else:
        answer = f"No direct timeline matches found for: {question}"
    return {
        "question": question,
        "answer": answer,
        "matches": matches,
        "method": "deterministic merged-timeline lexical retrieval",
    }


def render_match(entry: dict[str, Any], score: int) -> dict[str, Any]:
    events = entry.get("events") if isinstance(entry.get("events"), list) else []
    transcript = entry.get("transcript_segments") if isinstance(entry.get("transcript_segments"), list) else []
    return {
        "score": score,
        "path": entry.get("source_path") or entry.get("path"),
        "duration_s": entry.get("duration_s"),
        "caption": entry.get("caption"),
        "events": events[:5],
        "transcript_segments": transcript[:5],
        "uncertainty": entry.get("model_uncertainty_notes", []),
    }


def timeline_path_for_folder(repo: str | Path, folder: str | Path) -> Path:
    root = Path(folder).expanduser().resolve()
    return Path(repo) / "lexicon" / f"{root.name or 'sweep'}.timeline.json"


def _terms(question: str) -> list[str]:
    return [term for term in re.findall(r"[a-zA-Z0-9_]{3,}", question.lower()) if term not in STOPWORDS]


def _entry_text(entry: dict[str, Any]) -> str:
    parts = [str(entry.get("caption") or ""), json.dumps(entry.get("events", []), ensure_ascii=False), json.dumps(entry.get("transcript_segments", []), ensure_ascii=False)]
    return "\n".join(parts)


def _entry_terms(entry: dict[str, Any]) -> Counter[str]:
    return Counter(_terms(_entry_text(entry)))


STOPWORDS = {
    "what",
    "when",
    "where",
    "which",
    "with",
    "this",
    "that",
    "from",
    "does",
    "about",
    "audio",
    "sound",
    "sounds",
    "happen",
    "happens",
}
