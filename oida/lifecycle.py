"""Singleton lifecycle for the local Oída gateway."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from harness.http_client import get_json
from oida.config import data_dir, load_config
from oida.contracts import now_iso
from oida.storage import write_json_atomic

DEFAULT_SERVER_URL = "http://127.0.0.1:8765"


def server_url() -> str:
    explicit = os.getenv("OIDA_SERVER_URL") or os.getenv("HMM_SERVER_URL") or os.getenv("AEAR_SERVER_URL")
    if explicit:
        return explicit.rstrip("/")
    host = os.getenv("OIDA_HOST") or os.getenv("HMM_HOST") or os.getenv("AEAR_HOST") or "127.0.0.1"
    port = os.getenv("OIDA_PORT") or os.getenv("HMM_PORT") or os.getenv("AEAR_PORT") or "8765"
    return f"http://{host}:{port}"


def state_path() -> Path:
    return data_dir() / "gateway-state.json"


def log_path() -> Path:
    return data_dir() / "logs" / "gateway.log"


def gateway_status(url: str | None = None) -> dict[str, Any]:
    url = (url or server_url()).rstrip("/")
    state = _read_state()
    try:
        health = get_json(url, "/health", timeout=2)
    except Exception as exc:  # transport exceptions differ across Python builds
        return {
            "running": False,
            "url": url,
            "managed": bool(state and state.get("pid")),
            "state": state,
            "detail": str(exc),
        }
    return {
        "running": True,
        "url": url,
        "managed": bool(state and state.get("pid") and int(state["pid"]) == _integer(health.get("pid"))),
        "state": state,
        "health": health,
    }


def ensure_gateway(
    *,
    profile: str | None = None,
    url: str | None = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Reuse a running gateway or start one managed background process."""
    url = (url or server_url()).rstrip("/")
    status = gateway_status(url)
    if status["running"]:
        _require_requested_profile(status, profile)
        return status
    if not _is_local_url(url):
        raise RuntimeError(f"configured Oída server is not reachable and is not local: {url}")

    with _lifecycle_lock():
        status = gateway_status(url)
        if status["running"]:
            _require_requested_profile(status, profile)
            return status
        _remove_stale_state()
        config = load_config(profile=profile)
        logs = log_path()
        logs.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "oida.server", "--profile", config.profile, "--host", config.host, "--port", str(config.port)]
        environment = _gateway_environment(config.profile)
        with logs.open("ab", buffering=0) as output:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
                close_fds=True,
                env=environment,
            )
        state = {
            "pid": process.pid,
            "url": url,
            "profile": config.profile,
            "started_at": now_iso(),
            "managed_by": "oida.lifecycle",
            "command": command,
            "log": str(logs),
        }
        state_path().parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(state_path(), state)

        deadline = time.monotonic() + max(1.0, timeout)
        last_detail = "gateway has not answered yet"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _remove_stale_state()
                raise RuntimeError(
                    f"Oída gateway exited with status {process.returncode}; inspect {logs}"
                )
            status = gateway_status(url)
            if status["running"]:
                status["started"] = True
                return status
            last_detail = str(status.get("detail") or last_detail)
            time.sleep(0.2)
        _terminate_pid(process.pid)
        _remove_stale_state()
        raise RuntimeError(f"Oída gateway did not become ready at {url}: {last_detail}; inspect {logs}")


def stop_gateway(*, timeout: float = 10.0) -> dict[str, Any]:
    """Stop only a process that Oída's lifecycle manager started."""
    with _lifecycle_lock():
        state = _read_state()
        if not state or not state.get("pid"):
            return {"stopped": False, "detail": "no managed Oída gateway state exists"}
        pid = int(state["pid"])
        if not _pid_exists(pid):
            _remove_stale_state()
            return {"stopped": False, "detail": "managed Oída gateway was already stopped", "pid": pid}
        _terminate_pid(pid)
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline and _pid_exists(pid):
            time.sleep(0.1)
        if _pid_exists(pid):
            # SSE and mounted MCP connections can keep Uvicorn's graceful
            # shutdown open indefinitely. A user-requested stop owns this
            # managed PID, so finish the shutdown after the grace period.
            _kill_pid(pid)
            force_deadline = time.monotonic() + 2.0
            while time.monotonic() < force_deadline and _pid_exists(pid):
                time.sleep(0.05)
            if _pid_exists(pid):
                return {
                    "stopped": False,
                    "detail": "gateway did not exit after SIGTERM and SIGKILL",
                    "pid": pid,
                }
        _remove_stale_state()
        return {"stopped": True, "pid": pid, "forced": time.monotonic() >= deadline}


def doctor() -> dict[str, Any]:
    """Read-only integration diagnostics suitable for humans and installers."""
    config = load_config()
    packages = {}
    for name in (
        "sonicfield-oida",
        "akouo-contract",
        "akousma",
        "akousmata",
        "mcp",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    model_paths = {
        "instruct": {"value": config.instruct_model, "local": Path(config.instruct_model).expanduser().exists()},
        "thinking": {"value": config.thinking_model, "local": Path(config.thinking_model).expanduser().exists()},
    }
    checks = {
        "packages": packages,
        "executables": {
            name: shutil.which(name)
            for name in ("ffmpeg", "hermes", "codex", "claude", "openclaw", "opencode")
        },
        "gateway": gateway_status(),
        "models": model_paths,
        "directories": {
            "data": str(config.data_dir),
            "audio": str(config.audio_dir),
        },
        "moss_runtime": {
            "dyld_library_path": _gateway_environment("mac-mps").get(
                "DYLD_LIBRARY_PATH"
            ),
        },
    }
    required_packages = (
        "sonicfield-oida",
        "akouo-contract",
        "akousma",
        "akousmata",
        "mcp",
    )
    checks["ok"] = all(packages[name] for name in required_packages) and bool(checks["executables"]["ffmpeg"])
    return checks


def _gateway_environment(profile: str) -> dict[str, str]:
    """Build the child environment, including verified macOS audio libraries."""
    environment = os.environ.copy()
    if profile != "mac-mps" or sys.platform != "darwin":
        return environment
    libraries: list[str] = []
    torch_spec = importlib.util.find_spec("torch")
    if torch_spec and torch_spec.origin:
        torch_lib = Path(torch_spec.origin).resolve().parent / "lib"
        if torch_lib.is_dir():
            libraries.append(str(torch_lib))
    brew = shutil.which("brew")
    if brew:
        try:
            completed = subprocess.run(
                [brew, "--prefix", "ffmpeg"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ffmpeg_lib = Path(completed.stdout.strip()) / "lib"
            if completed.returncode == 0 and ffmpeg_lib.is_dir():
                libraries.append(str(ffmpeg_lib))
        except (OSError, subprocess.TimeoutExpired):
            pass
    for fallback in (Path("/opt/homebrew/opt/ffmpeg/lib"), Path("/opt/homebrew/lib")):
        if fallback.is_dir():
            libraries.append(str(fallback))
    existing = environment.get("DYLD_LIBRARY_PATH", "")
    if existing:
        libraries.extend(part for part in existing.split(":") if part)
    if libraries:
        environment["DYLD_LIBRARY_PATH"] = ":".join(dict.fromkeys(libraries))
    return environment


@contextmanager
def _lifecycle_lock() -> Iterator[None]:
    lock_path = data_dir() / "gateway.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows fallback
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover
            pass
        handle.close()


def _read_state() -> dict[str, Any] | None:
    path = state_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _remove_stale_state() -> None:
    try:
        state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _require_requested_profile(status: dict[str, Any], profile: str | None) -> None:
    if not profile:
        return
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    running_profile = health.get("profile")
    if running_profile and running_profile != profile:
        raise RuntimeError(
            f"Oída is already running with profile {running_profile!r}, not {profile!r}; "
            "stop the managed gateway first or use the running profile"
        )


def _pid_exists(pid: int) -> bool:
    # When start/stop happen in the same process (tests, supervisors), reap an
    # exited child before os.kill(pid, 0) mistakes the zombie for a live daemon.
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _is_local_url(url: str) -> bool:
    return any(token in url.lower() for token in ("127.0.0.1", "localhost", "[::1]"))


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
