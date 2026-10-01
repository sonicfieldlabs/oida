"""Observed process metrics; no inference about model residency or device limits."""

import sys


def process_peak_rss():
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            value = int(value)
        elif sys.platform.startswith("linux"):
            value = int(value * 1024)
        else:
            return dict(
                status="unknown", reason="RSS units not established on this platform"
            )
        return dict(
            status="known",
            bytes=value,
            basis="owner process lifetime high-water RSS; excludes capture child and GPU allocation",
        )
    except (ImportError, AttributeError, OSError):
        return dict(status="unknown", reason="process peak RSS unavailable")
