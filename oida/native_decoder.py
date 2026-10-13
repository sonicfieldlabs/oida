"""Isolated native decoder preflight: generated audio, no weights or network."""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

_CACHE = {}
_LOCK = threading.Lock()


def worker():
    libraries = []
    try:
        from torchcodec.decoders import AudioDecoder

        with tempfile.TemporaryDirectory(prefix="oida-decoder-") as directory:
            path = Path(directory) / "generated.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\x00\x00" * 1600)
            decoded = AudioDecoder(str(path)).get_all_samples()
            if decoded.sample_rate != 16000 or tuple(decoded.data.shape) != (1, 1600):
                raise ValueError("Native decoder changed generated input geometry")
        if sys.platform == "darwin":
            import ctypes

            loader = ctypes.CDLL(None)
            loader._dyld_get_image_name.restype = ctypes.c_char_p
            paths = [
                loader._dyld_get_image_name(i).decode()
                for i in range(min(loader._dyld_image_count(), 4096))
            ]
        else:
            maps = Path("/proc/self/maps")
            paths = (
                [line.split()[-1] for line in maps.read_text().splitlines()]
                if maps.exists()
                else []
            )
        for name in sorted(set(paths)):
            path = Path(name)
            if not any(
                word in path.name
                for word in (
                    "libavcodec",
                    "libavformat",
                    "libavutil",
                    "libswresample",
                    "libtorchcodec",
                )
            ):
                continue
            if not path.is_file() or len(libraries) >= 32:
                continue
            if path.stat().st_size > 128 * 1024**2:
                raise ValueError("Native library exceeds preflight hash budget")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            libraries.append(
                {
                    "path": str(path.resolve()),
                    "bytes": path.stat().st_size,
                    "sha256": digest,
                }
            )
        result = dict(
            status="supported",
            torchcodec_version=metadata.version("torchcodec"),
            sample_rate=decoded.sample_rate,
            channels=1,
            frames=1600,
            native_libraries=libraries,
            scope="Generated WAV native decode only; no weights, inference, capture or model readiness",
        )
    except Exception as exc:
        result = dict(
            status="unavailable",
            error_type=type(exc).__name__,
            detail=str(exc)[-1800:],
            native_libraries=libraries,
            action="Bind a supported FFmpeg library directory before launching the owner and rerun the decoder doctor; no model was loaded",
        )
    print(json.dumps(result, allow_nan=False))


def probe(*, timeout=30):
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "TMPDIR",
            "DYLD_LIBRARY_PATH",
            "DYLD_FALLBACK_LIBRARY_PATH",
            "LD_LIBRARY_PATH",
            "SYSTEMROOT",
        }
    }
    env.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", OIDA_ALLOW_HF_HUB="0")
    declared = os.getenv("OIDA_FFMPEG_LIB_DIR")
    key = (sys.executable, tuple(sorted(env.items())), declared)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and all(
            Path(row["path"]).is_file()
            and list(
                (Path(row["path"]).stat().st_size, Path(row["path"]).stat().st_mtime_ns)
            )
            == row["stat"]
            for row in cached.get("library_stats", [])
        ):
            return dict(cached)
        if declared:
            path = Path(declared).expanduser().resolve()
            loader_keys = (
                ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")
                if sys.platform == "darwin"
                else ("LD_LIBRARY_PATH",)
            )
            bound = [
                Path(item).resolve()
                for loader_key in loader_keys
                for item in env.get(loader_key, "").split(os.pathsep)
                if item
            ]
            if not path.is_dir() or path not in bound:
                return dict(
                    contract="oida/native-decoder/v1",
                    status="unavailable",
                    detail="Declared FFmpeg library directory is missing or not bound in this owner process",
                    action="Set the native loader path before owner startup",
                )
        started = time.monotonic()
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        chunks, size = [], 0
        try:
            while True:
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Native decoder doctor exceeded its deadline")
                readable, _, _ = select.select([process.stdout], [], [], 0.1)
                if not readable:
                    continue
                chunk = os.read(process.stdout.fileno(), 16384)
                if not chunk:
                    break
                size += len(chunk)
                if size > 64 * 1024:
                    raise ValueError("Native doctor output exceeds bounds")
                chunks.append(chunk)
            process.wait(timeout=2)
            if process.returncode:
                raise ValueError("Native decoder doctor process failed")
            lines = b"".join(chunks).decode(errors="replace").splitlines()
            result = json.loads(lines[-1])
        except (
            OSError,
            ValueError,
            TimeoutError,
            subprocess.TimeoutExpired,
            IndexError,
        ) as exc:
            result = dict(
                status="unavailable",
                error_type=type(exc).__name__,
                detail=str(exc)[:1000],
                action="Inspect the isolated native decoder environment; no weights were loaded",
            )
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            process.stdout.close()
        if result.get("status") == "supported":
            native = result.get("native_libraries", [])
            ffmpeg = [
                row
                for row in native
                if Path(row["path"]).name.startswith("libavformat")
            ]
            if not ffmpeg or (
                declared
                and not all(
                    Path(row["path"]).is_relative_to(Path(declared).resolve())
                    for row in ffmpeg
                )
            ):
                result = dict(
                    status="unavailable",
                    detail="Decoded audio but could not establish the declared FFmpeg library binding",
                    native_libraries=native,
                )
        result.update(
            contract="oida/native-decoder/v1",
            elapsed_ms=round((time.monotonic() - started) * 1000),
            declared_library_dir=declared,
            library_stats=[
                dict(
                    path=row["path"],
                    stat=[
                        Path(row["path"]).stat().st_size,
                        Path(row["path"]).stat().st_mtime_ns,
                    ],
                )
                for row in result.get("native_libraries", [])
            ],
        )
        if result["status"] == "supported":
            _CACHE.clear()
            _CACHE[key] = result
        return dict(result)


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        worker()
    else:
        print(json.dumps(probe(), indent=2))
