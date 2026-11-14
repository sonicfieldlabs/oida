"""Window leases: the exact input a radio listening heard, held for a short while so it can be heard again.

A live station is captured temporarily and its audio deleted after listening, so a second
listening would hear other audio. A caller may instead ask for a lease on the window it just
heard, where the operator has declared that the station's terms allow temporary recording
(``lease`` in the capture manifest). The custody is the research samples' own:

* It is the file the model was given, byte for byte: its SHA-256 must equal the digest the
  listening event recorded for its input, or nothing is held.
* It lives in ``<data dir>/window-leases/``, outside the audio folder that GERM and the library
  scan, so it is never a library sound, a generation input or an export. No record, event or
  export names its path; receipts name only the lease's id, digest and expiry.
* It is local only, and ``exportable`` is always false.
* It is short. A lease lasts at most thirty minutes, ends when its holder releases it, and is
  swept at its expiry. Each ending deletes the audio and journals a receipt.

A re-listening names the lease and the digest it expects; Oída listens to the file it holds.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oida.contracts import now_iso
from oida.research_samples import MAX_BYTES, _sha256, _within

CONTRACT = "oida/window-lease/v1"
KIND = "window_lease"
MAX_SECONDS = 1800
IDENTIFIER = re.compile(r"wl_[0-9a-f]{24}")
PUBLIC = ("id", "sha256", "bytes", "expires_at")


class WindowLeaseRefused(ValueError):
    pass


class WindowLeases:
    def __init__(self, root: Path, journal, *, scanned_roots=()):
        self.root = Path(root)
        self.journal = journal
        self.lock = threading.Lock()
        # A lease inside a folder the library scans would become a library sound.
        self.misplaced = any(_within(self.root, Path(r)) for r in scanned_roots if r)

    def _sidecars(self):
        if not self.root.is_dir():
            return []
        return sorted(self.root.glob("wl_*.json"))

    def _read(self, sidecar: Path) -> dict | None:
        # Read only a lease sidecar immediately inside the configured custody root.
        if (
            sidecar.parent != self.root
            or sidecar.suffix != ".json"
            or not IDENTIFIER.fullmatch(sidecar.stem)
            or sidecar.is_symlink()
        ):
            return None
        sidecar = self.root / sidecar.name
        try:
            value = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if value.get("contract") != CONTRACT or not IDENTIFIER.fullmatch(str(value.get("id"))):
            return None
        return value

    def grant(
        self,
        path: Path,
        *,
        seconds: float,
        expected_sha256: str | None,
        source,
        acquisition_id: str,
        event_id: str | None,
        record_id: str | None,
        captured_at: str | None,
        sample_rate: int | None,
        channels: int | None,
        duration_seconds: float | None,
    ) -> dict:
        """Hold ``path`` if it is the listened input. Returns the receipt's summary."""
        policy = getattr(source, "lease", None)
        if policy is None:
            raise WindowLeaseRefused(
                "This station's configured terms do not allow a leased window; its audio is temporary"
            )
        if not 0 < seconds <= min(MAX_SECONDS, policy.max_seconds):
            raise WindowLeaseRefused(
                f"A lease on this station lasts at most {min(MAX_SECONDS, policy.max_seconds)} seconds"
            )
        if self.misplaced:
            raise WindowLeaseRefused(
                "The window-lease folder is inside a scanned audio folder; nothing held"
            )
        if not expected_sha256 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise WindowLeaseRefused("The listening recorded no input digest; nothing held")
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise WindowLeaseRefused("The input is missing or over the lease limit")
        self.sweep()
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            identifier = "wl_" + secrets.token_hex(12)
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
                    raise WindowLeaseRefused(
                        "The captured file is not the input the listening recorded; nothing held"
                    )
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
            leased_at = datetime.now(timezone.utc)
            expires_at = leased_at + timedelta(seconds=seconds)
            metadata = {
                "contract": CONTRACT,
                "id": identifier,
                "file": target.name,
                "sha256": digest,
                "bytes": target.stat().st_size,
                "sample_rate": sample_rate,
                "channels": channels,
                "duration_seconds": duration_seconds,
                "source_id": source.id,
                "station": (source.apparatus or {}).get("station"),
                "basis_of_permission": policy.basis,
                "acquisition_id": acquisition_id,
                "event_id": event_id,
                "record_id": record_id,
                "captured_at": captured_at,
                "leased_at": leased_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "lease_seconds": seconds,
                "listenings": [],
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
            self.journal.save(KIND, identifier, {**metadata, "status": "leased"})
        return {"status": "leased", **{key: metadata[key] for key in PUBLIC}}

    def _end(self, value: dict, status: str, reason: str) -> dict:
        audio = self.root / str(value.get("file") or "")
        existed = audio.is_file() and audio.parent == self.root
        if existed:
            audio.unlink()
        (self.root / f"{value['id']}.json").unlink(missing_ok=True)
        receipt = {
            **value,
            "status": status,
            "deleted_at": now_iso(),
            "audio_deleted": existed,
            "reason": reason[:200],
        }
        self.journal.save(KIND, value["id"], receipt)
        return receipt

    def sweep(self, now: datetime | None = None) -> list[dict]:
        """Delete every expired lease and journal a receipt for each."""
        now = now or datetime.now(timezone.utc)
        removed = []
        with self.lock:
            if self.root.is_dir():
                for stale in self.root.glob(".wl_*.part"):
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
                removed.append(self._end(value, "expired", "the lease's time elapsed"))
        return removed

    def release(self, identifier: str, reason: str = "released by its holder") -> dict:
        """End one lease before it expires: delete its audio and journal the receipt."""
        if not IDENTIFIER.fullmatch(identifier):
            raise KeyError(identifier)
        with self.lock:
            value = self._read(self.root / f"{identifier}.json")
            if value is None:
                raise KeyError(identifier)
            return self._end(value, "released", reason)

    def release_all(self, reason: str) -> list[dict]:
        """End every lease, for a shutdown or a covenant that now forbids retention."""
        with self.lock:
            return [
                self._end(value, "released", reason)
                for value in (self._read(sidecar) for sidecar in self._sidecars())
                if value is not None
            ]

    def entries(self) -> list[dict]:
        self.sweep()
        rows = []
        for sidecar in self._sidecars():
            value = self._read(sidecar)
            if value is not None and (self.root / str(value.get("file"))).is_file():
                rows.append({k: v for k, v in value.items() if k != "file"})
        return sorted(rows, key=lambda row: row.get("leased_at") or "", reverse=True)

    def audio(self, identifier: str, expected_sha256: str) -> tuple[Path, dict]:
        """The leased file, only if unexpired, named by its digest, and its bytes still match."""
        if not IDENTIFIER.fullmatch(identifier):
            raise KeyError(identifier)
        self.sweep()
        value = self._read(self.root / f"{identifier}.json")
        if value is None:
            raise KeyError(identifier)
        if expected_sha256 != value["sha256"]:
            raise WindowLeaseRefused("The lease holds another window than the one named")
        path = self.root / str(value["file"])
        if (
            not path.is_file()
            or path.is_symlink()
            or path.parent != self.root
            or not _within(path, self.root)
        ):
            raise KeyError(identifier)
        if _sha256(path) != value["sha256"]:
            raise WindowLeaseRefused("The leased window no longer matches its recorded digest")
        return path, value

    def note_listening(self, identifier: str, acquisition_id: str, record_id: str | None) -> None:
        """Record on the lease that it was heard again (ids only)."""
        if not IDENTIFIER.fullmatch(identifier):
            raise KeyError(identifier)
        with self.lock:
            sidecar = self.root / f"{identifier}.json"
            value = self._read(sidecar)
            if value is None:
                return
            value.setdefault("listenings", []).append(
                {"acquisition_id": acquisition_id, "record_id": record_id, "at": now_iso()}
            )
            value["listenings"] = value["listenings"][-32:]
            partial = self.root / f".{identifier}.json.part"
            partial.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            os.replace(partial, sidecar)
