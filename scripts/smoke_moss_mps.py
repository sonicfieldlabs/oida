from __future__ import annotations

import os
import sys
from pathlib import Path

from oida.config import load_config
from oida.engine import build_engine
from oida.recipes import get_recipe


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    _ensure_macos_homebrew_dylibs()
    os.environ.setdefault("OIDA_ENGINE_PROFILE", "mac-mps")
    os.environ.setdefault("OIDA_MOSS_AUDIO_REPO", str(repo / "MOSS-Audio"))
    os.environ.setdefault("OIDA_MOSS_INSTRUCT_MODEL", str(repo / "weights" / "MOSS-Audio-4B-Instruct"))
    os.environ.setdefault("OIDA_MOSS_THINKING_MODEL", str(repo / "weights" / "MOSS-Audio-4B-Thinking"))
    os.environ.setdefault("OIDA_MOSS_RESIDENT", "single")
    audio_path = repo / "MOSS-Audio" / "test" / "test_en.mp3"
    engine = build_engine(load_config(profile="mac-mps"))
    recipe = get_recipe("caption_brief")
    result = engine.generate(str(audio_path), recipe.prompt, recipe.settings)
    print(result.text.strip())
    print(f"model={result.model}")
    print(f"profile={result.profile}")
    print(f"wall_ms={result.wall_ms}")


def _ensure_macos_homebrew_dylibs() -> None:
    if sys.platform != "darwin" or os.environ.get("DYLD_LIBRARY_PATH"):
        return
    homebrew_lib = Path("/opt/homebrew/lib")
    if not homebrew_lib.exists():
        return
    env = dict(os.environ)
    env["DYLD_LIBRARY_PATH"] = str(homebrew_lib)
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


if __name__ == "__main__":
    main()
