"""Run release acceptance against an isolated, PID-verified disposable stub."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="oida-release-smoke-") as directory:
        scratch = Path(directory)
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                ("OIDA_", "HMM_", "AEAR_", "AKOUSMATA_", "LISTENINGSTACK_")
            )
        }
        env.update(
            {
                "OIDA_DATA_DIR": str(scratch / "data"),
                "OIDA_AUDIO_DIR": str(scratch / "audio"),
                "OIDA_TRIAL_DIR": str(scratch / "trial"),
                "AKOUSMATA_PATH": str(scratch / "memory"),
                "AKOUSMATA_WATCHER": "0",
                "HF_HUB_OFFLINE": "1",
                "OIDA_ALLOW_HF_HUB": "0",
                "OIDA_PREWARM": "0",
            }
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        server = f"http://127.0.0.1:{port}"
        with (scratch / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "oida.cli",
                    "serve",
                    "--profile",
                    "stub",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with opener.open(server + "/health", timeout=1) as response:
                            health = json.loads(response.read(65536))
                        if (
                            health.get("pid") == process.pid
                            and health.get("profile") == "stub"
                            and Path(str(health.get("data_dir", ""))).resolve()
                            == (scratch / "data").resolve()
                        ):
                            break
                        raise RuntimeError("release smoke reached an unexpected daemon")
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.1)
                else:
                    raise RuntimeError("isolated stub did not become healthy")
                return subprocess.run(
                    [
                        sys.executable,
                        "scripts/release_smoke.py",
                        "--server",
                        server,
                        "--expect-profile",
                        "stub",
                    ],
                    cwd=root,
                    env=env,
                    check=False,
                ).returncode
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
