from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_session_id(audio_path: str | Path) -> str:
    stem = Path(audio_path).stem or "audio"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower() or "audio"


def session_path(repo: str | Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip("-") or "default"
    return Path(repo) / "sessions" / f"chat-{safe}.json"


def load_session(repo: str | Path, session_id: str, audio_path: str | Path) -> dict[str, Any]:
    path = session_path(repo, session_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    return {
        "session_id": session_id,
        "audio_path": str(audio_path),
        "created_at": now,
        "updated_at": now,
        "turns": [],
    }


def save_session(repo: str | Path, session: dict[str, Any]) -> Path:
    path = session_path(repo, str(session.get("session_id") or "default"))
    path.parent.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def append_turn(session: dict[str, Any], question: str, answer: dict[str, Any]) -> None:
    qa = answer.get("qa", answer) if isinstance(answer, dict) else {}
    turns = session.setdefault("turns", [])
    turns.append(
        {
            "question": question,
            "answer": qa.get("answer", ""),
            "reasoning_trace": qa.get("reasoning_trace"),
            "forbidden_topics_triggered": answer.get("forbidden_topics_triggered", []),
        }
    )


def context_text(session: dict[str, Any], max_turns: int = 8) -> str:
    turns = session.get("turns") if isinstance(session.get("turns"), list) else []
    selected = turns[-max_turns:]
    if not selected:
        return ""
    lines = ["Previous audio QA turns for this same source:"]
    for index, turn in enumerate(selected, start=1):
        lines.append(f"Q{index}: {turn.get('question', '')}")
        lines.append(f"A{index}: {turn.get('answer', '')}")
    return "\n".join(lines)
