from __future__ import annotations

import os
import json
import math
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
class OidaConfig:
    profile: str
    host: str
    port: int
    data_dir: Path
    audio_dir: Path
    moss_audio_repo: Path | None
    instruct_model: str
    thinking_model: str
    sglang_base_url: str
    sglang_thinking_processor: str | None
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
    trusted_hosts: tuple[str, ...]


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _env(*names: str, default: str | None = None) -> str | None:
    """First non-empty env var among ``names`` (OIDA_* primary, then legacy
    HMM_*/AEAR_* fallbacks), else ``default``."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _app_support_base() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.getenv("XDG_DATA_HOME")
    return Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"


def default_data_dir() -> Path:
    base = _app_support_base()
    oida_dir = base / "oida"
    legacy = base / "hmm"
    # Honor a pre-rename data directory if the new one has not been created yet,
    # so listening memory captured under the old name is not orphaned.
    if not oida_dir.exists() and legacy.exists():
        return legacy
    return oida_dir


def data_dir() -> Path:
    configured = _env("OIDA_DATA_DIR", "HMM_DATA_DIR", "AEAR_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else default_data_dir().resolve()


def default_audio_dir() -> Path:
    return Path.home() / "Documents" / "oida" / "audio"


def audio_dir() -> Path:
    """Where captures, uploads, and generated fixtures land. User-visible by design."""
    configured = _env("OIDA_AUDIO_DIR", "HMM_AUDIO_DIR", "AEAR_AUDIO_DIR")
    return Path(configured).expanduser().resolve() if configured else default_audio_dir().resolve()


def uploads_dir() -> Path:
    return audio_dir()


def default_sonicfield_root() -> Path | None:
    configured = _env("OIDA_SONICFIELD_ROOT", "HMM_SONICFIELD_ROOT", "AEAR_SONICFIELD_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    for candidate in (
        Path.home() / "Documents" / "SFL" / "sonicfield",
        Path.home() / "Documents" / "sonicfield",
    ):
        if candidate.exists():
            return candidate
    return None


def integration_settings_path() -> Path:
    return data_dir() / "integrations.json"


def _trusted_hosts() -> tuple[str, ...]:
    values: list[str] = []
    configured = _env("OIDA_TRUSTED_HOSTS", "HMM_TRUSTED_HOSTS", "AEAR_TRUSTED_HOSTS")
    if configured:
        values.extend(part.strip().lower().rstrip(".") for part in configured.split(",") if part.strip())
    path = integration_settings_path()
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            remote = settings.get("remote") if isinstance(settings, dict) and isinstance(settings.get("remote"), dict) else {}
            hosts = remote.get("trusted_hosts") if isinstance(remote.get("trusted_hosts"), list) else []
            values.extend(str(host).strip().lower().rstrip(".") for host in hosts if str(host).strip())
        except (OSError, json.JSONDecodeError):
            pass
    return tuple(dict.fromkeys(values))


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _model_value(configured: str | None, local_default: Path, hub_id: str, *, allow_hub: bool) -> str:
    if configured:
        return configured
    if local_default.exists():
        return str(local_default)
    return hub_id if allow_hub else str(local_default)


def _positive_float(value: str | None, *, name: str) -> float:
    try:
        parsed = float(value or "")
    except ValueError as exc:
        raise ValueError(f"{name} must be a number greater than zero") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be a finite number greater than zero")
    return parsed


def load_config(profile: str | None = None, host: str | None = None, port: int | None = None) -> OidaConfig:
    moss_repo = _optional_path(_env("OIDA_MOSS_AUDIO_REPO", "HMM_MOSS_AUDIO_REPO", "AEAR_MOSS_AUDIO_REPO")) or (
        DEFAULT_MOSS_REPO if DEFAULT_MOSS_REPO.exists() else None
    )
    hf_hub_offline = _truthy_env("HF_HUB_OFFLINE")
    allow_hf_hub = (
        _truthy_env("OIDA_ALLOW_HF_HUB")
        or _truthy_env("HMM_ALLOW_HF_HUB")
        or _truthy_env("AEAR_ALLOW_HF_HUB")
    )
    if hf_hub_offline:
        allow_hf_hub = False
    instruct = _model_value(
        _env("OIDA_MOSS_INSTRUCT_MODEL", "HMM_MOSS_INSTRUCT_MODEL", "AEAR_MOSS_INSTRUCT_MODEL"),
        DEFAULT_INSTRUCT, HF_INSTRUCT_ID, allow_hub=allow_hf_hub,
    )
    thinking = _model_value(
        _env("OIDA_MOSS_THINKING_MODEL", "HMM_MOSS_THINKING_MODEL", "AEAR_MOSS_THINKING_MODEL"),
        DEFAULT_THINKING, HF_THINKING_ID, allow_hub=allow_hf_hub,
    )
    resolved_profile = profile or _env("OIDA_ENGINE_PROFILE", "HMM_ENGINE_PROFILE", "AEAR_ENGINE_PROFILE", default="mac-mps")
    default_chunk = "45" if resolved_profile == "mac-mps" else "600"
    chunk_seconds = _positive_float(
        _env("OIDA_MOSS_CHUNK_SECONDS", "HMM_MOSS_CHUNK_SECONDS", "AEAR_MOSS_CHUNK_SECONDS", default=default_chunk),
        name="OIDA_MOSS_CHUNK_SECONDS",
    )
    return OidaConfig(
        profile=resolved_profile,
        host=host or _env("OIDA_HOST", "HMM_HOST", "AEAR_HOST", default="127.0.0.1"),
        port=port or int(_env("OIDA_PORT", "HMM_PORT", "AEAR_PORT", default="8765")),
        data_dir=data_dir(),
        audio_dir=audio_dir(),
        moss_audio_repo=moss_repo,
        instruct_model=instruct,
        thinking_model=thinking,
        sglang_base_url=_env("OIDA_SGLANG_BASE_URL", "HMM_SGLANG_BASE_URL", "AEAR_SGLANG_BASE_URL", default="http://127.0.0.1:30000"),
        sglang_thinking_processor=_env("OIDA_SGLANG_THINKING_PROCESSOR"),
        require_model=_env("OIDA_REQUIRE_MODEL", "HMM_REQUIRE_MODEL", "AEAR_REQUIRE_MODEL", default="0").strip().lower() in {"1", "true", "yes", "on"},
        resident_mode=_env("OIDA_MOSS_RESIDENT", "HMM_MOSS_RESIDENT", "AEAR_MOSS_RESIDENT", default="single"),
        # first-set name wins, like every other setting; an AND-chain would let
        # a leftover legacy HMM_/AEAR_MOSS_PREWARM=0 override OIDA_MOSS_PREWARM=1
        prewarm=_env("OIDA_MOSS_PREWARM", "HMM_MOSS_PREWARM", "AEAR_MOSS_PREWARM", default="1").strip().lower() in {"1", "true", "yes", "on"},
        moss_chunk_seconds=chunk_seconds,
        allow_hf_hub=allow_hf_hub,
        hf_hub_offline=hf_hub_offline,
        auth_token=_env("OIDA_AUTH_TOKEN", "HMM_AUTH_TOKEN", "AEAR_AUTH_TOKEN"),
        sonicfield_root=default_sonicfield_root(),
        trusted_hosts=_trusted_hosts(),
    )
