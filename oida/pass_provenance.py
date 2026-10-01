"""Content-free attribution of the adapter that actually returned each pass."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _file_state(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


class WeightHashCache:
    """The SHA-256 of a weight file, reused while the file's identity on disk is unchanged.

    Hashing MOSS-Audio's 10.4 GB took 5.4 s on an M1 Max, and every load hashed twice, so a
    swap between two models cost about 22 s before any tensor moved. A digest is reused only
    while device, inode, size, modification and change times all match the ones recorded
    around the hashing that produced it; any write to the file changes at least one. What a
    reuse attests is said in the load's verification record, never inside the inventory,
    so a model's identity does not depend on how its bytes were last checked.
    """

    def __init__(self, path: Path | None):
        self.path = path
        self._rows: dict[str, dict] = {}
        if path is not None:
            try:
                value = json.loads(path.read_text())
                if isinstance(value, dict) and value.get("version") == 1:
                    self._rows = {
                        key: row
                        for key, row in (value.get("files") or {}).items()
                        if isinstance(row, dict)
                    }
            except (OSError, ValueError):
                self._rows = {}

    def get(self, path: Path, state: list[int]) -> dict | None:
        row = self._rows.get(str(path.resolve()))
        if row and row.get("state") == state and isinstance(row.get("sha256"), str):
            return row
        return None

    def put(self, path: Path, state: list[int], sha256: str) -> dict:
        from datetime import datetime, timezone

        row = {
            "state": state,
            "sha256": sha256,
            "hashed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._rows[str(path.resolve())] = row
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"version": 1, "files": self._rows}))
                os.replace(temporary, self.path)
            except OSError:
                pass  # A cache that cannot be written only means the next load hashes again.
        return row


def weight_inventory(
    directory: Path,
    *,
    cache: WeightHashCache | None = None,
    verification: dict | None = None,
) -> dict[str, Any]:
    """Hash local safetensors at load time, never infer bytes from a model name.

    The digest identifies a sorted filename/digest manifest, not concatenated
    tensors. Call while loading, and retain alongside the resident model. With a
    cache, a file whose on-disk identity is unchanged since it was hashed keeps that
    digest; ``verification`` (if given) is filled with what was hashed and what reused.
    """
    files = sorted(directory.glob("*.safetensors"))
    if not files:
        return {
            "status": "unknown",
            "reason": "No local safetensors inventory available",
        }
    inventory = []
    hashed = reused = 0
    hashed_at: list[str] = []
    for path in files:
        before = _file_state(path)
        row = cache.get(path, before) if cache is not None else None
        if row is not None:
            reused += 1
            hashed_at.append(row["hashed_at"])
            inventory.append(
                {"file": path.name, "sha256": row["sha256"], "bytes": before[2]}
            )
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = _file_state(path)
        if before != after:
            raise ValueError("Weights changed while their provenance was being read")
        hashed += 1
        if cache is not None:
            hashed_at.append(cache.put(path, after, digest.hexdigest())["hashed_at"])
        inventory.append(
            {"file": path.name, "sha256": digest.hexdigest(), "bytes": after[2]}
        )
    if verification is not None:
        verification.update(
            method="sha256" if not reused else "stat-cache" if not hashed else "mixed",
            files_hashed=hashed,
            files_reused=reused,
            **({"oldest_hash_at": min(hashed_at)} if hashed_at else {}),
            basis=(
                "every file hashed now"
                if not reused
                else "reused digests: device, inode, size, mtime and ctime unchanged since hashing"
            ),
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
    #: How the kind was established. "loaded_model" means it was read from the
    #: model actually loaded; anything else says what it was read from instead.
    #: Added after a Thinking run recorded the right weights with the wrong kind,
    #: because the request had selected by id and the kind kept its default.
    model_kind_basis: str = "requested",
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
        "model_kind_basis": model_kind_basis,
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
        from contextlib import nullcontext
        from oida.input_binding import enforce_input_bindings, has_input_bindings

        prepare = getattr(self.engine, "prepare_input_binding", None)
        binding = (
            prepare(audio_path, settings.model_kind)
            if prepare is not None and not has_input_bindings()
            else {}
        )
        guard = (
            enforce_input_bindings({settings.model_kind: binding["binding_id"]})
            if binding.get("status") == "prepared"
            else nullcontext()
        )
        with guard:
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
