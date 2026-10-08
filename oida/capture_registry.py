"""Owner-managed radio registrations, separate from operator configuration."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from oida.public_fetch import public_url
from oida.source_capture import CaptureSource


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    url: str = Field(min_length=1, max_length=2048)
    consent_ref: str = Field(min_length=1, max_length=512)
    rights_ref: str = Field(min_length=1, max_length=512)
    source_ref: str = Field(min_length=1, max_length=512)
    retention: str = Field(pattern="^temp_only$")
    consent: str = Field(pattern="^granted$")
    playlist_policy: Literal["refuse", "finite"] = "refuse"
    max_seconds: float = Field(default=30.0, gt=0, le=60)


class CaptureRegistry:
    def __init__(self, root, configured):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "registered-capture.json"
        self.configured = set(configured)

    @contextmanager
    def locked(self):
        try:
            import fcntl
        except ImportError as exc:
            raise RuntimeError(
                "Runtime radio registration requires POSIX file locking"
            ) from exc
        with (self.root / ".capture-registry.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self):
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or self.path.stat().st_size > 512 * 1024:
            raise ValueError("Invalid capture registry")
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict) or len(value) > 64:
            raise ValueError("Invalid capture registry bounds")
        return value

    def _write(self, value):
        fd, temp = tempfile.mkstemp(prefix=".capture-registry-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temp).unlink(missing_ok=True)

    def register(self, request):
        url = public_url(request.url)
        parts = urlsplit(url)
        if parts.path.lower().endswith(".pls") or (
            parts.path.lower().endswith((".m3u", ".m3u8"))
            and request.playlist_policy != "finite"
        ):
            raise ValueError(
                "Playlist/HLS registration unavailable; direct streams only"
            )
        port = parts.port
        host = parts.hostname.lower()
        netloc = (f"[{host}]" if ":" in host else host) + (
            f":{port}"
            if port and port != (443 if parts.scheme == "https" else 80)
            else ""
        )
        url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
        identifier = "radio_" + hashlib.sha256(url.encode()).hexdigest()[:24]
        with self.locked():
            items = self._read()
            if identifier in self.configured:
                raise ValueError("Registration collides with configured source")
            value = {**request.model_dump(), "url": url, "id": identifier}
            prior = items.get(identifier)
            if prior is not None:
                prior.setdefault("playlist_policy", "refuse")
            if (
                prior is not None
                and {k: v for k, v in prior.items() if k != "revision"} != value
            ):
                raise ValueError(
                    "Existing source has different permission scope; revoke first"
                )
            if identifier not in items and len(items) >= 64:
                raise ValueError("Runtime source cap is 64")
            value["revision"] = prior["revision"] if prior else uuid.uuid4().hex
            items[identifier] = value
            self._write(items)
            return value

    def entries(self):
        with self.locked():
            return list(self._read().values())

    def source(self, identifier):
        item = next((v for v in self.entries() if v["id"] == identifier), None)
        if item is None:
            return None
        return CaptureSource(
            id=item["id"],
            adapter="radio",
            input=item["url"],
            sample_rate=192000,
            channels=2,
            max_seconds=item["max_seconds"],
            producer_id="oida:registered-radio",
            consent="granted",
            consent_ref=item["consent_ref"],
            runtime_registered=True,
            playlist_policy=item.get("playlist_policy", "refuse"),
            registration_revision=item["revision"],
            rights_ref=item["rights_ref"],
            source_ref=item["source_ref"],
        )

    def revoke(self, identifier):
        with self.locked():
            items = self._read()
            if identifier not in items:
                raise ValueError("Unknown runtime source")
            items.pop(identifier)
            self._write(items)
