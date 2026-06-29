from __future__ import annotations

import os
import resource
import sys
from pathlib import Path


def process_metrics() -> dict[str, object]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "max_rss_mb": _ru_maxrss_to_mb(usage.ru_maxrss),
        "user_cpu_s": usage.ru_utime,
        "system_cpu_s": usage.ru_stime,
    }


def _ru_maxrss_to_mb(value: int | float) -> float:
    if sys.platform == "darwin":
        return round(float(value) / (1024 * 1024), 3)
    return round(float(value) / 1024, 3)
