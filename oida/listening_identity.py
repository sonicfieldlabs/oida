"""The user-authored listening identity carried by Oída's model harnesses.

``LISTENING.md`` is deliberately distinct from a listening covenant.  A
covenant can refuse, redact, coarsen, or release material at runtime;
``LISTENING.md`` is a bounded perspective that may orient attention and voice
without becoming evidence or changing those gates.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oida.config import data_dir
from oida.storage import atomic_write_text


LISTENING_IDENTITY_FILENAME = "LISTENING.md"
LISTENING_IDENTITY_CONTRACT = "oida/listening-identity/v0.1"
MAX_LISTENING_IDENTITY_CHARS = 4_000
LISTENING_IDENTITY_ROLE = "situated orientation; never evidence, apparatus, permission, or covenant"


class ListeningIdentityConflict(RuntimeError):
    """The document changed after an editor loaded it."""


@dataclass(frozen=True)
class ListeningIdentitySnapshot:
    """One bounded revision of ``LISTENING.md``.

    The text is kept only for the model request that needs it. Event and memory
    records receive :meth:`event_block`, which contains a digest and application
    state but never the document itself.
    """

    text: str = ""
    sha256: str | None = None
    truncated: bool = False

    @property
    def active(self) -> bool:
        return bool(self.text.strip())

    @classmethod
    def empty(cls) -> "ListeningIdentitySnapshot":
        return cls(
            text="",
            sha256=hashlib.sha256(b"").hexdigest(),
            truncated=False,
        )

    def event_block(
        self,
        *,
        application: str | None = None,
        applied_to: list[str] | tuple[str, ...] | None = None,
        declared_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Return export-safe provenance for an event or conversation turn."""

        active = self.active
        block: dict[str, Any] = {
            "contract": LISTENING_IDENTITY_CONTRACT,
            "filename": LISTENING_IDENTITY_FILENAME,
            "active": active,
            "sha256": self.sha256,
            "truncated": self.truncated,
            "application": application or ("available" if active else "inactive"),
            "applied_to": list(dict.fromkeys(str(item) for item in (applied_to or []) if item)),
            "content_included": False,
            "role": LISTENING_IDENTITY_ROLE,
        }
        if declared_sha256:
            block["declared_sha256"] = declared_sha256
        return block

    def host_event_block(self, declaration: Any) -> dict[str, Any]:
        """Reconcile a host's application declaration with this revision.

        Oída cannot retroactively prove how another model listened. A host that
        read and applied the document therefore declares the digest it used.
        Matching is recorded as host-declared, not as daemon-verified evidence.
        """

        if not self.active:
            return self.event_block(application="inactive")
        value = declaration if isinstance(declaration, dict) else {}
        declared = str(value.get("sha256") or "").strip().lower()
        applied = value.get("applied") is True
        if applied and declared and self.sha256 and hmac.compare_digest(declared, self.sha256):
            return self.event_block(
                application="host_declared",
                applied_to=["host_perception"],
                declared_sha256=declared,
            )
        if declared and self.sha256 and not hmac.compare_digest(declared, self.sha256):
            return self.event_block(
                application="revision_mismatch",
                declared_sha256=declared,
            )
        return self.event_block(
            application="available_not_declared",
            declared_sha256=declared or None,
        )


class ListeningIdentityStore:
    """Local plain-text storage for the global listening perspective.

    The file is created empty so the default remains neutral and explicit.
    Reads are bounded even when somebody edits the file outside Oída; this
    keeps a mistaken very large document from consuming a model context.
    """

    def __init__(self, root: Path | None = None) -> None:
        base = Path(root) if root is not None else data_dir()
        self.path = base / LISTENING_IDENTITY_FILENAME
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)

    def _read_bounded(self) -> tuple[str, bool]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = handle.read(MAX_LISTENING_IDENTITY_CHARS + 1)
        except UnicodeDecodeError as exc:
            raise ValueError(f"{LISTENING_IDENTITY_FILENAME} must be UTF-8 text") from exc
        return value[:MAX_LISTENING_IDENTITY_CHARS], len(value) > MAX_LISTENING_IDENTITY_CHARS

    def read(self) -> str:
        """Return the bounded document exactly as stored."""

        text, _ = self._read_bounded()
        return text

    def effective(self) -> str:
        """Return the perspective inserted into a harness, or an empty string."""

        return self.read().strip()

    def snapshot(self) -> ListeningIdentitySnapshot:
        """Read one revision for a complete request, event, or conversation turn."""

        text, truncated = self._read_bounded()
        return ListeningIdentitySnapshot(
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            truncated=truncated,
        )

    def save(self, text: str, *, expected_sha256: str | None = None) -> str:
        if not isinstance(text, str):
            raise ValueError(f"{LISTENING_IDENTITY_FILENAME} must be text")
        if len(text) > MAX_LISTENING_IDENTITY_CHARS:
            raise ValueError(
                f"{LISTENING_IDENTITY_FILENAME} is limited to "
                f"{MAX_LISTENING_IDENTITY_CHARS} characters"
            )
        if "\x00" in text:
            raise ValueError(f"{LISTENING_IDENTITY_FILENAME} cannot contain NUL characters")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{LISTENING_IDENTITY_FILENAME} must be valid UTF-8 text") from exc
        if expected_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError("expected LISTENING.md revision must be a 64-character SHA-256 digest")
        with self._lock:
            if expected_sha256 is not None:
                current = self.snapshot().sha256 or ""
                if not hmac.compare_digest(expected_sha256.lower(), current):
                    raise ListeningIdentityConflict(
                        "LISTENING.md changed after it was read; reload it before saving"
                    )
            atomic_write_text(self.path, text)
        return text

    def status(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "contract": LISTENING_IDENTITY_CONTRACT,
            "filename": LISTENING_IDENTITY_FILENAME,
            "path": str(self.path),
            "text": snapshot.text,
            "active": snapshot.active,
            "characters": len(snapshot.text),
            "max_characters": MAX_LISTENING_IDENTITY_CHARS,
            "truncated": snapshot.truncated,
            "sha256": snapshot.sha256,
            "role": LISTENING_IDENTITY_ROLE,
        }
