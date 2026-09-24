"""Phase R: research samples keep exactly the input a radio listening heard, locally, for a while."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

from oida.owner_journal import OwnerJournal
from oida.research_samples import ResearchSampleRefused, ResearchSamples
from fastapi.testclient import TestClient

from oida.server import create_app
from oida.source_capture import CaptureSource
from tests.test_source_capture import source


ATTESTATION = "Operator attestation, fixture: local research sample of a public stream."


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """The same isolated owner as tests/test_source_capture.py."""
    import os

    for key in list(os.environ):
        if (
            key.startswith(("OIDA_", "HMM_", "AEAR_"))
            and key != "OIDA_TEST_MASA_VALIDATOR_MODULE"
        ):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("OIDA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")

    def app(sources=()):
        manifest = tmp_path / "sources.json"
        manifest.write_text(
            json.dumps(dict(contract="oida/capture-sources/v1", sources=list(sources)))
        )
        monkeypatch.setenv("OIDA_CAPTURE_SOURCES", str(manifest))
        return TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")

    return app


def research_source(**kwargs):
    return source(
        **{
            "input": "https://radio.example/stream",
            "sample_rate": 192000,
            "channels": 2,
            "network_policy": "public_radio",
            "retention": "research_sample",
            "research": {"attestation": ATTESTATION, "ttl_seconds": 3600},
            "apparatus": {"status": "partially-known", "station": "Fixture FM"},
            **kwargs,
        }
    )


def stream_bytes(seconds=0.25, rate=44100):
    payload = io.BytesIO()
    tone = 0.1 * np.sin(np.linspace(0, 2 * np.pi * 440 * seconds, int(rate * seconds)))
    sf.write(payload, tone.astype(np.float32), rate, format="WAV")
    return SimpleNamespace(content_type="audio/wav", data=payload.getvalue())


def test_the_opt_in_needs_an_attestation_and_only_public_radio_has_it():
    CaptureSource.model_validate(research_source())
    with pytest.raises(ValidationError, match="research attestation"):
        CaptureSource.model_validate({**research_source(), "research": None})
    with pytest.raises(ValidationError, match="research attestation"):
        CaptureSource.model_validate({**research_source(), "retention": "temp_only"})
    with pytest.raises(ValidationError):
        CaptureSource.model_validate(
            {
                **research_source(),
                "research": {"attestation": ATTESTATION, "ttl_seconds": 31 * 86400},
            }
        )
    with pytest.raises(ValidationError, match="retention is valid only"):
        CaptureSource.model_validate(
            {
                **research_source(),
                "network_policy": None,
                "input": "http://127.0.0.1:1/x.wav",
            }
        )


def test_an_opted_in_listening_keeps_exactly_its_input_and_says_so(setup, tmp_path):
    client = setup([research_source()])
    listed = client.get("/sources/capture").json()["sources"][0]
    assert listed["raw_audio_policy"] == "research_sample"
    assert listed["research"] == {"ttl_seconds": 3600, "audience": "local"}
    with patch("oida.public_fetch.PublicFetcher.get", return_value=stream_bytes()):
        response = client.post(
            "/sources/capture/fixture/listen",
            json=dict(
                acquisition_id="r1",
                seconds=0.2,
                remember=True,
                retain_research_sample=True,
            ),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    receipt = body["receipt"]
    assert receipt["status"] == "complete", receipt
    sample = receipt["research_sample"]
    assert sample["status"] == "retained"
    assert receipt["raw_audio_deleted"] is False
    heard = body["result"]["listening_event"]["segment"]["data_ref"]["sha256"]
    assert sample["sha256"] == heard

    root = tmp_path / "runtime" / "research-samples"
    files = [p for p in root.iterdir() if p.suffix == ".wav"]
    assert len(files) == 1
    assert hashlib.sha256(files[0].read_bytes()).hexdigest() == heard
    assert files[0].stat().st_mode & 0o077 == 0
    # Outside the audio folder the library scans: never a library sound.
    audio_dir = tmp_path / "audio"
    assert not any(p.name.startswith("rs_") for p in audio_dir.rglob("*"))

    rows = client.get("/sources/research-samples").json()
    assert rows["exportable"] is False and rows["audience"] == "local"
    (row,) = rows["samples"]
    assert row["id"] == sample["id"] and row["record_id"] == receipt["akousma_id"]
    assert row["attestation"] == ATTESTATION and row["exportable"] is False
    assert "file" not in row and str(root) not in json.dumps(rows)

    location = client.get(f"/sources/research-samples/{sample['id']}/resolve")
    assert location.status_code == 200
    assert location.json()["contract"] == "oida/research-sample-location/v1"
    assert location.json()["sample"] == row
    assert Path(location.json()["path"]).is_file()

    audio = client.get(f"/sources/research-samples/{sample['id']}/audio")
    assert audio.status_code == 200
    assert hashlib.sha256(audio.content).hexdigest() == heard
    assert audio.headers["cache-control"] == "private, no-store"

    # Nothing about the sample reaches the record, the event or the memory export.
    exported = client.get("/memory/export").text
    assert sample["id"] not in exported and "research-samples" not in exported
    assert sample["id"] not in json.dumps(body["result"])


def test_a_station_that_is_not_opted_in_is_refused_before_capture(setup):
    client = setup(
        [
            source(
                input="https://radio.example/stream",
                network_policy="public_radio",
                retention="temp_only",
            )
        ]
    )
    with patch("oida.public_fetch.PublicFetcher.get") as fetch:
        response = client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="r2", seconds=0.2, retain_research_sample=True),
        )
    assert response.status_code == 400
    assert "not opted in" in response.json()["detail"]
    fetch.assert_not_called()


def test_a_covenant_forbidding_raw_audio_refuses_the_sample(setup):
    client = setup([research_source()])
    forbidding = SimpleNamespace(
        forbids_retention=lambda target: "raw-audio" if target == "raw-audio" else None
    )
    with (
        patch("oida.covenant.CovenantStore.engine", return_value=forbidding),
        patch("oida.public_fetch.PublicFetcher.get") as fetch,
    ):
        response = client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="r3", seconds=0.2, retain_research_sample=True),
        )
    assert response.status_code == 400 and "covenant" in response.json()["detail"]
    fetch.assert_not_called()


def test_without_the_request_a_research_station_still_deletes_its_audio(
    setup, tmp_path
):
    client = setup([research_source()])
    with patch("oida.public_fetch.PublicFetcher.get", return_value=stream_bytes()):
        receipt = client.post(
            "/sources/capture/fixture/listen",
            json=dict(acquisition_id="r4", seconds=0.2),
        ).json()["receipt"]
    assert receipt["raw_audio_deleted"] is True and "research_sample" not in receipt
    assert not (tmp_path / "runtime" / "research-samples").exists() or not any(
        (tmp_path / "runtime" / "research-samples").iterdir()
    )


@pytest.fixture
def store(tmp_path):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    return ResearchSamples(tmp_path / "research-samples", journal), journal


def keep(store, tmp_path, *, digest=None, ttl=3600):
    samples, _ = store
    audio = tmp_path / "capture.wav"
    audio.write_bytes(stream_bytes().data)
    configured = CaptureSource.model_validate(
        research_source(research={"attestation": ATTESTATION, "ttl_seconds": ttl})
    )
    return samples.retain(
        audio,
        expected_sha256=digest or hashlib.sha256(audio.read_bytes()).hexdigest(),
        source=configured,
        acquisition_id="a",
        event_id="evt",
        record_id="akm_x",
        captured_at=None,
        sample_rate=44100,
        channels=1,
    )


def test_a_sample_expires_on_schedule_with_a_deletion_receipt(store, tmp_path):
    samples, journal = store
    kept = keep(store, tmp_path, ttl=60)
    assert samples.sweep(datetime.now(timezone.utc) + timedelta(seconds=30)) == []
    (receipt,) = samples.sweep(datetime.now(timezone.utc) + timedelta(seconds=61))
    assert receipt["status"] == "expired" and receipt["audio_deleted"] is True
    assert receipt["sha256"] == kept["sha256"] and receipt["deleted_at"]
    assert journal.get("research_sample", kept["id"])["status"] == "expired"
    assert not any(samples.root.iterdir())
    with pytest.raises(KeyError):
        samples.audio(kept["id"])


def test_a_file_that_is_not_the_heard_input_is_never_kept(store, tmp_path):
    samples, journal = store
    with pytest.raises(ResearchSampleRefused, match="not the input"):
        keep(store, tmp_path, digest="0" * 64)
    assert not any(p for p in samples.root.iterdir())


def test_a_changed_sample_is_refused_at_read_time(store, tmp_path):
    samples, _ = store
    kept = keep(store, tmp_path)
    (wav,) = samples.root.glob("rs_*.wav")
    data = bytearray(wav.read_bytes())
    data[-1] ^= 1
    wav.write_bytes(bytes(data))
    with pytest.raises(ResearchSampleRefused, match="no longer matches"):
        samples.audio(kept["id"])


def test_a_sample_folder_inside_a_scanned_audio_folder_keeps_nothing(tmp_path):
    journal = OwnerJournal(tmp_path / "journal.sqlite3")
    samples = ResearchSamples(
        tmp_path / "audio" / "research-samples",
        journal,
        scanned_roots=(tmp_path / "audio",),
    )
    with pytest.raises(ResearchSampleRefused, match="scanned audio folder"):
        keep((samples, journal), tmp_path)
    assert not Path(tmp_path / "audio" / "research-samples").exists()


def test_expired_samples_are_swept_on_a_timer_without_any_reader(setup, tmp_path):
    import time

    with patch("oida.source_api.RESEARCH_SWEEP_SECONDS", 0.05):
        client = setup(
            [research_source(research={"attestation": ATTESTATION, "ttl_seconds": 60})]
        )
        with patch("oida.public_fetch.PublicFetcher.get", return_value=stream_bytes()):
            receipt = client.post(
                "/sources/capture/fixture/listen",
                json=dict(
                    acquisition_id="r5", seconds=0.2, retain_research_sample=True
                ),
            ).json()["receipt"]
        root = tmp_path / "runtime" / "research-samples"
        assert any(root.glob("rs_*.wav"))
        later = datetime.now(timezone.utc) + timedelta(seconds=120)
        with patch("oida.research_samples.datetime") as clock:
            clock.now.return_value = later
            clock.fromisoformat = datetime.fromisoformat
            deadline = time.monotonic() + 5
            while any(root.glob("rs_*")) and time.monotonic() < deadline:
                time.sleep(0.05)
        assert not any(root.glob("rs_*"))
        journal = OwnerJournal(tmp_path / "runtime" / "owner-journal.sqlite3")
        assert (
            journal.get("research_sample", receipt["research_sample"]["id"])["status"]
            == "expired"
        )


def test_the_operator_can_delete_a_sample_early_with_a_receipt(store, tmp_path):
    samples, journal = store
    kept = keep(store, tmp_path)
    receipt = samples.delete(kept["id"])
    assert receipt["status"] == "deleted" and receipt["audio_deleted"] is True
    assert journal.get("research_sample", kept["id"])["reason"] == "operator request"
    assert not any(samples.root.iterdir())
    with pytest.raises(KeyError):
        samples.delete(kept["id"])


def test_research_sample_reads_reject_symlinks(store, tmp_path):
    samples, _ = store
    kept = keep(store, tmp_path)
    path, _ = samples.audio(kept["id"])
    path.unlink()
    path.symlink_to(tmp_path / "capture.wav")
    with pytest.raises(KeyError):
        samples.audio(kept["id"])


def test_research_sample_digest_enforces_byte_limit(tmp_path, monkeypatch):
    import oida.research_samples as module

    path = tmp_path / "oversized.wav"
    path.write_bytes(b"12345")
    monkeypatch.setattr(module, "MAX_BYTES", 4)
    with pytest.raises(ResearchSampleRefused, match="byte limit"):
        module._sha256(path)
