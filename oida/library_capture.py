"""Optional retained copies for the local sound library; never edits an Auditum."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path


def retain_capture(
    path: Path,
    root: Path,
    *,
    record_id: str | None,
    event_id: str,
    label: str,
    duration_seconds: float | None,
    source_type: str,
    blocked: bool,
    file_window=None,
    parent_sound_id: str | None = None,
):
    if blocked:
        return {
            "status": "withheld",
            "reason": "The active privacy or covenant policy forbids retention",
        }
    target = None
    sidecar = None
    try:
        if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            return {
                "status": "unavailable",
                "reason": "Capture is missing or exceeds the library limit",
            }
        directory = root / "library-captures"
        directory.mkdir(parents=True, exist_ok=True)
        identifier = "capture-" + uuid.uuid4().hex
        target = directory / (identifier + path.suffix.lower())
        sidecar = target.with_suffix(target.suffix + ".json")
        temporary = directory / (identifier + ".part")
        try:
            with path.open("rb") as src, temporary.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        metadata = {
            "contract": "oida/library-capture/v1",
            "record_id": record_id,
            "event_id": event_id,
            "label": label,
            "duration_seconds": duration_seconds,
            "source_type": source_type,
            "sha256": sha,
            "file_window": file_window,
            "parent_sound_id": parent_sound_id,
            "retention": "explicit-dashboard-library-copy",
        }
        sidecar.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return {"status": "retained", "path": str(target), **metadata}
    except OSError:
        if sidecar:
            sidecar.unlink(missing_ok=True)
        if target:
            target.unlink(missing_ok=True)
        return {
            "status": "error",
            "reason": "Listening completed, but the library audio copy could not be retained",
        }
