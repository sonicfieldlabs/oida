"""Where one listening's time went, stage by stage.

A listening's receipt said how long the model generated, and nothing about the rest: on
24 September a 77-second listening held 43 seconds of generation, and the other 34 were
unaccounted for. Timings are collected only inside a ``collecting()`` block, so code that
runs outside a listening pays nothing and records nothing. They measure this process's wall
clock; they are not a claim about perception.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar

CONTRACT = "oida/listen-timings/v1"

_rows: ContextVar[list[dict] | None] = ContextVar("oida_stage_timings", default=None)


@contextmanager
def collecting():
    """Collect the stages timed inside this block; the yielded callable summarises them."""
    rows: list[dict] = []
    token = _rows.set(rows)
    started = time.perf_counter()

    def summary() -> dict:
        return {
            "contract": CONTRACT,
            "total_ms": round((time.perf_counter() - started) * 1000),
            "stages": [dict(row) for row in rows],
            "basis": "owner process wall clock; stages may nest and need not sum to the total",
        }

    try:
        yield summary
    finally:
        _rows.reset(token)


@contextmanager
def stage(name: str):
    rows = _rows.get()
    if rows is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        rows.append(
            {"stage": name, "ms": round((time.perf_counter() - started) * 1000)}
        )


def record(name: str, ms: float) -> None:
    """Record a stage measured elsewhere, such as a model load inside the engine lock."""
    rows = _rows.get()
    if rows is not None:
        rows.append({"stage": name, "ms": round(ms)})
