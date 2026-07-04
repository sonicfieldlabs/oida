#!/usr/bin/env bash
set -euo pipefail

# Derive the repo root from this script's location so the defaults are portable rather
# than hardcoded to one machine's home directory. The AEAR_* names are the documented
# config interface (read by oida.config) and remain overridable.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export AEAR_ENGINE_PROFILE="${AEAR_ENGINE_PROFILE:-mac-mps}"
export AEAR_MOSS_AUDIO_REPO="${AEAR_MOSS_AUDIO_REPO:-$ROOT_DIR/MOSS-Audio}"
export AEAR_MOSS_INSTRUCT_MODEL="${AEAR_MOSS_INSTRUCT_MODEL:-$ROOT_DIR/weights/MOSS-Audio-4B-Instruct}"
export AEAR_MOSS_THINKING_MODEL="${AEAR_MOSS_THINKING_MODEL:-$ROOT_DIR/weights/MOSS-Audio-4B-Thinking}"
export AEAR_MOSS_RESIDENT="${AEAR_MOSS_RESIDENT:-single}"
export DYLD_LIBRARY_PATH="${DYLD_LIBRARY_PATH:-/opt/homebrew/lib}"

exec uv run oida --profile mac-mps --host "${AEAR_HOST:-127.0.0.1}" --port "${AEAR_PORT:-8765}"
