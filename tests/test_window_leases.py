"""A window lease: the exact input a radio listening heard, held briefly so it can be heard again."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from oida.source_capture import CaptureSource
from oida.window_leases import WindowLeases
from tests.test_research_samples import setup, stream_bytes  # noqa: F401  (the isolated owner)
from tests.test_source_capture import source

BASIS = "Fixture terms, read 2026-11-13: recording and adaptation allowed with attribution."


def leased_source(**kwargs):
    return source(
        **{
            "input": "https://radio.example/stream",
            "sample_rate": 192000,
            "channels": 2,
            "network_policy": "public_radio",
            "retention": "temp_only",
            "lease": {"basis": BASIS, "max_seconds": 600},
            "apparatus": {"status": "partially-known", "station": "Fixture FM"},
            **kwargs,
        }
    )


def capture(client, acquisition_id="w1", **kwargs):
    with patch("oida.public_fetch.PublicFetcher.get", return_value=stream_bytes()):
        return client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id=acquisition_id, seconds=0.2, remember=True, **kwargs),
        )


@pytest.mark.parametrize("identifier", ["../outside", "wl_" + "a" * 24 + "\n", "rs_" + "a" * 24])
def test_lease_identifiers_require_an_exact_match(tmp_path, identifier):
    leases = WindowLeases(tmp_path / "leases", None)
    with pytest.raises(KeyError):
        leases.release(identifier)
    with pytest.raises(KeyError):
        leases.audio(identifier, "0" * 64)
    with pytest.raises(KeyError):
        leases.note_listening(identifier, "listening", None)
    assert not leases.root.exists()


def test_sidecars_cannot_be_read_outside_the_custody_root(tmp_path):
    leases = WindowLeases(tmp_path / "leases", None)
    leases.root.mkdir()
    identifier = "wl_" + "a" * 24
    outside = tmp_path / f"{identifier}.json"
    outside.write_text(json.dumps({"contract": "oida/window-lease/v1", "id": identifier}))
    assert leases._read(outside) is None
    inside = leases.root / outside.name
    inside.symlink_to(outside)
    assert leases._read(inside) is None
    inside.unlink()
    inside.write_bytes(outside.read_bytes())
    assert leases._read(inside)["id"] == identifier


def test_only_guarded_public_radio_may_declare_a_lease_and_never_past_thirty_minutes():
    CaptureSource.model_validate(leased_source())
    with pytest.raises(ValidationError):
        CaptureSource.model_validate(leased_source(lease={"basis": BASIS, "max_seconds": 1801}))
    with pytest.raises(ValidationError, match="window lease is valid only"):
        CaptureSource.model_validate(
            {**leased_source(), "network_policy": None, "retention": None, "input": "http://127.0.0.1:1/x.wav"}
        )


def test_a_leased_window_is_heard_again_byte_for_byte_and_deleted_at_release(setup, tmp_path):  # noqa: F811
    client = setup([leased_source()])
    assert client.get("/sources/capture").json()["sources"][0]["lease"] == {
        "max_seconds": 600,
        "audience": "local",
    }
    first = capture(client, lease_seconds=300)
    assert first.status_code == 200, first.text
    receipt = first.json()["receipt"]
    lease = receipt["window_lease"]
    heard = first.json()["result"]["listening_event"]["segment"]["data_ref"]["sha256"]
    assert receipt["status"] == "complete" and lease["status"] == "leased"
    assert lease["sha256"] == heard and receipt["raw_audio_deleted"] is False

    root = tmp_path / "runtime" / "window-leases"
    (held,) = [p for p in root.iterdir() if p.suffix == ".wav"]
    assert hashlib.sha256(held.read_bytes()).hexdigest() == heard
    assert held.stat().st_mode & 0o077 == 0
    assert not any(p.name.startswith("wl_") for p in (tmp_path / "audio").rglob("*"))

    listing = client.get("/sources/window-leases").json()
    assert listing["exportable"] is False and listing["audience"] == "local"
    (row,) = listing["leases"]
    assert row["id"] == lease["id"] and row["record_id"] == receipt["akousma_id"]
    assert row["basis_of_permission"] == BASIS
    assert "file" not in row and str(root) not in json.dumps(listing)

    # A second and a third listening, with no network: the same file, another configuration.
    with patch("oida.public_fetch.PublicFetcher.get", side_effect=AssertionError("no new capture")):
        again = client.post(
            f"/sources/window-leases/{lease['id']}/listen",
            json=dict(
                acquisition_id="w1-focused",
                sha256=heard,
                remember=True,
                context_refs=[receipt["akousma_id"]],
                focus_question="What repeats in this window?",
            ),
        )
        assert again.status_code == 200, again.text
        second = again.json()
        assert second["receipt"]["status"] == "complete", second["receipt"]
        assert second["result"]["listening_event"]["segment"]["data_ref"]["sha256"] == heard
        assert second["receipt"]["same_window_as"] == receipt["akousma_id"]
        assert second["receipt"]["akousma_id"] != receipt["akousma_id"]
        assert second["receipt"]["source_admission"]["source_time"] == receipt["source_admission"]["source_time"]
        third = client.post(
            f"/sources/window-leases/{lease['id']}/listen",
            json=dict(acquisition_id="w1-corrective", sha256=heard, remember=True),
        )
        assert third.json()["receipt"]["status"] == "complete"
    (row,) = client.get("/sources/window-leases").json()["leases"]
    assert [x["acquisition_id"] for x in row["listenings"]] == ["w1-focused", "w1-corrective"]

    # Nothing about the lease reaches a record, an event or the memory export.
    exported = client.get("/memory/export").text
    assert lease["id"] not in exported and "window-leases" not in exported
    assert lease["id"] not in json.dumps(second["result"])

    released = client.delete(f"/sources/window-leases/{lease['id']}", params={"reason": "the circuit moved on"})
    assert released.status_code == 200
    body = released.json()
    assert body["status"] == "released" and body["audio_deleted"] is True
    assert body["reason"] == "the circuit moved on" and "file" not in body
    assert not list(root.glob("wl_*"))
    assert client.get("/sources/window-leases").json()["leases"] == []
    gone = client.post(
        f"/sources/window-leases/{lease['id']}/listen",
        json=dict(acquisition_id="w1-late", sha256=heard),
    )
    assert gone.status_code == 404
    assert client.delete(f"/sources/window-leases/{lease['id']}").status_code == 404


def test_a_lease_names_its_window_by_digest(setup):  # noqa: F811
    client = setup([leased_source()])
    lease = capture(client, lease_seconds=60).json()["receipt"]["window_lease"]
    other = client.post(
        f"/sources/window-leases/{lease['id']}/listen",
        json=dict(acquisition_id="w-other", sha256="0" * 64),
    )
    assert other.status_code == 409 and "another window" in other.json()["detail"]


def test_a_station_without_the_permission_is_refused_before_capture(setup):  # noqa: F811
    client = setup(
        [source(input="https://radio.example/stream", network_policy="public_radio", retention="temp_only")]
    )
    with patch("oida.public_fetch.PublicFetcher.get", side_effect=AssertionError("captured")):
        refused = client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="n1", seconds=0.2, lease_seconds=60),
        )
    assert refused.status_code == 400 and "do not allow a leased window" in refused.json()["detail"]
    client = setup([leased_source()])
    with patch("oida.public_fetch.PublicFetcher.get", side_effect=AssertionError("captured")):
        too_long = client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="n2", seconds=0.2, lease_seconds=601),
        )
    assert too_long.status_code == 400 and "at most 600 seconds" in too_long.json()["detail"]
    assert client.post(
        "/sources/capture/fixture/listen",
        json=dict(acquisition_id="n3", seconds=0.2, lease_seconds=1801),
    ).status_code == 422


def test_a_capture_without_a_lease_keeps_nothing(setup, tmp_path):  # noqa: F811
    client = setup([leased_source()])
    receipt = capture(client).json()["receipt"]
    assert receipt["status"] == "complete" and receipt["raw_audio_deleted"] is True
    assert "window_lease" not in receipt
    assert not list((tmp_path / "runtime" / "window-leases").glob("wl_*"))


def test_a_lease_ends_at_its_expiry_with_a_receipt(setup, tmp_path):  # noqa: F811
    client = setup([leased_source()])
    lease = capture(client, lease_seconds=60).json()["receipt"]["window_lease"]
    root = tmp_path / "runtime" / "window-leases"
    from oida.owner_journal import OwnerJournal

    journal = OwnerJournal(tmp_path / "runtime" / "owner-journal.sqlite3")
    leases = WindowLeases(root, journal)
    assert leases.sweep() == []
    (expired,) = leases.sweep(datetime.now(timezone.utc) + timedelta(seconds=61))
    assert expired["id"] == lease["id"] and expired["status"] == "expired" and expired["audio_deleted"] is True
    assert not list(root.glob("wl_*"))
    assert client.get("/sources/window-leases").json()["leases"] == []


def test_no_lease_survives_its_owner(setup, tmp_path):  # noqa: F811
    client = setup([leased_source()])
    capture(client, lease_seconds=600)
    root = tmp_path / "runtime" / "window-leases"
    assert list(root.glob("wl_*.wav"))
    again = setup([leased_source()])  # a new owner on the same data directory
    assert not list(root.glob("wl_*"))
    assert again.get("/sources/window-leases").json()["leases"] == []
