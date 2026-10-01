"""Research samples: the exact input a radio listening heard, kept for local research.

Public radio is captured temporarily and its audio deleted after listening. A station
the operator has opted in (``retention: "research_sample"`` in the capture manifest,
with an attestation) may instead keep one sample per listening, under these rules:

* It is the file the model was given, byte for byte: its SHA-256 must equal the
  digest the listening event recorded for its input, or nothing is kept.
* It lives in ``<data dir>/research-samples/``, outside the audio folder that GERM and
  the library scan, so it is never a library sound, a generation input or an export.
  No record, event or export names its path; the acquisition receipt names only its id,
  digest and expiry.
* It is local only: the listing and audio routes are Oída's own, for the operator's
  processes on this machine. ``exportable`` is always false.
* It expires. A sweep deletes it at ``expires_at`` and journals a deletion receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oida.contracts import now_iso

CONTRACT = "oida/research-sample/v1"
KIND = "research_sample"
MAX_BYTES = 64 * 1024 * 1024
IDENTIFIER = re.compile(r"^rs_[0-9a-f]{24}$")


class ResearchSampleRefused(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            if size > MAX_BYTES:
                raise ResearchSampleRefused("Research sample exceeds its byte limit")
            digest.update(block)
    return digest.hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class ResearchSamples:
    def __init__(self, root: Path, journal, *, scanned_roots=()):
        self.root = Path(root)
        self.journal = journal
        self.lock = threading.Lock()
        # A sample inside a folder the library scans would become a library sound.
        self.misplaced = any(_within(self.root, Path(r)) for r in scanned_roots if r)

    def _sidecars(self):
        if not self.root.is_dir():
            return []
        return sorted(self.root.glob("rs_*.json"))

    def _read(self, sidecar: Path) -> dict | None:
        try:
            value = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if value.get("contract") != CONTRACT or not IDENTIFIER.match(
            str(value.get("id"))
        ):
            return None
        return value

    def retain(
        self,
        path: Path,
        *,
        expected_sha256: str | None,
        source,
        acquisition_id: str,
        event_id: str | None,
        record_id: str | None,
        captured_at: str | None,
        sample_rate: int | None,
        channels: int | None,
    ) -> dict:
        """Keep ``path`` if it is the listened input. Returns the receipt's summary."""
        research = source.research
        if source.retention != "research_sample" or research is None:
            raise ResearchSampleRefused(
                "This station is not opted in to research samples"
            )
        if self.misplaced:
            raise ResearchSampleRefused(
                "The research-sample folder is inside a scanned audio folder; nothing kept"
            )
        if not expected_sha256 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ResearchSampleRefused(
                "The listening recorded no input digest; nothing kept"
            )
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ResearchSampleRefused("The input is missing or over the sample limit")
        self.sweep()
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            identifier = "rs_" + secrets.token_hex(12)
            target = self.root / f"{identifier}{path.suffix.lower() or '.wav'}"
            partial = self.root / f".{identifier}.part"
            try:
                fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as dst, path.open("rb") as src:
                    for block in iter(lambda: src.read(1024 * 1024), b""):
                        dst.write(block)
                    dst.flush()
                    os.fsync(dst.fileno())
                digest = _sha256(partial)
                if digest != expected_sha256:
                    raise ResearchSampleRefused(
                        "The captured file is not the input the listening recorded; nothing kept"
                    )
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
            retained_at = datetime.now(timezone.utc)
            expires_at = retained_at + timedelta(seconds=research.ttl_seconds)
            metadata = {
                "contract": CONTRACT,
                "id": identifier,
                "file": target.name,
                "sha256": digest,
                "bytes": target.stat().st_size,
                "sample_rate": sample_rate,
                "channels": channels,
                "source_id": source.id,
                "station": (source.apparatus or {}).get("station"),
                "attestation": research.attestation,
                "acquisition_id": acquisition_id,
                "event_id": event_id,
                "record_id": record_id,
                "captured_at": captured_at,
                "retained_at": retained_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "ttl_seconds": research.ttl_seconds,
                "audience": "local",
                "exportable": False,
                "basis": "sha256 equals the listening event's segment.data_ref.sha256",
            }
            sidecar = self.root / f"{identifier}.json"
            try:
                fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(metadata, stream, ensure_ascii=False)
            except OSError:
                target.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                raise
            self.journal.save(KIND, identifier, {**metadata, "status": "retained"})
        return {
            "status": "retained",
            "id": identifier,
            "sha256": digest,
            "bytes": metadata["bytes"],
            "expires_at": metadata["expires_at"],
        }

    def sweep(self, now: datetime | None = None) -> list[dict]:
        """Delete every expired sample and journal a receipt for each."""
        now = now or datetime.now(timezone.utc)
        removed = []
        with self.lock:
            if self.root.is_dir():
                for stale in self.root.glob(".rs_*.part"):
                    stale.unlink(missing_ok=True)
            for sidecar in self._sidecars():
                value = self._read(sidecar)
                if value is None:
                    continue
                try:
                    expires = datetime.fromisoformat(value["expires_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                if expires > now:
                    continue
                audio = self.root / str(value.get("file") or "")
                existed = audio.is_file() and audio.parent == self.root
                if existed:
                    audio.unlink()
                sidecar.unlink(missing_ok=True)
                receipt = {
                    **value,
                    "status": "expired",
                    "deleted_at": now_iso(),
                    "audio_deleted": existed,
                    "reason": "research-sample retention elapsed",
                }
                self.journal.save(KIND, value["id"], receipt)
                removed.append(receipt)
        return removed

    def delete(self, identifier: str, reason: str = "operator request") -> dict:
        """Delete one sample before it expires, with a receipt."""
        if not IDENTIFIER.match(identifier):
            raise KeyError(identifier)
        with self.lock:
            sidecar = self.root / f"{identifier}.json"
            value = self._read(sidecar)
            if value is None:
                raise KeyError(identifier)
            audio = self.root / str(value.get("file") or "")
            existed = audio.is_file() and audio.parent == self.root
            if existed:
                audio.unlink()
            sidecar.unlink(missing_ok=True)
            receipt = {
                **value,
                "status": "deleted",
                "deleted_at": now_iso(),
                "audio_deleted": existed,
                "reason": reason[:200],
            }
            self.journal.save(KIND, identifier, receipt)
        return receipt

    def entries(self) -> list[dict]:
        self.sweep()
        rows = []
        for sidecar in self._sidecars():
            value = self._read(sidecar)
            if value is not None and (self.root / str(value.get("file"))).is_file():
                rows.append({k: v for k, v in value.items() if k != "file"})
        return sorted(rows, key=lambda row: row.get("retained_at") or "", reverse=True)

    def audio(self, identifier: str) -> tuple[Path, dict]:
        """The sample's file, only if it is still unexpired and its bytes still match."""
        if not IDENTIFIER.match(identifier):
            raise KeyError(identifier)
        self.sweep()
        value = self._read(self.root / f"{identifier}.json")
        if value is None:
            raise KeyError(identifier)
        path = self.root / str(value["file"])
        if not path.is_file() or path.is_symlink() or path.parent != self.root or not _within(path, self.root):
            raise KeyError(identifier)
        if _sha256(path) != value["sha256"]:
            raise ResearchSampleRefused(
                "The sample no longer matches its recorded digest"
            )
        return path, value
