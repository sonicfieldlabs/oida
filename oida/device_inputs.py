"""Mac input acquisition and buffered monitoring, with no background/model calls."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import soundfile as sf
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .source_capture import CaptureSource, capture_input_command

LEASE_SECONDS = 45


def parse_devices(text: str) -> list[dict]:
    audio = False
    devices = []
    for line in text.splitlines():
        if "AVFoundation audio devices:" in line:
            audio = True
            continue
        if audio:
            match = re.search(r"\] \[(\d+)\] (.+)$", line)
            if match:
                devices.append({"id": match[1], "label": match[2].strip()})
    return devices[:64]


def audio_devices() -> list[dict]:
    if platform.system() != "Darwin":
        raise RuntimeError("Mac input capture requires macOS")
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("FFmpeg is unavailable on the Mac")
    result = subprocess.run(
        [exe, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True,
        timeout=10,
        text=True,
    )
    return parse_devices(result.stderr)


class MacInputs:
    """One hardware producer, a 20 s ring, and an expiring browser lease.

    The existing capture worker terminates FFmpeg if this owner crashes. Native
    WAV segments feed the existing LiveManager; only completed segments are read.
    No engine, scheduler, background listener, or memory operation is invoked.
    """

    def __init__(self, live):
        self.live = live
        self.lock = threading.RLock()
        self.current = None

    def start(self, device_id: str, device_label: str, source_type='live_input'):
        if source_type == 'system_output':
            configured = os.environ.get('OIDA_SYSTEM_OUTPUT_DEVICE')
            if configured != device_id or not is_system_device(device_label):
                raise ValueError('System output requires an explicitly configured loopback device')
        with self.lock:
            if self.current is not None:
                raise ValueError(
                    "A Mac input is already open; close it before opening another"
                )
            device = next(
                (
                    d
                    for d in audio_devices()
                    if d["id"] == device_id and d["label"] == device_label
                ),
                None,
            )
            if device is None:
                raise ValueError(
                    "Input device changed or is unavailable; refresh the device list"
                )
            source = CaptureSource(
                id="mac-input",
                adapter="avfoundation",
                input=":" + device_id,
                sample_rate=48000,
                channels=2,
                max_seconds=30.0,
                producer_id="oida/mac-input",
                consent="granted",
                consent_ref="explicit-open-input",
            )
            folder = tempfile.TemporaryDirectory(prefix="oida-mac-input-")
            # Rate/channels above are not capture claims. Actual WAV metadata is
            # returned below; this command does not request any resampling.
            command = capture_input_command(source) + [
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "pcm_f32le",
                "-f",
                "segment",
                "-segment_time",
                "2",
                "-reset_timestamps",
                "1",
                str(Path(folder.name) / "%08d.wav"),
            ]
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "oida.capture_worker"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                process.stdin.write((json.dumps(command) + "\n").encode())
                process.stdin.flush()
                session = self.live.start(
                    ring_seconds=20,
                    source_type=source_type,
                    source_label=device["label"],
                    device_id=device_id,
                )
            except Exception:
                folder.cleanup()
                if "process" in locals():
                    process.terminate()
                    process.wait(timeout=3)
                raise
            state = dict(
                id=uuid4().hex,
                session_id=session["session_id"],
                source_type=source_type,
                device=device,
                folder=folder,
                process=process,
                expires=time.monotonic() + LEASE_SECONDS,
                started=time.monotonic(),
                chunks=[],
                error=None,
                sequence=-1,
            )
            self.current = state
            threading.Thread(target=self._watch, args=(state,), daemon=True).start()
            return self.status(state["id"])

    def _watch(self, state):
        try:
            while True:
                time.sleep(0.2)
                with self.lock:
                    if self.current is not state:
                        return
                    if time.monotonic() > state["expires"]:
                        self._close(state)
                        return
                    if state["process"].poll() is not None:
                        state["error"] = (
                            "Mac capture stopped. Check the device and macOS microphone permission for the stack process."
                        )
                        return
                    files = sorted(Path(state["folder"].name).glob("*.wav"))
                    if sum(p.stat().st_size for p in files) > 64 * 1024 * 1024:
                        state["error"] = "Input exceeded the 64 MiB monitoring buffer."
                        return
                    for path in files[:-1]:
                        sequence = int(path.stem)
                        if sequence <= state["sequence"]:
                            continue
                        info = sf.info(path)
                        if not info.frames:
                            continue
                        status = self.live.ingest_saved_upload(
                            state["session_id"], {"path": str(path)}
                        )
                        state["chunks"].append(
                            dict(
                                sequence=sequence,
                                duration=info.duration,
                                sample_rate=info.samplerate,
                                channels=info.channels,
                                rms_dbfs=status["latest_chunk"]["rms_dbfs"],
                            )
                        )
                        state["sequence"] = sequence
                        while len(state["chunks"]) > 10:
                            old = state["chunks"].pop(0)
                            (path.parent / f"{old['sequence']:08d}.wav").unlink(
                                missing_ok=True
                            )
                    if not state["chunks"] and time.monotonic() - state["started"] > 15:
                        state["error"] = (
                            "No audio arrived from this Mac input. Check the device and macOS microphone permission."
                        )
                        return
        except Exception:
            with self.lock:
                state["error"] = (
                    "Mac input could not be read; refresh devices and open it again."
                )
        finally:
            # Failed inputs release hardware immediately; status retains the error
            # until the owner stops it or its lease expires.
            if state["error"]:
                self._terminate(state)
                threading.Timer(
                    LEASE_SECONDS, self._expire_failed, args=(state,)
                ).start()

    def _expire_failed(self, state):
        with self.lock:
            if self.current is state:
                self._close(state)

    def _state(self, identity):
        if not self.current or self.current["id"] != identity:
            raise ValueError("Input session has closed or expired")
        return self.current

    def status(self, identity):
        with self.lock:
            state = self._state(identity)
            state["expires"] = time.monotonic() + LEASE_SECONDS
            ring = self.live.status(state["session_id"])
            return {
                k: state[k] for k in ("id", "session_id", "device", "chunks", "error", "source_type")
            } | {
                "cursor_seconds": ring["cursor_seconds"],
                "available_start_seconds": ring["available_start_seconds"],
                "ring_seconds": ring["ring_seconds"],
                "active": not bool(state["error"]),
                "lease_seconds": LEASE_SECONDS,
                "state": "error"
                if state["error"]
                else ("receiving" if state["chunks"] else "opening"),
            }

    def chunk(self, identity, sequence):
        with self.lock:
            state = self._state(identity)
            if state['source_type'] == 'system_output':
                raise ValueError('System-output monitoring is disabled to prevent feedback')
            if not any(c["sequence"] == sequence for c in state["chunks"]):
                raise ValueError("Audio chunk is no longer in the input buffer")
            return (Path(state["folder"].name) / f"{sequence:08d}.wav").read_bytes()

    def _terminate(self, state):
        process = state["process"]
        if process.stdin:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _close(self, state):
        self._terminate(state)
        try:
            self.live.stop(state["session_id"])
        finally:
            state["folder"].cleanup()
            self.current = None

    def stop(self, identity):
        with self.lock:
            self._close(self._state(identity))
        return {"stopped": True}

    def close(self):
        with self.lock:
            if self.current:
                self._close(self.current)


class OpenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(pattern=r"^\d{1,3}$")
    device_label: str = Field(min_length=1, max_length=256)


def is_system_device(label):
    from oida.system_audio import is_loopback_device_label
    return is_loopback_device_label(label)


def system_output_backend():
    configured = os.environ.get('OIDA_SYSTEM_OUTPUT_DEVICE')
    if not configured:
        return {'status':'unavailable', 'reason':'Configure OIDA_SYSTEM_OUTPUT_DEVICE to an installed loopback audio device; no driver is installed automatically.'}
    try:
        device = next((d for d in audio_devices() if d['id'] == configured and is_system_device(d['label'])), None)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        device = None
    return {'status':'available', 'device':device, 'backend':'avfoundation', 'raw_audio_policy':'temp',
            'memory':'record', 'monitor_playback':False} if device else {
                'status':'unavailable', 'reason':'Configured loopback device was not found in the actual backend inventory'}


def input_router(live, shutdown_callbacks):
    manager = MacInputs(live)
    shutdown_callbacks.append(manager.close)
    router = APIRouter(prefix="/inputs")

    def call(function, *args):
        try:
            return function(*args)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise HTTPException(
                503, "Mac input unavailable; check FFmpeg and connected audio devices"
            ) from exc

    @router.get("/devices")
    def devices():
        return {"host": "stack-mac", "devices": call(audio_devices)}

    @router.get('/system-output')
    def system_output():
        return system_output_backend()

    @router.post('/system-output/start')
    def start_system_output():
        status = system_output_backend()
        if status['status'] != 'available':
            raise HTTPException(503, status['reason'])
        device = status['device']
        return call(manager.start, device['id'], device['label'], 'system_output')

    @router.post("/start")
    def start(body: OpenInput):
        return call(manager.start, body.device_id, body.device_label)

    @router.post("/{identity}/status")
    def status(identity: str):
        return call(manager.status, identity)

    @router.post("/{identity}/stop")
    def stop(identity: str):
        return call(manager.stop, identity)

    @router.get("/{identity}/chunks/{sequence}")
    def chunk(identity: str, sequence: int):
        return Response(
            call(manager.chunk, identity, sequence),
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    return router
