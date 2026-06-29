#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-default}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

uv run python -m unittest discover -s tests
uv run python -m compileall -q aear harness bench_adapter scripts tests

if command -v node >/dev/null 2>&1; then
  node --check aear/static/app.js
fi

case "$MODE" in
  default)
    ;;
  --release|release)
    apps/macos/script/build_and_run.sh --verify
    apps/macos/script/package_unsigned.sh
    uv run python scripts/release_smoke.py --server "${HMM_SERVER:-http://127.0.0.1:8765}"
    ;;
  *)
    echo "usage: $0 [release|--release]" >&2
    exit 2
    ;;
esac
