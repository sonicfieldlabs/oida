"""A weight file is hashed once while it stays the same file on disk (Phase 3, 24 Sept 2026)."""

from __future__ import annotations

import hashlib
import os

import pytest

from oida import stage_timing
from oida.pass_provenance import WeightHashCache, weight_inventory


def model(tmp_path):
    directory = tmp_path / "weights"
    directory.mkdir()
    for index in range(2):
        (directory / f"model-{index}.safetensors").write_bytes(
            f"shard {index}".encode()
        )
    return directory


def test_an_unchanged_file_reuses_its_digest_and_says_so(tmp_path):
    directory = model(tmp_path)
    cache = WeightHashCache(tmp_path / "cache.json")
    first, again = {}, {}
    fresh = weight_inventory(directory, cache=cache, verification=first)
    assert first["method"] == "sha256" and first["files_hashed"] == 2
    reloaded = WeightHashCache(tmp_path / "cache.json")
    reused = weight_inventory(directory, cache=reloaded, verification=again)
    assert reused == fresh, (
        "the identity of the model does not depend on how it was checked"
    )
    assert again["method"] == "stat-cache" and again["files_reused"] == 2
    assert "oldest_hash_at" in again
    assert fresh == weight_inventory(directory), (
        "the same digests as an uncached inventory"
    )


@pytest.mark.parametrize("change", ["content", "same_size_same_mtime"])
def test_any_write_to_a_file_forces_a_fresh_hash(tmp_path, change):
    directory = model(tmp_path)
    cache = WeightHashCache(tmp_path / "cache.json")
    weight_inventory(directory, cache=cache)
    shard = directory / "model-0.safetensors"
    stat = shard.stat()
    if change == "content":
        shard.write_bytes(b"changed shard")
    else:
        shard.write_bytes(b"shard X")  # same length as "shard 0"
        os.utime(shard, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    verification = {}
    inventory = weight_inventory(directory, cache=cache, verification=verification)
    assert verification["method"] == "mixed" and verification["files_hashed"] == 1
    digest = next(f["sha256"] for f in inventory["files"] if f["file"] == shard.name)
    assert digest == hashlib.sha256(shard.read_bytes()).hexdigest()


def test_an_unreadable_cache_only_means_hashing_again(tmp_path):
    directory = model(tmp_path)
    (tmp_path / "cache.json").write_text("{not json")
    verification = {}
    weight_inventory(
        directory,
        cache=WeightHashCache(tmp_path / "cache.json"),
        verification=verification,
    )
    assert verification["method"] == "sha256"


def test_stage_timings_are_collected_only_inside_a_listening():
    with stage_timing.stage("outside"):
        pass
    with stage_timing.collecting() as summary:
        with stage_timing.stage("dsp"):
            pass
        stage_timing.record("model_load", 1500.4)
        timings = summary()
    assert [row["stage"] for row in timings["stages"]] == ["dsp", "model_load"]
    assert (
        timings["stages"][1]["ms"] == 1500
        and timings["contract"] == "oida/listen-timings/v1"
    )
