"""Loader for the upstream AKOÚŌ machine-readable contract (v0.6).

AKOÚŌ v0.6 publishes its skills, command chains, evidence ladder, permission
overrides, and presets as data (``akouo.manifest.json`` + ``presets/presets.json``).
This module loads them from the configured akouo root and provides a drift check
so oída's hardcoded fallback tables (kept for offline robustness) are tested
against the published contract instead of silently diverging from it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.akouo.loader import default_akouo_root
from harness.akouo.routing import COMMAND_ROUTES, claim_permissions_for
from harness.types import LISTENING_MODES


def manifest_path(root: str | Path | None = None) -> Path:
    base = Path(root).expanduser() if root else default_akouo_root()
    return base / "akouo.manifest.json"


def load_manifest(root: str | Path | None = None) -> dict[str, Any] | None:
    """The upstream contract, or None when the akouo checkout predates v0.6."""
    path = manifest_path(root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_presets(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Upstream portable presets ([] when unavailable)."""
    manifest = load_manifest(root)
    if not manifest:
        return []
    base = Path(root).expanduser() if root else default_akouo_root()
    presets_file = base / str(manifest.get("presets_file") or "presets/presets.json")
    if not presets_file.exists():
        return []
    data = json.loads(presets_file.read_text(encoding="utf-8"))
    presets = data.get("presets")
    return presets if isinstance(presets, list) else []


def drift_errors(root: str | Path | None = None) -> list[str] | None:
    """Compare oída's hardcoded harness tables against the published manifest.

    Returns None when no manifest is available, else a list of drift errors
    (empty = in sync). Used by tests so the fallback tables cannot silently
    diverge from the upstream contract.
    """
    manifest = load_manifest(root)
    if manifest is None:
        return None

    errors: list[str] = []

    contract = str(manifest.get("contract") or "")
    version = str(manifest.get("akouo_version") or "")
    if version and not contract.endswith(f"v{version}"):
        errors.append(f"manifest contract {contract!r} does not match akouo_version {version!r}")

    manifest_modes = {
        skill["id"] for skill in manifest.get("skills", []) if skill.get("kind") == "mode"
    }
    for mode in LISTENING_MODES:
        if mode not in manifest_modes:
            errors.append(f"harness mode {mode} missing from upstream manifest")
    for mode in manifest_modes:
        if mode not in LISTENING_MODES:
            errors.append(f"upstream mode {mode} missing from harness LISTENING_MODES")

    manifest_commands = {cmd["name"] for cmd in manifest.get("commands", [])}
    for command in COMMAND_ROUTES:
        if command not in manifest_commands:
            errors.append(f"harness command {command} missing from upstream manifest")
    for command in manifest_commands:
        if command not in COMMAND_ROUTES:
            errors.append(f"upstream command {command} missing from harness COMMAND_ROUTES")

    # evidence ladder: harness permissions must match the manifest row by row
    for rung in manifest.get("evidence_ladder", []):
        level = str(rung.get("level"))
        ours = claim_permissions_for(level, "/listen")
        for key in ("heard_allowed", "measured_allowed", "inferred_allowed", "interpreted_allowed", "speculative_allowed", "must_include_undetermined"):
            theirs = bool(rung.get(key))
            if key == "measured_allowed" and level == "mixed":
                # the manifest marks mixed as measured-capable; oída additionally
                # requires an actually-measured component at run time
                continue
            if bool(ours.get(key)) != theirs:
                errors.append(f"evidence ladder drift at {level}.{key}: harness={ours.get(key)} manifest={theirs}")

    # command permission overrides
    overrides = manifest.get("command_permission_overrides", {})
    forensic = claim_permissions_for("mixed", "/forensic")
    if "/forensic" in overrides:
        if forensic.get("interpreted_allowed") is not False or forensic.get("speculative_allowed") is not False:
            errors.append("/forensic override drift: harness does not suppress interpreted/speculative")
    fiction = claim_permissions_for("mixed", "/fiction")
    if "/fiction" in overrides and fiction.get("speculative_allowed") is not True:
        errors.append("/fiction override drift: harness does not grant speculative")

    return errors
