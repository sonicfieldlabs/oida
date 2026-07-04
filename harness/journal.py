from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def session_dir(base: str | Path, audio_path: str | Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", Path(audio_path).stem).strip("-").lower() or "audio"
    path = Path(base) / "sessions" / f"{stamp}-{slug}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_session(base: str | Path, audio_path: str | Path, report: dict[str, Any], command_output: dict[str, Any]) -> Path:
    path = session_dir(base, audio_path)
    write_json(path / "PerceptionReport.json", report)
    write_json(path / "command-output.json", command_output)
    if command_output.get("outputs"):
        write_json(path / "listening-output.json", command_output["outputs"][0])
    (path / "journal.md").write_text(render_journal(audio_path, report, command_output), encoding="utf-8")
    return path


def render_journal(audio_path: str | Path, report: dict[str, Any], command_output: dict[str, Any]) -> str:
    claims = command_output.get("claim_summary", {})
    lines = [
        f"# oída Listening Journal - {Path(audio_path).name}",
        "",
        f"- Command: `{command_output.get('command', '/listen')}`",
        f"- Source: `{report.get('source', {}).get('path', audio_path)}`",
        f"- Engine: `{report.get('engine', {}).get('model', 'unknown')}` via `{report.get('engine', {}).get('profile', 'unknown')}`",
        "",
        "## Synthesis",
        "",
        str(command_output.get("synthesis", "")),
        "",
        "## Claims",
        "",
    ]
    for category in ("heard", "measured", "inferred", "interpreted", "speculative", "undetermined"):
        lines.append(f"### {category}")
        items = claims.get(category, []) if isinstance(claims, dict) else []
        if not items:
            lines.append("")
            lines.append("- None.")
        for claim in items:
            lines.append("")
            lines.append(f"- {claim.get('statement')} ({claim.get('confidence')})")
            if claim.get("basis"):
                lines.append(f"  Basis: {claim.get('basis')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def append_lexicon_entry(path: str | Path, entry: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
