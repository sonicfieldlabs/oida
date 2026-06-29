from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTRUCT = REPO_ROOT / "weights" / "MOSS-Audio-4B-Instruct"
DEFAULT_THINKING = REPO_ROOT / "weights" / "MOSS-Audio-4B-Thinking"
DEFAULT_MOSS_REPO = REPO_ROOT / "MOSS-Audio"


@dataclass(frozen=True)
class AearConfig:
    profile: str
    host: str
    port: int
    moss_audio_repo: Path | None
    instruct_model: str
    thinking_model: str
    sglang_base_url: str
    require_model: bool
    resident_mode: str


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()


def load_config(profile: str | None = None, host: str | None = None, port: int | None = None) -> AearConfig:
    moss_repo = _optional_path(os.getenv("AEAR_MOSS_AUDIO_REPO")) or (DEFAULT_MOSS_REPO if DEFAULT_MOSS_REPO.exists() else None)
    instruct = os.getenv("AEAR_MOSS_INSTRUCT_MODEL") or (str(DEFAULT_INSTRUCT) if DEFAULT_INSTRUCT.exists() else "OpenMOSS-Team/MOSS-Audio-4B-Instruct")
    thinking = os.getenv("AEAR_MOSS_THINKING_MODEL") or (str(DEFAULT_THINKING) if DEFAULT_THINKING.exists() else "OpenMOSS-Team/MOSS-Audio-4B-Thinking")
    return AearConfig(
        profile=profile or os.getenv("AEAR_ENGINE_PROFILE", "mac-mps"),
        host=host or os.getenv("AEAR_HOST", "127.0.0.1"),
        port=port or int(os.getenv("AEAR_PORT", "8765")),
        moss_audio_repo=moss_repo,
        instruct_model=instruct,
        thinking_model=thinking,
        sglang_base_url=os.getenv("AEAR_SGLANG_BASE_URL", "http://127.0.0.1:30000"),
        require_model=os.getenv("AEAR_REQUIRE_MODEL", "0") == "1",
        resident_mode=os.getenv("AEAR_MOSS_RESIDENT", "single"),
    )
