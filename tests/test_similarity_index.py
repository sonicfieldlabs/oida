"""Similar sounds are found without re-reading every trace (Phase 3, 24 September 2026).

The Testing workspace held 82 traces totalling 438 MB; each listening parsed and deep-copied
all of them twice. The index keeps each trace's vector and preview per file version.
"""

from __future__ import annotations

import json
import os

from oida.memory import AkousmataStore


def trace(identifier, rms, created):
    return {
        "id": identifier,
        "title": identifier,
        "createdAt": created,
        "features": features(rms),
        "event": {"blob": "x" * 1000},
    }


def features(rms):
    return {
        "rmsDbfs": rms,
        "peakDbfs": rms + 10,
        "duration_s": 10,
        "sample_rate": 44100,
        "channels": 2,
        "spectralCentroidHz": 1500,
        "bandEnergy": {"low": 0.5},
    }


def store(tmp_path):
    value = AkousmataStore(root=tmp_path / "akousmata")
    value.traces_dir.mkdir(parents=True)
    return value


def write(value, identifier, **kwargs):
    path = value.traces_dir / f"{identifier}.json"
    path.write_text(json.dumps(trace(identifier, **kwargs)))
    return path


def test_similar_traces_are_ranked_and_the_index_persists(tmp_path):
    value = store(tmp_path)
    write(value, "near", rms=-20, created="2026-09-24T01:00:00Z")
    write(value, "far", rms=-90, created="2026-09-24T02:00:00Z")
    event = {"features": features(-21)}
    first = value.similar_to_event(event)
    assert [m["trace"]["id"] for m in first][0] == "near"
    assert "event" not in first[0]["trace"], "only the preview travels"
    assert (value.root / "cache" / "similarity-v1.json").is_file()
    fresh = AkousmataStore(root=tmp_path / "akousmata")
    reads = []
    original = json.loads
    fresh_result = None
    import oida.memory as memory

    def counting(text, *args, **kwargs):
        reads.append(len(text))
        return original(text, *args, **kwargs)

    memory.json.loads = counting
    try:
        fresh_result = fresh.similar_to_event(event)
    finally:
        memory.json.loads = original
    assert fresh_result == first
    assert all(size < 5000 for size in reads), (
        "a restart reads the index, not the traces"
    )


def test_a_changed_or_removed_trace_is_read_again_or_dropped(tmp_path):
    value = store(tmp_path)
    path = write(value, "one", rms=-20, created="2026-09-24T01:00:00Z")
    write(value, "two", rms=-20, created="2026-09-24T02:00:00Z")
    event = {"features": features(-20)}
    assert {m["trace"]["id"] for m in value.similar_to_event(event)} == {"one", "two"}
    changed = trace("one", rms=-20, created="2026-09-24T01:00:00Z")
    changed["title"] = "renamed and longer"
    path.write_text(json.dumps(changed))
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1000))
    titles = {
        m["trace"]["id"]: m["trace"]["title"] for m in value.similar_to_event(event)
    }
    assert titles["one"] == "renamed and longer"
    (value.traces_dir / "two.json").unlink()
    assert [m["trace"]["id"] for m in value.similar_to_event(event)] == ["one"]
