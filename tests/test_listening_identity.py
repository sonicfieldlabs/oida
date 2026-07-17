from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from oida.listening_identity import (
    LISTENING_IDENTITY_CONTRACT,
    LISTENING_IDENTITY_FILENAME,
    MAX_LISTENING_IDENTITY_CHARS,
    ListeningIdentityConflict,
    ListeningIdentityStore,
)


def test_store_creates_an_empty_named_identity_and_round_trips_markdown() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = ListeningIdentityStore(root)

        assert store.path == root / LISTENING_IDENTITY_FILENAME
        assert store.path.exists()
        assert store.path.read_text(encoding="utf-8") == ""
        assert store.status()["active"] is False

        markdown = "# Listening identity\n\nListen like a careful guest.\n"
        store.save(markdown)

        assert ListeningIdentityStore(root).read() == markdown
        status = store.status()
        assert status["filename"] == "LISTENING.md"
        assert status["path"] == str(root / "LISTENING.md")
        assert status["active"] is True
        assert status["characters"] == len(markdown)
        assert status["truncated"] is False
        assert len(status["sha256"]) == 64

        snapshot = store.snapshot()
        event_block = snapshot.event_block(
            application="model_prompt",
            applied_to=["model_perception:caption"],
        )
        assert event_block["contract"] == LISTENING_IDENTITY_CONTRACT
        assert event_block["sha256"] == status["sha256"]
        assert event_block["content_included"] is False
        assert "text" not in event_block


def test_host_declaration_distinguishes_matching_missing_and_changed_revisions() -> None:
    with TemporaryDirectory() as tmp:
        store = ListeningIdentityStore(Path(tmp))
        store.save("Listen for infrastructures without pretending to identify them.")
        snapshot = store.snapshot()

        matching = snapshot.host_event_block(
            {
                "contract": LISTENING_IDENTITY_CONTRACT,
                "sha256": snapshot.sha256,
                "applied": True,
            }
        )
        missing = snapshot.host_event_block(None)
        changed = snapshot.host_event_block(
            {
                "contract": LISTENING_IDENTITY_CONTRACT,
                "sha256": "0" * 64,
                "applied": True,
            }
        )

        assert matching["application"] == "host_declared"
        assert matching["applied_to"] == ["host_perception"]
        assert missing["application"] == "available_not_declared"
        assert changed["application"] == "revision_mismatch"


def test_store_bounds_manual_edits_and_rejects_oversized_api_writes() -> None:
    with TemporaryDirectory() as tmp:
        store = ListeningIdentityStore(Path(tmp))
        manually_written = "x" * (MAX_LISTENING_IDENTITY_CHARS + 20)
        store.path.write_text(manually_written, encoding="utf-8")

        assert store.read() == "x" * MAX_LISTENING_IDENTITY_CHARS
        assert store.status()["truncated"] is True

        with pytest.raises(ValueError, match="limited to 4000 characters"):
            store.save(manually_written)
        with pytest.raises(ValueError, match="NUL"):
            store.save("listen\x00here")


def test_store_can_refuse_a_stale_editor_revision() -> None:
    with TemporaryDirectory() as tmp:
        store = ListeningIdentityStore(Path(tmp))
        first_revision = store.status()["sha256"]
        store.save("First position.", expected_sha256=first_revision)

        with pytest.raises(ListeningIdentityConflict, match="changed after it was read"):
            store.save("Stale overwrite.", expected_sha256=first_revision)
