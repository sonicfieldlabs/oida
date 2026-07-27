#!/usr/bin/env bash
set -euo pipefail

HOST="${OIDA_HOST:-${HMM_HOST:-127.0.0.1}}"
PORT="${OIDA_PORT:-${HMM_PORT:-8765}}"
SERVER="http://$HOST:$PORT"
LOG_FILE="${OIDA_SMOKE_LOG:-${HMM_SMOKE_LOG:-/tmp/oida-release-smoke.log}}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && ps -p "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

uv run oida --profile stub --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
SERVER_PID="$!"

uv run python - "$SERVER/health" <<'PY'
from __future__ import annotations

import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

url = sys.argv[1]
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    try:
        with urlopen(url, timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
    except URLError:
        time.sleep(0.25)
raise SystemExit(f"daemon did not become healthy: {url}")
PY

scripts/release_smoke.py --server "$SERVER" --expect-profile stub
