#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-default}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

# pytest collects the unittest.TestCase suite too; one runner is enough.
uv run --extra dev pytest -q
uv run python -m compileall -q oida harness bench_adapter scripts tests

if command -v node >/dev/null 2>&1; then
  node --check oida/static/app.js
fi

case "$MODE" in
  default)
    ;;
  --release|release)
    uv build
    apps/macos/script/build_and_run.sh --verify
    apps/macos/script/package_unsigned.sh
    scripts/release_smoke_with_stub.sh
    ;;
  *)
    echo "usage: $0 [release|--release]" >&2
    exit 2
    ;;
esac
