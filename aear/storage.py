from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["atomic_write_text", "write_json_atomic"]


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write text durably and atomically.

    The content is written to a temporary file in the same directory and then
    ``os.replace``-d into place, so a crash mid-write or a concurrent reader never
    observes a truncated or interleaved file. Several stores here are read back on
    daemon start and silently discard the file on ``JSONDecodeError``; an in-place
    ``write_text`` could therefore lose a user's entire history on an ill-timed crash.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def write_json_atomic(path: str | Path, data: Any, *, indent: int = 2) -> Path:
    return atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")
