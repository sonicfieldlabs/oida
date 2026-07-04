from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from oida.config import uploads_dir

NATIVE_SYSTEM_AUDIO_TEMP_PATTERN = "*-oida-native-system-output-*s.wav"
NATIVE_TEMP_RETENTION_POLICIES = {"keep", "delete_after_session", "delete_after_days"}


def default_native_temp_audio_retention() -> dict[str, Any]:
    return {
        "policy": "delete_after_session",
        "delete_after_days": 1.0,
        "max_files": 24,
        "delete_after_analysis": False,
    }


def normalize_native_temp_audio_retention(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    base = default_native_temp_audio_retention()
    incoming = value if isinstance(value, Mapping) else {}

    policy = str(incoming.get("policy") or base["policy"])
    if policy not in NATIVE_TEMP_RETENTION_POLICIES:
        policy = str(base["policy"])

    return {
        "policy": policy,
        "delete_after_days": _positive_float(incoming.get("delete_after_days"), base["delete_after_days"]),
        "max_files": _positive_int(incoming.get("max_files"), base["max_files"]),
        "delete_after_analysis": bool(incoming.get("delete_after_analysis", base["delete_after_analysis"])),
    }


def native_temp_audio_directory() -> Path:
    return uploads_dir()


def native_system_audio_temp_status(
    retention: Mapping[str, Any] | None = None,
    *,
    directory: Path | None = None,
) -> dict[str, Any]:
    files = native_system_audio_temp_files(directory=directory)
    return {
        "raw_audio_policy": "temp",
        "directory": str((directory or native_temp_audio_directory()).resolve()),
        "pattern": NATIVE_SYSTEM_AUDIO_TEMP_PATTERN,
        "retention": normalize_native_temp_audio_retention(retention),
        "file_count": len(files),
        "bytes": sum(int(item["bytes"]) for item in files),
        "files": files,
    }


def native_system_audio_temp_files(*, directory: Path | None = None) -> list[dict[str, Any]]:
    root = (directory or native_temp_audio_directory()).resolve()
    if not root.exists():
        return []
    now = datetime.now(timezone.utc).timestamp()
    files: list[dict[str, Any]] = []
    for path in root.glob(NATIVE_SYSTEM_AUDIO_TEMP_PATTERN):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        files.append(
            {
                "path": str(path.resolve()),
                "name": path.name,
                "bytes": stat.st_size,
                "modified_at": modified.isoformat().replace("+00:00", "Z"),
                "modified_epoch": stat.st_mtime,
                "age_hours": max(0.0, (now - stat.st_mtime) / 3600.0),
            }
        )
    return sorted(files, key=lambda item: float(item["modified_epoch"]), reverse=True)


def cleanup_native_system_audio_temp_files(
    retention: Mapping[str, Any] | None = None,
    *,
    directory: Path | None = None,
    delete_all: bool = False,
    dry_run: bool = False,
    max_age_hours: float | None = None,
    max_files: int | None = None,
    delete_paths: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    root = (directory or native_temp_audio_directory()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    policy = normalize_native_temp_audio_retention(retention)
    files = native_system_audio_temp_files(directory=root)
    by_path = {Path(str(item["path"])).resolve(): item for item in files}
    selected: dict[Path, dict[str, Any]] = {}

    if delete_all:
        selected.update(by_path)

    if delete_paths:
        for candidate in delete_paths:
            path = Path(candidate).expanduser().resolve()
            if path in by_path:
                selected[path] = by_path[path]

    effective_age = max_age_hours
    effective_max_files = max_files
    if effective_age is None and policy["policy"] == "delete_after_days":
        effective_age = float(policy["delete_after_days"]) * 24.0
    if effective_max_files is None and policy["policy"] != "keep":
        effective_max_files = int(policy["max_files"])

    if effective_age is not None:
        age = max(0.0, float(effective_age))
        for path, item in by_path.items():
            if float(item["age_hours"]) >= age:
                selected[path] = item

    if effective_max_files is not None:
        keep_count = max(0, int(effective_max_files))
        for item in files[keep_count:]:
            path = Path(str(item["path"])).resolve()
            selected[path] = item

    deleted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path, item in sorted(selected.items(), key=lambda pair: float(pair[1]["modified_epoch"])):
        public_item = _public_file_item(item)
        if dry_run:
            deleted.append(public_item)
            continue
        try:
            path.unlink()
            deleted.append(public_item)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})

    status = native_system_audio_temp_status(policy, directory=root)
    return {
        "raw_audio_policy": "temp",
        "directory": str(root),
        "pattern": NATIVE_SYSTEM_AUDIO_TEMP_PATTERN,
        "retention": policy,
        "dry_run": dry_run,
        "files_before": len(files),
        "bytes_before": sum(int(item["bytes"]) for item in files),
        "deleted_count": len(deleted),
        "deleted_bytes": sum(int(item["bytes"]) for item in deleted),
        "deleted": deleted,
        "errors": errors,
        "status": status,
    }


def apply_native_temp_audio_retention_after_analysis(
    retention: Mapping[str, Any] | None,
    analyzed_path: str | Path,
) -> dict[str, Any]:
    policy = normalize_native_temp_audio_retention(retention)
    delete_paths: list[str | Path] = []
    if policy["delete_after_analysis"]:
        delete_paths.append(analyzed_path)
    return cleanup_native_system_audio_temp_files(policy, delete_paths=delete_paths)


def finalize_native_temp_audio_session(
    retention: Mapping[str, Any] | None = None,
    *,
    directory: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply session-end retention.

    The default ``delete_after_session`` policy removes all native system-audio temp
    captures when the daemon session ends (wired into the FastAPI shutdown handler);
    other policies fall back to their normal age/count cleanup. Without this the
    ``delete_after_session`` default was a no-op and raw temp WAVs persisted until the
    file-count cap evicted them.
    """
    policy = normalize_native_temp_audio_retention(retention)
    delete_all = policy["policy"] == "delete_after_session"
    return cleanup_native_system_audio_temp_files(
        policy, directory=directory, delete_all=delete_all, dry_run=dry_run
    )


def _public_file_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": item["path"],
        "name": item["name"],
        "bytes": item["bytes"],
        "modified_at": item["modified_at"],
        "age_hours": item["age_hours"],
    }


def _positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if parsed <= 0:
        return float(fallback)
    return parsed


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    if parsed < 0:
        return int(fallback)
    return parsed
