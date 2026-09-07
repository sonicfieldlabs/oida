"""Content-free attribution of the adapter that actually returned each pass."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def weight_inventory(directory: Path) -> dict[str, Any]:
    """Hash local safetensors at load time, never infer bytes from a model name.

    The digest identifies a sorted filename/digest manifest, not concatenated
    tensors. Call while loading, and retain alongside the resident model.
    """
    files = sorted(directory.glob("*.safetensors"))
    if not files:
        return {
            "status": "unknown",
            "reason": "No local safetensors inventory available",
        }
    inventory = []
    for path in files:
        digest = hashlib.sha256()
        before = path.stat()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Weights changed while their provenance was being read")
        inventory.append(
            {"file": path.name, "sha256": digest.hexdigest(), "bytes": after.st_size}
        )
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    return {
        "status": "known",
        "algorithm": "sha256-manifest-v1",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "files": inventory,
    }


def pass_receipt(
    *,
    model: str,
    provider: str,
    model_kind: str,
    revision: str | None = None,
    revision_basis: str = "unknown",
    weights: dict | None = None,
    effective_input: dict | None = None,
) -> dict:
    return {
        "contract": "oida/pass-provenance/v1",
        "model": model,
        "provider": provider,
        "model_kind": model_kind,
        "revision": {"value": revision, "basis": revision_basis},
        "weights": weights
        or {"status": "unknown", "reason": "Adapter cannot inspect provider weights"},
        "effective_input": effective_input
        or {
            "status": "unknown",
            "reason": "Provider preprocessing is not observable by this adapter",
        },
    }


class SourceChangedError(ValueError):
    """A report must not retain output attributed to different source bytes."""


def file_identity(stat) -> tuple:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def audio_fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    current = path.stat()
    if file_identity(before) != file_identity(after) or file_identity(
        after
    ) != file_identity(current):
        raise SourceChangedError("Audio changed while provenance was being read")
    return {"sha256": digest.hexdigest(), "bytes": after.st_size}


class SourceBoundEngine:
    """Request-scoped adapter; no shared engine state or paths enter receipts.

    Bind the byte files submitted to the adapter, not hidden provider tensors.
    Temporary crop hashes remain meaningful after the files are removed.
    """

    def __init__(
        self,
        engine,
        source_path: str | Path,
        source_sha256: str,
        duration_s: float,
        *,
        inputs: dict | None = None,
    ):
        self.engine = engine
        self.profile = engine.profile
        self.source_path = Path(source_path).resolve()
        self.source_sha256 = source_sha256
        self.source_identity = file_identity(self.source_path.stat())
        source_fingerprint = audio_fingerprint(self.source_path)
        if source_fingerprint["sha256"] != source_sha256:
            raise SourceChangedError(
                "Source bytes differ from the inspected report source"
            )
        self.verify_source()
        self.inputs = inputs or {
            str(self.source_path): {
                "window_s": {"start": 0.0, "end": duration_s},
                "chunk_index": None,
            }
        }
        self.fingerprints = {
            key: source_fingerprint
            if key == str(self.source_path)
            else audio_fingerprint(Path(key))
            for key in self.inputs
        }

    def verify_source(self):
        if file_identity(self.source_path.stat()) != self.source_identity:
            raise SourceChangedError(
                "Source bytes differ from the inspected report source"
            )

    def generate(self, audio_path, prompt, settings, thinking_budget=None):
        from copy import deepcopy
        from dataclasses import replace
        from uuid import uuid4

        path = Path(audio_path).resolve()
        key = str(path)
        if key not in self.inputs:
            raise ValueError("Pass audio is not bound to this report source")
        self.verify_source()
        expected = self.fingerprints[key]
        if audio_fingerprint(path) != expected:
            raise SourceChangedError("Submitted audio changed before the pass")
        result = self.engine.generate(
            audio_path, prompt, settings, thinking_budget=thinking_budget
        )
        if audio_fingerprint(path) != expected:
            raise SourceChangedError("Submitted audio changed during the pass")
        self.verify_source()
        receipts = deepcopy(result.pass_provenance) or [
            pass_receipt(
                model=result.model,
                provider=result.profile,
                model_kind=settings.model_kind,
            )
        ]
        pass_id = "pass_" + uuid4().hex
        for receipt in receipts:
            receipt.update(
                pass_id=pass_id,
                source={"sha256": self.source_sha256, **deepcopy(self.inputs[key])},
                submitted_audio={
                    **expected,
                    "basis": "encoded file submitted to adapter",
                },
            )
        return replace(result, pass_provenance=receipts)
