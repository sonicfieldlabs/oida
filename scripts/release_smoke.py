#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


REQUIRED_ENDPOINTS = {
    "/background/status",
    "/background/history",
    "/listen-event",
    "/listen-event/rerun",
    "/conversation/ask",
    "/generation/prompt",
    "/generation/history",
    "/generation/{generation_id}",
    "/generation/relisten",
    "/gateway/schema/host-perception",
    "/gateway/schema/listening-event",
    "/gateway/schema/listening-context",
    "/native/system-audio/routes",
    "/native/system-audio/temp",
}

REQUIRED_CAPABILITIES = {
    "daemon_background_runtime",
    "native_shell_api",
    "live_signal_api",
    "route_rerun_api",
    "recent_history_management",
    "generation_prompt_api",
    "generation_relisten_api",
}

EXPECTED_BUNDLE = {
    "CFBundleName": "oida",
    "CFBundleExecutable": "oida-macos",
    "CFBundleIdentifier": "org.sonicfield.oida",
    "CFBundlePackageType": "APPL",
}


class CheckResult:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def ok(self, message: str) -> None:
        self.notes.append(f"ok: {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(f"warn: {message}")

    def fail(self, message: str) -> None:
        self.failures.append(f"fail: {message}")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Validate local oida release artifacts and daemon contract.")
    parser.add_argument(
        "--server",
        default=os.environ.get("OIDA_SERVER_URL")
        or os.environ.get("HMM_SERVER_URL")
        or os.environ.get("AEAR_SERVER_URL")
        or os.environ.get("HMM_SERVER", "http://127.0.0.1:8765"),
    )
    parser.add_argument("--app", type=Path, default=repo_root / "apps/macos/dist/oida.app")
    parser.add_argument("--archive", type=Path, default=repo_root / "apps/macos/dist/oida-macos-unsigned.zip")
    parser.add_argument("--mutating", action="store_true", help="Create and clean up one prompt record through /generation/prompt.")
    args = parser.parse_args()

    result = CheckResult()
    app_path = resolve_app_path(args.app, repo_root)

    try:
        server = normalize_server_url(args.server)
    except ValueError as exc:
        result.fail(str(exc))
    else:
        check_daemon(server, repo_root, args.mutating, result)
    check_app_bundle(app_path, result)
    check_archive(args.archive, result)

    for line in result.notes:
        print(line)
    for line in result.warnings:
        print(line)
    for line in result.failures:
        print(line, file=sys.stderr)
    return 1 if result.failures else 0


def resolve_app_path(path: Path, repo_root: Path) -> Path:
    if path.exists():
        return path
    packaged = repo_root / "apps/macos/dist/package/oida.app"
    if path == repo_root / "apps/macos/dist/oida.app" and packaged.exists():
        return packaged
    return path


def normalize_server_url(server: str) -> str:
    normalized = server.rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("daemon URL must use http:// or https:// and include a hostname")
    return normalized


def check_daemon(server: str, repo_root: Path, mutating: bool, result: CheckResult) -> None:
    try:
        health = get_json(server, "/health")
        api = get_json(server, "/api")
        hmm_status = get_json(server, "/oida/status")
        background = get_json(server, "/background/status")
        generation_history = get_json(server, "/generation/history?limit=1")
    except urllib.error.URLError as exc:
        result.fail(f"daemon is not reachable at {server}: {exc}")
        return

    if health.get("ok") is True and health.get("name") == "oida":
        result.ok(f"daemon health {health.get('profile') or 'profile'} at {server}")
    else:
        result.fail(f"unexpected health payload: {health}")

    endpoints = set(api.get("endpoints") or [])
    missing_endpoints = sorted(REQUIRED_ENDPOINTS - endpoints)
    if missing_endpoints:
        result.fail(f"missing API endpoints: {', '.join(missing_endpoints)}")
    else:
        result.ok(f"{len(REQUIRED_ENDPOINTS)} required endpoints exposed")

    privacy = hmm_status.get("privacy_defaults") if isinstance(hmm_status.get("privacy_defaults"), dict) else {}
    if privacy.get("generation_default_adapter") == "prompt_only":
        result.ok("generation default adapter is prompt_only")
    else:
        result.fail("generation default adapter is not prompt_only")

    capabilities = background.get("capabilities") if isinstance(background.get("capabilities"), dict) else {}
    missing_capabilities = sorted(name for name in REQUIRED_CAPABILITIES if capabilities.get(name) is not True)
    if missing_capabilities:
        result.fail(f"missing background capabilities: {', '.join(missing_capabilities)}")
    else:
        result.ok(f"{len(REQUIRED_CAPABILITIES)} release capabilities exposed")

    if generation_history.get("adapter_default") == "prompt_only" and isinstance(generation_history.get("records"), list):
        result.ok("generation history endpoint responds")
    else:
        result.fail(f"unexpected generation history payload: {generation_history}")

    check_daemon_security(server, result)

    if mutating:
        smoke_generation_prompt(server, repo_root, result)


def check_daemon_security(server: str, result: CheckResult) -> None:
    if not is_loopback_server(server):
        result.warn("skipping loopback Origin/Host smoke check for non-loopback server URL")
        return
    evil_origin = expect_http_status(server, "/health", 403, headers={"origin": "http://evil.example"})
    null_origin = expect_http_status(server, "/health", 403, headers={"origin": "null"})
    evil_host = expect_http_status(server, "/health", 403, headers={"host": "evil.example"})
    if evil_origin and null_origin and evil_host:
        result.ok("loopback Origin/Host guard rejects cross-site and DNS-rebinding requests")
    else:
        result.fail("loopback Origin/Host guard did not reject all release-smoke probes")


def smoke_generation_prompt(server: str, repo_root: Path, result: CheckResult) -> None:
    payload = {
        "event": {
            "id": "evt_release_smoke",
            "source": {"type": "file", "label": "release-smoke.wav"},
            "segment": {"duration_ms": 1000, "data_ref": {"kind": "path", "uri": "release-smoke.wav"}},
            "aggregate": {
                "title": "Release smoke",
                "short_summary": "A synthetic release-check event for prompt derivation.",
                "signal_facts": ["No raw audio is required for this prompt-only check."],
                "warnings": ["This event is synthetic."],
            },
            "routes": [{"route_id": "signal-health", "summary": "synthetic stable signal"}],
            "features": {"duration_s": 1.0, "rmsDbfs": -24.0},
            "tags": ["release-smoke"],
            "privacy_mode": "session",
            "raw_audio_policy": "external_ref",
        },
        "intent": "variation",
        "adapter": "prompt_only",
        "generate": False,
    }
    try:
        record = post_json(server, "/generation/prompt", payload)
    except urllib.error.URLError as exc:
        result.fail(f"mutating prompt smoke failed: {exc}")
        return

    generation_id = str(record.get("id") or "")
    if record.get("status") == "prompt_ready" and generation_id.startswith("gen_"):
        result.ok("mutating prompt smoke returned prompt_ready")
    else:
        result.fail(f"unexpected prompt smoke payload: {record}")
        return

    # GenerationStore writes under the daemon's data dir, not the repo root.
    data_dir_env = os.environ.get("OIDA_DATA_DIR") or os.environ.get("HMM_DATA_DIR") or os.environ.get("AEAR_DATA_DIR")
    record_name = f"{safe_id(generation_id)}.json"
    candidates = [
        Path(data_dir_env).expanduser() if data_dir_env else None,
        Path.home() / "Library/Application Support/oida",
        repo_root,
    ]
    record_path = next(
        (base / "generations/records" / record_name for base in candidates if base and (base / "generations/records" / record_name).exists()),
        None,
    )
    if record_path:
        record_path.unlink()
        result.ok("mutating prompt smoke record cleaned up")
    else:
        result.warn(f"could not find prompt smoke record for cleanup: {record_name}")


def check_app_bundle(app_path: Path, result: CheckResult) -> None:
    if not app_path.exists():
        result.fail(f"app bundle is missing: {app_path}")
        return
    if app_path.suffix != ".app":
        result.fail(f"app path is not an .app bundle: {app_path}")
        return

    info_path = app_path / "Contents/Info.plist"
    binary_path = app_path / "Contents/MacOS/oida-macos"
    if not info_path.exists():
        result.fail(f"Info.plist is missing: {info_path}")
        return
    if not binary_path.exists():
        result.fail(f"app executable is missing: {binary_path}")
        return

    with info_path.open("rb") as handle:
        plist = plistlib.load(handle)
    mismatched = [
        f"{key}={plist.get(key)!r}, expected {expected!r}"
        for key, expected in EXPECTED_BUNDLE.items()
        if plist.get(key) != expected
    ]
    if mismatched:
        result.fail(f"Info.plist mismatch: {'; '.join(mismatched)}")
    else:
        result.ok(f"app bundle metadata valid: {app_path}")

    if os.access(binary_path, os.X_OK):
        result.ok("app executable bit is set")
    else:
        result.fail(f"app executable is not executable: {binary_path}")

    if not (app_path / "Contents/_CodeSignature").exists():
        result.warn("app bundle is unsigned; this is expected for the local unsigned archive")


def check_archive(archive_path: Path, result: CheckResult) -> None:
    if not archive_path.exists():
        result.fail(f"unsigned archive is missing: {archive_path}")
        return
    if archive_path.stat().st_size <= 0:
        result.fail(f"unsigned archive is empty: {archive_path}")
        return
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        result.fail(f"unsigned archive is not a valid zip file: {archive_path}")
        return

    required = {
        "oida.app/Contents/Info.plist",
        "oida.app/Contents/MacOS/oida-macos",
    }
    missing = sorted(required - names)
    if missing:
        result.fail(f"unsigned archive is missing entries: {', '.join(missing)}")
    else:
        result.ok(f"unsigned archive contains app metadata and binary: {archive_path}")


def get_json(server: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{server}{path}", headers=request_headers(), method="GET")
    # main() normalizes and validates the HTTP(S) server URL before any probe.
    with urllib.request.urlopen(request, timeout=15) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        return json.loads(response.read())


def expect_http_status(server: str, path: str, expected: int, headers: dict[str, str] | None = None) -> bool:
    request = urllib.request.Request(f"{server}{path}", headers=request_headers(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            return response.status == expected
    except urllib.error.HTTPError as exc:
        return exc.code == expected
    except urllib.error.URLError:
        return False


def post_json(server: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{server}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers({"content-type": "application/json"}),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        return json.loads(response.read())


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "generation"


def is_loopback_server(server: str) -> bool:
    parsed = urllib.parse.urlparse(server)
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


def request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    token = os.getenv("OIDA_AUTH_TOKEN") or os.getenv("HMM_AUTH_TOKEN") or os.getenv("AEAR_AUTH_TOKEN")
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers


if __name__ == "__main__":
    raise SystemExit(main())
