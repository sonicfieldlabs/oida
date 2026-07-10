from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from oida.config import REPO_ROOT, uploads_dir

UPLOAD_AUDIO_RETENTION_POLICIES = {"keep", "delete_after_session", "delete_after_days"}


def legacy_uploads_dir() -> Path | None:
    """Pre-data-dir uploads/ location inside the source checkout, when distinct.

    Older daemon builds wrote raw uploads and live chunks to ``<repo>/uploads``.
    Those recordings must stay reachable by the wipe endpoint, but they are only
    swept when a caller explicitly asks (``include_legacy``) so that routine
    cleanup never touches the checkout implicitly.
    """
    legacy = (REPO_ROOT / "uploads").resolve()
    try:
        current = uploads_dir().resolve()
    except OSError:
        return None
    if legacy == current or not legacy.is_dir():
        return None
    return legacy


def default_upload_audio_retention() -> dict[str, Any]:
    return {
        "policy": "keep",
        "delete_after_days": 7.0,
        "max_files": 200,
    }


def normalize_upload_audio_retention(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    base = default_upload_audio_retention()
    incoming = value if isinstance(value, Mapping) else {}
    policy = str(incoming.get("policy") or base["policy"])
    if policy not in UPLOAD_AUDIO_RETENTION_POLICIES:
        policy = str(base["policy"])
    return {
        "policy": policy,
        "delete_after_days": _positive_float(incoming.get("delete_after_days"), base["delete_after_days"]),
        "max_files": _positive_int(incoming.get("max_files"), base["max_files"]),
    }


def upload_audio_status(
    retention: Mapping[str, Any] | None = None,
    *,
    directory: Path | None = None,
) -> dict[str, Any]:
    root = (directory or uploads_dir()).resolve()
    files = upload_audio_files(directory=root)
    legacy_root = legacy_uploads_dir() if directory is None else None
    legacy_files = upload_audio_files(directory=legacy_root) if legacy_root else []
    return {
        "raw_audio_policy": "local_uploads",
        "directory": str(root),
        "retention": normalize_upload_audio_retention(retention),
        "file_count": len(files),
        "bytes": sum(int(item["bytes"]) for item in files),
        "files": files,
        "legacy_directory": str(legacy_root) if legacy_root else None,
        "legacy_file_count": len(legacy_files),
        "legacy_bytes": sum(int(item["bytes"]) for item in legacy_files),
        "legacy_files": legacy_files,
    }


def upload_audio_files(*, directory: Path | None = None) -> list[dict[str, Any]]:
    root = (directory or uploads_dir()).resolve()
    if not root.exists():
        return []
    now = datetime.now(timezone.utc).timestamp()
    files: list[dict[str, Any]] = []
    for path in root.iterdir():
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


def cleanup_upload_audio_files(
    retention: Mapping[str, Any] | None = None,
    *,
    directory: Path | None = None,
    delete_all: bool = False,
    dry_run: bool = False,
    max_age_hours: float | None = None,
    max_files: int | None = None,
    delete_paths: Iterable[str | Path] | None = None,
    include_legacy: bool = False,
) -> dict[str, Any]:
    root = (directory or uploads_dir()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    policy = normalize_upload_audio_retention(retention)
    files = upload_audio_files(directory=root)
    legacy_root = legacy_uploads_dir() if (include_legacy and directory is None) else None
    if legacy_root:
        files = sorted(
            files + upload_audio_files(directory=legacy_root),
            key=lambda item: float(item["modified_epoch"]),
            reverse=True,
        )
    by_path = {Path(str(item["path"])).resolve(): item for item in files}
    selected: dict[Path, dict[str, Any]] = {}

    if delete_all:
        selected.update(by_path)

    if delete_paths:
        for candidate in delete_paths:
            try:
                path = Path(candidate).expanduser().resolve()
            except OSError:
                continue
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

    status = upload_audio_status(policy, directory=root if directory is not None else None)
    return {
        "raw_audio_policy": "local_uploads",
        "directory": str(root),
        "legacy_directory": str(legacy_root) if legacy_root else None,
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


def finalize_upload_audio_session(
    retention: Mapping[str, Any] | None = None,
    *,
    directory: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    policy = normalize_upload_audio_retention(retention)
    delete_all = policy["policy"] == "delete_after_session"
    return cleanup_upload_audio_files(policy, directory=directory, delete_all=delete_all, dry_run=dry_run)


def delete_upload_paths(paths: Iterable[str | Path]) -> dict[str, Any]:
    # Explicitly referenced paths (e.g. a forgotten trace's stored audio) may
    # predate the platform data dir, so the legacy checkout uploads/ is a valid
    # deletion root here; only the exact requested paths are ever selected.
    return cleanup_upload_audio_files(delete_paths=paths, include_legacy=True)


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
    if not math.isfinite(parsed) or parsed <= 0:
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
