"""Read operator-selected evidence locally; never fetch a caller-supplied URL."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 131_072


class EvidenceUnavailable(ValueError):
    pass


def _bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise EvidenceUnavailable(
            "Configured apparatus evidence is unavailable"
        ) from exc
    if len(data) > MAX_BYTES:
        raise EvidenceUnavailable(
            "Configured apparatus evidence exceeds its size limit"
        )
    return data


def _json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")

    try:
        result = json.loads(
            data,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
        if not isinstance(result, dict):
            raise ValueError("Not an object")
        return result
    except (ValueError, UnicodeError) as exc:
        raise EvidenceUnavailable(
            "Configured apparatus evidence is invalid JSON"
        ) from exc


def resolve_evidence(
    manifest: Path,
    access: dict,
    *,
    now: datetime | None = None,
    bindings=None,
    preprocessing=None,
) -> dict:
    """Resolve approved declarations, not physical truth or model capability.

    Evidence files repeat the exact approved capture or representation block.
    A changed claim cannot reuse a file approved for another subject/declaration.
    """
    now = now or datetime.now(timezone.utc)
    raw = _bytes(manifest)
    index = _json(raw)
    if (
        set(index) != {"contract", "entries"}
        or index["contract"] != "oida/apparatus-evidence/v1"
        or not isinstance(index["entries"], list)
        or len(index["entries"]) > 256
    ):
        raise EvidenceUnavailable("Unsupported apparatus evidence manifest")
    root = manifest.resolve().parent
    refs, seen, unresolved = [], set(), []
    model_refs = []
    for entry in index["entries"]:
        if not isinstance(entry, dict) or set(entry) != {
            "ref",
            "kind",
            "file",
            "sha256",
            "expires_at",
        }:
            raise EvidenceUnavailable("Invalid apparatus evidence entry")
        if any(not isinstance(value, str) or not value for value in entry.values()):
            raise EvidenceUnavailable("Invalid apparatus evidence entry value")
        if entry["ref"] in seen or entry["kind"] not in {
            "capture",
            "sampled_representation",
            "model_input",
        }:
            raise EvidenceUnavailable(
                "Duplicate reference or unsupported evidence kind"
            )
        seen.add(entry["ref"])
        if len(entry["sha256"]) != 64 or any(
            c not in "0123456789abcdef" for c in entry["sha256"]
        ):
            raise EvidenceUnavailable("Invalid evidence digest")
        try:
            expiry = datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00"))
            if expiry.utcoffset() is None:
                raise ValueError("No timezone")
        except ValueError as exc:
            raise EvidenceUnavailable(
                "Evidence expiry must be an aware timestamp"
            ) from exc
        if expiry <= now:
            unresolved.append({"ref": entry["ref"], "reason": "expired"})
            continue
        relative = Path(entry["file"])
        path = (root / relative).resolve()
        if (
            relative.is_absolute()
            or not path.is_relative_to(root)
            or ".." in relative.parts
        ):
            raise EvidenceUnavailable(
                "Evidence file must remain within the configured directory"
            )
        data = _bytes(path)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise EvidenceUnavailable("Apparatus evidence integrity check failed")
        evidence = _json(data)
        expected = {
            "ref": entry["ref"],
            "subject_ref": access["subject_ref"],
            "kind": entry["kind"],
            "declaration": access[entry["kind"]],
        }
        if entry["kind"] == "model_input":
            prepared = bindings or {}
            expected["binding_ids"] = {
                k: v["binding_id"]
                for k, v in prepared.items()
                if v.get("status") == "prepared"
            }
            expected["preprocessing"] = preprocessing or []
            if not prepared or len(expected["binding_ids"]) != len(prepared):
                unresolved.append(
                    {"ref": entry["ref"], "reason": "prepared input unavailable"}
                )
                continue
        if json.dumps(evidence, sort_keys=True) == json.dumps(expected, sort_keys=True):
            refs.append(entry["ref"])
            if entry["kind"] == "model_input":
                model_refs.append(entry["ref"])
        else:
            unresolved.append(
                {"ref": entry["ref"], "reason": "subject or declaration mismatch"}
            )
    return {
        "resolved_refs": refs,
        "model_refs": model_refs,
        "unresolved": unresolved,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "basis": "operator-selected declarations; not independent calibration verification",
    }
