"""Stop the capture child if its owner disappears or requests termination."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading


def main() -> int:
    command = json.loads(sys.stdin.buffer.readline(65537))
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(v, str) for v in command)
    ):
        return 2
    stopped = [False]
    owner_closed = threading.Event()

    def stop(signum, frame):
        stopped[0] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def watch_owner():
        # Owner keeps the pipe open for the entire capture. EOF also detects crashes.
        while os.read(0, 4096):
            pass
        owner_closed.set()

    threading.Thread(target=watch_owner, daemon=True).start()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while process.poll() is None:
            if stopped[0] or owner_closed.wait(0.05):
                return 1
        return process.returncode
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


if __name__ == "__main__":
    raise SystemExit(main())
