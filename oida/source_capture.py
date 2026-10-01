"""Bounded owner-configured acquisition. No caller-supplied URL or FFmpeg arguments."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field, model_validator

from oida.owner_journal import OwnerJournal
from oida.contracts import now_iso

MAX_AUDIO_BYTES = 128 * 1024 * 1024
RESEARCH_TTL_DEFAULT = 30 * 24 * 3600


class ResearchPolicy(BaseModel):
    """The operator's opt-in to keep a station's listened input for local research."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    attestation: str = Field(min_length=1, max_length=1024)
    ttl_seconds: int = Field(
        default=RESEARCH_TTL_DEFAULT, ge=60, le=RESEARCH_TTL_DEFAULT
    )


class CaptureSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    adapter: Literal["radio", "avfoundation", "alsa"]
    input: str = Field(min_length=1, max_length=2048)
    sample_rate: int = Field(ge=8000, le=384000)
    channels: int = Field(ge=1, le=8)
    max_seconds: float = Field(gt=0, le=300)
    producer_id: str = Field(min_length=1, max_length=256)
    consent: Literal["granted", "denied", "unknown"]
    consent_ref: str = Field(min_length=1, max_length=512)
    apparatus: dict = Field(default_factory=lambda: {"status": "unknown"})
    runtime_registered: bool = False
    registration_revision: str | None = None
    rights_ref: str | None = None
    source_ref: str | None = None
    guarded_format: Literal["mp3", "aac", "wav", "flac", "ogg"] | None = None
    network_policy: Literal["public_radio"] | None = None
    retention: Literal["temp_only", "research_sample"] | None = None
    research: ResearchPolicy | None = None

    @model_validator(mode="after")
    def bounded(self):
        if (
            self.max_seconds * self.sample_rate * self.channels * 4
            > MAX_AUDIO_BYTES - 4096
        ):
            raise ValueError("configured capture exceeds 128 MiB PCM budget")
        if self.adapter == "radio":
            u = urlsplit(self.input)
            if (
                u.scheme not in {"http", "https"}
                or not u.hostname
                or u.username
                or u.password
                or u.fragment
            ):
                raise ValueError(
                    "radio requires an owner-selected HTTP(S) URL without credentials"
                )
            if self.network_policy == "public_radio":
                if (
                    self.retention not in {"temp_only", "research_sample"}
                    or self.guarded_format is not None
                ):
                    raise ValueError(
                        "public radio requires temp_only or research_sample retention and owner-derived format"
                    )
            elif self.retention is not None:
                raise ValueError("retention is valid only for guarded public radio")
        elif self.network_policy is not None or self.retention is not None:
            raise ValueError("network policy is valid only for radio")
        if (self.retention == "research_sample") != (self.research is not None):
            raise ValueError(
                "research_sample retention requires, and only it accepts, a research attestation"
            )
        if len(json.dumps(self.apparatus, allow_nan=False).encode()) > 16384:
            raise ValueError("apparatus exceeds 16 KiB")
        return self

    @property
    def source_type(self):
        return "external_stream" if self.adapter == "radio" else "live_input"


def load_capture_sources(path: str | None) -> dict[str, CaptureSource]:
    if not path:
        return {}
    with Path(path).open("rb") as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError("source manifest exceeds 128 KiB")
    value = json.loads(raw)
    if set(value) != {"contract", "sources"} or value["contract"] not in {
        "oida/capture-sources/v1",
        "oida/capture-sources/v2",
    }:
        raise ValueError("invalid capture source manifest")
    if not isinstance(value["sources"], list) or len(value["sources"]) > 64:
        raise ValueError("at most 64 configured sources are supported")
    result = {}
    for item in value["sources"]:
        source = CaptureSource.model_validate(item)
        if source.id in result:
            raise ValueError("duplicate source id")
        result[source.id] = source
    return result


def capture_input_command(source: CaptureSource) -> list[str]:
    """Shared fixed input setup for bounded capture and model-free monitoring."""
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("FFmpeg is unavailable")
    command = [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if source.guarded_format:
        command += ["-protocol_whitelist", "file,pipe", "-f", source.guarded_format]
    elif source.adapter == "radio":
        command += [
            "-protocol_whitelist",
            "http,https,tcp,tls,crypto",
            "-rw_timeout",
            "10000000",
        ]
    elif source.adapter == "alsa":
        command += [
            "-f",
            "alsa",
            "-sample_rate",
            str(source.sample_rate),
            "-channels",
            str(source.channels),
        ]
    else:
        command += ["-f", "avfoundation"]
    return command + ["-i", source.input]


def capture_command(source: CaptureSource, seconds: float, path: Path) -> list[str]:
    # No output -ar/-ac: never resample a device to pretend it captured a rate.
    return capture_input_command(source) + [
        "-map",
        "0:a:0",
        "-vn",
        "-t",
        str(seconds),
        "-c:a",
        "pcm_f32le",
        "-fs",
        str(MAX_AUDIO_BYTES),
        str(path),
    ]


class CaptureInterrupted(RuntimeError):
    pass


def capture_audio(
    source: CaptureSource, seconds: float, path: Path, cancelled: threading.Event
) -> None:
    if source.consent != "granted" or not 0 < seconds <= source.max_seconds:
        raise ValueError("Capture consent or duration refused")
    if cancelled.is_set():
        raise CaptureInterrupted("Capture cancelled")
    if source.runtime_registered or source.network_policy == "public_radio":
        from tempfile import TemporaryDirectory
        from oida.public_fetch import PublicFetcher

        result = PublicFetcher().get(
            source.input,
            limit=32 * 1024**2,
            cancel=cancelled,
            stream_seconds=min(seconds + 2, 40),
        )
        data = result.data
        formats = {
            "audio/mpeg": "mp3",
            "audio/aac": "aac",
            "audio/aacp": "aac",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/flac": "flac",
            "audio/ogg": "ogg",
            "application/ogg": "ogg",
        }
        format_name = formats.get(result.content_type.lower())
        if not format_name or data.lstrip().startswith(
            (b"#EXTM3U", b"[playlist]", b"<")
        ):
            raise ValueError(
                "Only guarded direct audio streams are available; playlists/HLS refused"
            )
        with TemporaryDirectory(prefix="oida-radio-", dir=path.parent) as temp:
            local = Path(temp) / "input.audio"
            local.write_bytes(data)
            return capture_audio(
                source.model_copy(
                    update={
                        "input": str(local),
                        "runtime_registered": False,
                        "network_policy": None,
                        "retention": None,
                        "research": None,
                        "guarded_format": format_name,
                    }
                ),
                seconds,
                path,
                cancelled,
            )
    if source.consent != "granted" or not 0 < seconds <= source.max_seconds:
        raise ValueError("capture consent or duration refused")
    if cancelled.is_set():
        raise CaptureInterrupted("capture cancelled")
    # Do not retain URL-bearing FFmpeg diagnostics in receipts or responses.
    command = capture_command(source, seconds, path)
    process = subprocess.Popen(
        [sys.executable, "-m", "oida.capture_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + seconds + 15
    try:
        assert process.stdin is not None
        process.stdin.write((json.dumps(command) + "\n").encode())
        process.stdin.flush()
        while process.poll() is None:
            if cancelled.wait(0.05):
                raise CaptureInterrupted("capture cancelled")
            if time.monotonic() > deadline:
                raise TimeoutError("capture deadline exceeded")
            if path.exists() and path.stat().st_size >= MAX_AUDIO_BYTES:
                raise ValueError("capture byte budget exceeded")
        if cancelled.is_set():
            raise CaptureInterrupted("capture cancelled")
        if process.returncode:
            raise RuntimeError(
                "capture backend failed; check device availability or stream configuration"
            )
        info = sf.info(path)
        if source.guarded_format:
            if (
                not 8000 <= info.samplerate <= source.sample_rate
                or not 1 <= info.channels <= source.channels
            ):
                raise ValueError("Direct stream exceeds admitted native format limits")
        elif info.samplerate != source.sample_rate or info.channels != source.channels:
            raise ValueError(
                "native input rate/channels differ from configured expectation; no resampling applied"
            )
        if (
            info.frames <= 0
            or info.duration > seconds + 1 / info.samplerate
            or path.stat().st_size >= MAX_AUDIO_BYTES
        ):
            raise ValueError("capture output violates declared bounds")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdin is not None:
            process.stdin.close()


class AcquisitionReceipts:
    """Durable receipts shared by direct acquisition and the bounded scheduler."""

    def __init__(self, root: Path, journal=None):
        self.root = root
        self.journal = journal or OwnerJournal(root.parent / "owner-journal.sqlite3")
        self.lock = threading.Lock()
        self.active: dict[str, threading.Event] = {}
        root.mkdir(parents=True, exist_ok=True)
        for path in root.glob("*.json"):
            legacy = json.loads(path.read_text())
            if self.journal.get("acquisition", legacy["id"]) is None:
                self.journal.save("acquisition", legacy["id"], legacy)
        # Recover authoritative snapshots, including transitions whose JSON mirror failed.
        cursor = 0
        boundary = None
        items = []
        while True:
            page = self.journal.snapshots(
                after=cursor, producer_id=self.journal.producer_id, at=boundary
            )
            boundary = page["high_water_sequence"]
            items.extend(
                row["payload"]
                for row in page["snapshots"]
                if row["kind"] == "acquisition"
            )
            if not page["has_more"]:
                break
            cursor = page["next_sequence"]
        for item in items:
            if item.get("status") in {"queued", "acquiring", "listening", "committing"}:
                expired = (
                    item.get("status") == "queued"
                    and item.get("expires_at")
                    and datetime.fromisoformat(item["expires_at"])
                    <= datetime.now(timezone.utc)
                )
                self.save(
                    {
                        **item,
                        "status": "expired" if expired else "interrupted",
                        "finished_at": now_iso(),
                        "reason": "queued deadline elapsed before restart"
                        if expired
                        else "owner restarted before a terminal acquisition receipt",
                    }
                )

    def get(self, identifier):
        return self.journal.get("acquisition", identifier)

    def save(self, receipt: dict):
        identifier = receipt["id"]
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,80}", identifier
        ):
            raise ValueError("Invalid acquisition identifier")
        self.journal.save("acquisition", receipt["id"], receipt)
        # Disposable legacy mirror; never overrides committed journal state on restart.
        target = self.root / (identifier + ".json")
        temp = target.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(receipt, allow_nan=False) + "\n")
            os.replace(temp, target)
        except OSError:
            logging.getLogger(__name__).warning(
                "Acquisition JSON mirror unavailable; journal commit retained"
            )

    def begin(
        self, identifier: str, source_id: str, *, scheduled: bool = False
    ) -> tuple[dict, threading.Event]:
        with self.lock:
            if self.active:
                raise ValueError(
                    "an acquisition is already active; no queue is configured"
                )
            previous = {}
            existing = self.get(identifier)
            if existing is not None:
                previous = existing
                if (
                    not scheduled
                    or previous.get("status") != "queued"
                    or previous.get("source_id") != source_id
                ):
                    raise ValueError(
                        "acquisition id already exists; inspect its receipt or retry with a fresh id"
                    )
            elif scheduled:
                raise ValueError("scheduled acquisition receipt is missing")
            item = dict(
                contract="oida/acquisition-receipt/v1",
                id=identifier,
                source_id=source_id,
                status="acquiring",
                started_at=now_iso(),
            )
            item = {**previous, **item}
            cancel = threading.Event()
            self.save(item)
            self.active[identifier] = cancel
            return item, cancel

    def finish(self, item: dict):
        with self.lock:
            event = self.active.get(item["id"])
            if event is not None and event.is_set():
                if item.get("status") != "expired":
                    item.update(
                        status="cancelled",
                        reason="owner cancellation accepted; late output discarded",
                    )
                for key in ("event_id", "akousma_id", "source_admission"):
                    item.pop(key, None)
            self.save(item)
            self.active.pop(item["id"], None)

    def cancel(self, identifier: str) -> bool:
        with self.lock:
            event = self.active.get(identifier)
            if event is None:
                return (self.get(identifier) or {}).get("status") == "cancelled"
            item = self.get(identifier)
            if item["status"] not in {"acquiring", "listening", "cancelled"}:
                return False
            event.set()
            item.update(
                status="cancelled",
                reason="owner cancellation accepted; late output will be discarded",
                finished_at=now_iso(),
            )
            self.save(item)
            return True
