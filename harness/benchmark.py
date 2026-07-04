from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.http_client import get_json, post_json


AUDIO_EXTS = {".wav", ".wave", ".aiff", ".aif", ".flac", ".mp3", ".m4a", ".ogg"}


def audio_files(folder: str | Path, limit: int | None = None) -> list[Path]:
    root = Path(folder).expanduser().resolve()
    files = [path for path in sorted(root.rglob("*")) if path.suffix.lower() in AUDIO_EXTS]
    return files[:limit] if limit is not None else files


def run_report_benchmark(
    *,
    server: str,
    folder: str | Path,
    limit: int | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for audio_file in audio_files(folder, limit):
        before = get_json(server, "/metrics/process")
        start = time.perf_counter()
        report = post_json(server, "/report", {"path": str(audio_file), "profile": "benchmark"})
        wall_s = time.perf_counter() - start
        after = get_json(server, "/metrics/process")
        report_text = json.dumps(report, ensure_ascii=False)
        approx_output_tokens = max(1, round(len(report_text) / 4))
        engine_wall_ms = _engine_wall_ms(report)
        rows.append(
            {
                "path": str(audio_file),
                "duration_s": report.get("source", {}).get("duration_s") if isinstance(report.get("source"), dict) else None,
                "client_wall_s": round(wall_s, 3),
                "engine_wall_ms": engine_wall_ms,
                "approx_output_tokens": approx_output_tokens,
                "approx_output_tokens_per_client_s": round(approx_output_tokens / wall_s, 3) if wall_s > 0 else None,
                "approx_output_tokens_per_engine_s": round(approx_output_tokens / (engine_wall_ms / 1000), 3) if engine_wall_ms else None,
                "server_max_rss_mb_before": before.get("max_rss_mb"),
                "server_max_rss_mb_after": after.get("max_rss_mb"),
                "server_pid": after.get("pid"),
                "event_count": len(report.get("events", [])) if isinstance(report.get("events"), list) else 0,
                "transcript_segment_count": _transcript_segment_count(report),
                "unavailable_reason": report.get("engine", {}).get("unavailable_reason") if isinstance(report.get("engine"), dict) else None,
            }
        )
    result = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "server": server,
        "folder": str(Path(folder).expanduser().resolve()),
        "clip_count": len(rows),
        "rows": rows,
        "summary": summarize(rows),
    }
    if output:
        output_path = Path(output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path("benchmarks") / f"oida-report-benchmark-{stamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result["output_path"] = str(output_path)
    return result


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"clip_count": 0}
    wall_times = [row["client_wall_s"] for row in rows if isinstance(row.get("client_wall_s"), (int, float))]
    rss_values = [row["server_max_rss_mb_after"] for row in rows if isinstance(row.get("server_max_rss_mb_after"), (int, float))]
    token_rates = [row["approx_output_tokens_per_engine_s"] for row in rows if isinstance(row.get("approx_output_tokens_per_engine_s"), (int, float))]
    return {
        "clip_count": len(rows),
        "total_client_wall_s": round(sum(wall_times), 3),
        "mean_client_wall_s": round(sum(wall_times) / len(wall_times), 3) if wall_times else None,
        "server_high_water_rss_mb": max(rss_values) if rss_values else None,
        "mean_approx_output_tokens_per_engine_s": round(sum(token_rates) / len(token_rates), 3) if token_rates else None,
    }


def _engine_wall_ms(report: dict[str, Any]) -> int | None:
    engine = report.get("engine") if isinstance(report.get("engine"), dict) else {}
    wall_ms = engine.get("wall_ms")
    return int(wall_ms) if isinstance(wall_ms, int) else None


def _transcript_segment_count(report: dict[str, Any]) -> int:
    transcript = report.get("transcript") if isinstance(report.get("transcript"), dict) else {}
    segments = transcript.get("segments")
    return len(segments) if isinstance(segments, list) else 0
