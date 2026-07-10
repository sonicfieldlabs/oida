#!/usr/bin/env bash
set -euo pipefail

# Derive the repo root from this script's location so the defaults are portable rather
# than hardcoded to one machine's home directory. OIDA_* is the documented config
# interface (read by oida.config); pre-set legacy AEAR_* values are still honored.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export OIDA_ENGINE_PROFILE="${OIDA_ENGINE_PROFILE:-${AEAR_ENGINE_PROFILE:-mac-mps}}"
export OIDA_MOSS_AUDIO_REPO="${OIDA_MOSS_AUDIO_REPO:-${AEAR_MOSS_AUDIO_REPO:-$ROOT_DIR/MOSS-Audio}}"
export OIDA_MOSS_INSTRUCT_MODEL="${OIDA_MOSS_INSTRUCT_MODEL:-${AEAR_MOSS_INSTRUCT_MODEL:-$ROOT_DIR/weights/MOSS-Audio-4B-Instruct}}"
export OIDA_MOSS_THINKING_MODEL="${OIDA_MOSS_THINKING_MODEL:-${AEAR_MOSS_THINKING_MODEL:-$ROOT_DIR/weights/MOSS-Audio-4B-Thinking}}"
export OIDA_MOSS_RESIDENT="${OIDA_MOSS_RESIDENT:-${AEAR_MOSS_RESIDENT:-single}}"
TORCH_LIB="$(uv run --no-sync python -c 'import pathlib, torch; print(pathlib.Path(torch.__file__).parent / "lib")')"
FFMPEG_LIB="$(brew --prefix ffmpeg)/lib"
export DYLD_LIBRARY_PATH="$TORCH_LIB:$FFMPEG_LIB${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"

exec uv run --no-sync oida --profile mac-mps --host "${OIDA_HOST:-${AEAR_HOST:-127.0.0.1}}" --port "${OIDA_PORT:-${AEAR_PORT:-8765}}"
