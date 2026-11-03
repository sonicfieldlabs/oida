#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-default}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

# pytest collects the unittest.TestCase suite too; one runner is enough.
uv run --extra dev pytest -q
uv run --extra dev ruff check .
uv run python -m compileall -q oida harness bench_adapter scripts tests

# Use the same unsuppressed, policy-aware audit as CI.
audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/oida-audit.XXXXXX")"
trap 'rm -rf "$audit_dir"' EXIT
uv export --locked --all-extras --no-emit-project --no-emit-local > "$audit_dir/dependencies.txt"
uv run python scripts/audit_dependencies.py \
  --requirements "$audit_dir/dependencies.txt" \
  --output "$audit_dir/dependency-audit.json"

if command -v node >/dev/null 2>&1; then
  node --check oida/static/app.js
  node --check oida/static/remote.js
fi

case "$MODE" in
  default)
    ;;
  --release|release)
    uv build
    uv run --extra dev twine check dist/*
    (cd apps/macos && swift build -Xswiftc -strict-concurrency=complete)
    apps/macos/script/package_unsigned.sh
    scripts/release_smoke_with_stub.sh
    ;;
  *)
    echo "usage: $0 [release|--release]" >&2
    exit 2
    ;;
esac
