from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from oida.engine_stub import StubMossEngine
from oida.pass_provenance import SourceBoundEngine, SourceChangedError
from oida.recipes import get_recipe
from oida.reporting import report


def audio(path: Path, seconds=3):
    sf.write(path, np.sin(np.arange(16000 * seconds) * 0.1) * 0.1, 16000)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_source_pass_is_bound_without_a_private_path(tmp_path):
    path = tmp_path / "private.wav"
    digest = audio(path)
    result = report(StubMossEngine(), str(path), passes=["caption"])
    receipt = result.engine.pass_provenance[0]
    assert receipt["source"] == {
        "sha256": digest,
        "window_s": {"start": 0.0, "end": 3.0},
        "chunk_index": None,
    }
    assert receipt["submitted_audio"]["sha256"] == digest
    assert str(tmp_path) not in str(receipt)
    assert receipt["pass_id"].startswith("pass_")


def test_overlapping_chunks_and_first_chunk_only_passes(tmp_path):
    path = tmp_path / "source.wav"
    source_hash = audio(path)

    class Observed(StubMossEngine):
        def __init__(self):
            super().__init__()
            self.calls = []

        def generate(self, path, *args, **kwargs):
            self.calls.append(
                (str(path), hashlib.sha256(Path(path).read_bytes()).hexdigest())
            )
            return super().generate(path, *args, **kwargs)

    engine = Observed()
    result = report(
        engine,
        str(path),
        passes=["caption", "speech", "music"],
        chunk_seconds=2,
        overlap_seconds=1,
    )
    receipts = result.engine.pass_provenance
    assert len(receipts) == 4
    assert [r["source"]["window_s"] for r in receipts] == [
        {"start": 0.0, "end": 2.0},
        {"start": 1.0, "end": 3.0},
        {"start": 0.0, "end": 2.0},
        {"start": 0.0, "end": 2.0},
    ]
    assert [r["source"]["chunk_index"] for r in receipts] == [0, 1, 0, 0]
    assert all(r["source"]["sha256"] == source_hash for r in receipts)
    assert [r["submitted_audio"]["sha256"] for r in receipts] == [
        h for _, h in engine.calls
    ]
    assert len({r["pass_id"] for r in receipts}) == 4
    assert all(not Path(p).exists() for p, _ in engine.calls)


def test_mutation_during_generate_discards_output(tmp_path):
    path = tmp_path / "source.wav"
    digest = audio(path)

    class Mutating(StubMossEngine):
        def generate(self, *args, **kwargs):
            result = super().generate(*args, **kwargs)
            path.write_bytes(b"changed")
            return result

    engine = SourceBoundEngine(Mutating(), path, digest, 3.0)
    with pytest.raises(SourceChangedError):
        engine.generate(str(path), "fixture", get_recipe("caption_dense").settings)


def test_source_changed_after_cropping_rejects_pass_before_execution(tmp_path):
    path = tmp_path / "source.wav"
    digest = audio(path)
    crop = tmp_path / "crop.wav"
    audio(crop, 1)

    class Never(StubMossEngine):
        def generate(self, *args, **kwargs):
            raise AssertionError("must not run")

    engine = SourceBoundEngine(
        Never(),
        path,
        digest,
        3.0,
        inputs={
            str(crop.resolve()): {"window_s": {"start": 0, "end": 1}, "chunk_index": 0}
        },
    )
    path.write_bytes(b"changed")
    with pytest.raises(SourceChangedError):
        engine.generate(str(crop), "fixture", get_recipe("caption_dense").settings)


def test_input_not_bound_to_report_is_rejected(tmp_path):
    path = tmp_path / "source.wav"
    digest = audio(path)
    engine = SourceBoundEngine(StubMossEngine(), path, digest, 3.0)
    other = tmp_path / "other.wav"
    audio(other)
    with pytest.raises(ValueError, match="not bound"):
        engine.generate(str(other), "fixture", get_recipe("caption_dense").settings)
