#!/bin/sh
# Sync an operator's Oída environment (.venv) to uv.lock, with the embedded MOSS-Audio stack.
#
# The MOSS stack (torch, torchaudio, torchcodec, transformers) is the `moss` extra, so a plain
# `uv sync --locked` uninstalls it from an environment that hears with MOSS. This script is the one
# way to sync such an environment: it always passes `--extra moss` (and any further arguments, such
# as `--extra dev`), refuses while an Oída server runs from this environment, and checks afterwards
# that the stack imports.
#
#   scripts/sync_operator.sh            # sync
#   scripts/sync_operator.sh --dry-run  # show what would change
set -eu
cd "$(dirname "$0")/.."
venv="$(pwd)/.venv"
case " $* " in *" --dry-run "*) dry=1 ;; *) dry=0 ;; esac
if [ "$dry" = 0 ] && pgrep -f "^$venv/bin/python.* -m oida.server" >/dev/null 2>&1; then
  echo "An Oída server is running from $venv; stop it before syncing, and start it again after." >&2
  exit 1
fi
env -u VIRTUAL_ENV -u PYTHONPATH -u PYTHONHOME uv sync --locked --extra moss "$@"
[ "$dry" = 1 ] && exit 0
"$venv/bin/python" -I -c '
import torch, torchaudio, torchcodec, transformers
from importlib import metadata
print("oida", metadata.version("sonicfield-oida"), "| torch", torch.__version__, "| torchaudio", torchaudio.__version__,
      "| torchcodec", torchcodec.__version__, "| transformers", transformers.__version__, "| mps", torch.backends.mps.is_available())
'
