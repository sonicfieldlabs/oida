from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTRUCT = REPO_ROOT / "weights" / "MOSS-Audio-4B-Instruct"
DEFAULT_THINKING = REPO_ROOT / "weights" / "MOSS-Audio-4B-Thinking"
DEFAULT_MOSS_REPO = REPO_ROOT / "MOSS-Audio"
HF_INSTRUCT_ID = "OpenMOSS-Team/MOSS-Audio-4B-Instruct"
HF_THINKING_ID = "OpenMOSS-Team/MOSS-Audio-4B-Thinking"


@dataclass(frozen=True)
class AearConfig:
    profile: str
    host: str
    port: int
    data_dir: Path
    audio_dir: Path
    moss_audio_repo: Path | None
    instruct_model: str
    thinking_model: str
    sglang_base_url: str
    require_model: bool
    resident_mode: str
    prewarm: bool
    # Longest audio fed to MOSS in one pass; longer files are chunked. On MPS,
    # single passes beyond ~60 s slow down sharply and can destabilize decoding.
    moss_chunk_seconds: float
    allow_hf_hub: bool
    hf_hub_offline: bool
    auth_token: str | None
    sonicfield_root: Path | None


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "hmm"
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "hmm"
    return Path.home() / ".local" / "share" / "hmm"


def data_dir() -> Path:
    configured = os.getenv("HMM_DATA_DIR") or os.getenv("AEAR_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else default_data_dir().resolve()


def default_audio_dir() -> Path:
    return Path.home() / "Documents" / "hmm" / "audio"


def audio_dir() -> Path:
    """Where captures, uploads, and generated fixtures land. User-visible by design."""
    configured = os.getenv("HMM_AUDIO_DIR") or os.getenv("AEAR_AUDIO_DIR")
    return Path(configured).expanduser().resolve() if configured else default_audio_dir().resolve()


def uploads_dir() -> Path:
    return audio_dir()


def default_sonicfield_root() -> Path | None:
    configured = os.getenv("HMM_SONICFIELD_ROOT") or os.getenv("AEAR_SONICFIELD_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    candidate = Path.home() / "Documents" / "sonicfield"
    return candidate if candidate.exists() else None


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _model_value(env_name: str, local_default: Path, hub_id: str, *, allow_hub: bool) -> str:
    configured = os.getenv(env_name)
    if configured:
        return configured
    if local_default.exists():
        return str(local_default)
    return hub_id if allow_hub else str(local_default)


def load_config(profile: str | None = None, host: str | None = None, port: int | None = None) -> AearConfig:
    moss_repo = _optional_path(os.getenv("AEAR_MOSS_AUDIO_REPO")) or (DEFAULT_MOSS_REPO if DEFAULT_MOSS_REPO.exists() else None)
    hf_hub_offline = _truthy_env("HF_HUB_OFFLINE")
    allow_hf_hub = _truthy_env("AEAR_ALLOW_HF_HUB") or _truthy_env("HMM_ALLOW_HF_HUB")
    if hf_hub_offline:
        allow_hf_hub = False
    instruct = _model_value("AEAR_MOSS_INSTRUCT_MODEL", DEFAULT_INSTRUCT, HF_INSTRUCT_ID, allow_hub=allow_hf_hub)
    thinking = _model_value("AEAR_MOSS_THINKING_MODEL", DEFAULT_THINKING, HF_THINKING_ID, allow_hub=allow_hf_hub)
    resolved_profile = profile or os.getenv("HMM_ENGINE_PROFILE") or os.getenv("AEAR_ENGINE_PROFILE", "mac-mps")
    default_chunk = "45" if resolved_profile == "mac-mps" else "600"
    return AearConfig(
        profile=resolved_profile,
        host=host or os.getenv("HMM_HOST") or os.getenv("AEAR_HOST", "127.0.0.1"),
        port=port or int(os.getenv("HMM_PORT") or os.getenv("AEAR_PORT", "8765")),
        data_dir=data_dir(),
        audio_dir=audio_dir(),
        moss_audio_repo=moss_repo,
        instruct_model=instruct,
        thinking_model=thinking,
        sglang_base_url=os.getenv("AEAR_SGLANG_BASE_URL", "http://127.0.0.1:30000"),
        require_model=os.getenv("AEAR_REQUIRE_MODEL", "0") == "1",
        resident_mode=os.getenv("AEAR_MOSS_RESIDENT", "single"),
        prewarm=_truthy_env("HMM_MOSS_PREWARM", "1") and _truthy_env("AEAR_MOSS_PREWARM", "1"),
        moss_chunk_seconds=float(os.getenv("HMM_MOSS_CHUNK_SECONDS") or os.getenv("AEAR_MOSS_CHUNK_SECONDS") or default_chunk),
        allow_hf_hub=allow_hf_hub,
        hf_hub_offline=hf_hub_offline,
        auth_token=os.getenv("HMM_AUTH_TOKEN") or os.getenv("AEAR_AUTH_TOKEN") or None,
        sonicfield_root=default_sonicfield_root(),
    )
