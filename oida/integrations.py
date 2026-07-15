"""Install the local Oída adapters shipped inside the Oída distribution."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from oida.config import REPO_ROOT, data_dir
from oida.storage import write_json_atomic

TARGETS = ("hermes", "codex", "claude", "openclaw", "opencode")


def assets_root() -> Path:
    packaged = Path(__file__).resolve().parent / "integration_assets"
    if packaged.exists():
        return packaged
    return REPO_ROOT / "integrations"


def install(target: str) -> dict[str, Any]:
    target = target.strip().lower()
    if target == "all":
        return {
            "target": "all",
            "results": [install(name) for name in TARGETS],
        }
    if target == "hermes":
        return _install_hermes()
    if target == "codex":
        return _install_codex()
    if target == "claude":
        return _install_claude()
    if target == "openclaw":
        return _install_openclaw()
    if target == "opencode":
        return _install_opencode()
    raise ValueError(f"unknown integration {target!r}; choose {', '.join(TARGETS)} or all")


def inspect_integrations() -> dict[str, Any]:
    hermes_home = _hermes_home()
    return {
        "hermes": {
            "available": bool(shutil.which("hermes")),
            "plugin": str(hermes_home / "plugins" / "oida"),
            "installed": (hermes_home / "plugins" / "oida" / "plugin.yaml").exists(),
        },
        "codex": {"available": bool(shutil.which("codex"))},
        "claude": {"available": bool(shutil.which("claude"))},
        "openclaw": {"available": bool(shutil.which("openclaw"))},
        "opencode": {
            "available": bool(shutil.which("opencode")),
            "config": str(_opencode_config_path()),
            "installed": _opencode_installed(),
        },
    }


def _install_hermes() -> dict[str, Any]:
    executable = shutil.which("hermes")
    if not executable:
        return {"target": "hermes", "installed": False, "detail": "hermes executable not found"}
    source = assets_root() / "hermes"
    home = _hermes_home()
    destination = home / "plugins" / "oida"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    write_json_atomic(
        destination / "runtime.json",
        {"command": sys.executable, "args_prefix": ["-m", "oida.cli"]},
    )
    enable = _run([executable, "plugins", "enable", "oida", "--no-allow-tool-override"])
    # Add is discovery-first and idempotent enough for installers: a duplicate
    # reports non-zero but leaves the existing server untouched.
    mcp = _run(
        [
            executable,
            "mcp",
            "add",
            "oida",
            "--command",
            sys.executable,
            "--connect-timeout",
            "45",
            "--env",
            "OIDA_MCP_ENSURE_DAEMON=1",
            "OIDA_MOSS_PREWARM=0",
            "--args",
            "-m",
            "oida.cli",
            "gateway",
            "--stdio",
            "--ensure-daemon",
        ],
        input_text="y\n",
    )
    return {
        "target": "hermes",
        "installed": destination.exists(),
        "plugin": str(destination),
        "skill": str(destination / "skills" / "oida-listening"),
        "enable": enable,
        "mcp": mcp,
        "runtime": {"command": sys.executable, "module": "oida.cli"},
        "restart_required": True,
        "memory_policy": "Earworm/Akousmata is a sonic-memory sidecar; the selected Hermes memory provider is unchanged.",
    }


def _install_codex() -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        return {"target": "codex", "installed": False, "detail": "codex executable not found"}
    marketplace = _stage_marketplace("codex")
    validate_script = Path.home() / ".codex" / "skills" / ".system" / "plugin-creator" / "scripts" / "validate_plugin.py"
    validation = None
    if validate_script.exists():
        validation = _run([os.sys.executable, str(validate_script), str(marketplace / "plugins" / "oida")])
    compatibility = ["-c", 'model_reasoning_effort="xhigh"']
    added_marketplace = _run(
        [executable, *compatibility, "plugin", "marketplace", "add", str(marketplace)]
    )
    added_plugin = _run(
        [executable, *compatibility, "plugin", "add", "oida@oida-local"]
    )
    return {
        "target": "codex",
        "installed": added_plugin["ok"] or "already" in added_plugin["output"].lower(),
        "marketplace": str(marketplace),
        "validation": validation,
        "marketplace_add": added_marketplace,
        "plugin_add": added_plugin,
        "config_compatibility": "installer command maps deprecated reasoning effort 'max' to 'xhigh' without editing user config",
        "restart_required": True,
    }


def _install_claude() -> dict[str, Any]:
    executable = shutil.which("claude")
    if not executable:
        return {"target": "claude", "installed": False, "detail": "claude executable not found"}
    marketplace = _stage_marketplace("claude")
    plugin = marketplace / "plugins" / "oida"
    validation = _run([executable, "plugin", "validate", str(plugin)])
    added_marketplace = _run([executable, "plugin", "marketplace", "add", "--scope", "user", str(marketplace)])
    added_plugin = _run([executable, "plugin", "install", "--scope", "user", "oida@oida-local"])
    return {
        "target": "claude",
        "installed": added_plugin["ok"] or "already" in added_plugin["output"].lower(),
        "marketplace": str(marketplace),
        "validation": validation,
        "marketplace_add": added_marketplace,
        "plugin_add": added_plugin,
        "restart_required": True,
    }


def _install_openclaw() -> dict[str, Any]:
    """Install the Claude-compatible Oída bundle into OpenClaw.

    OpenClaw owns its provider credentials and model selection.  This bundle
    contributes only Oída's skill and local MCP gateway; it never copies or
    reads OpenClaw authentication state.
    """
    executable = shutil.which("openclaw")
    if not executable:
        return {"target": "openclaw", "installed": False, "detail": "openclaw executable not found"}
    marketplace = _stage_marketplace("openclaw")
    listed = _run([executable, "plugins", "marketplace", "list", str(marketplace)])
    installed = _run([executable, "plugins", "install", "--marketplace", str(marketplace), "oida"])
    return {
        "target": "openclaw",
        "installed": installed["ok"] or "already" in installed["output"].lower(),
        "marketplace": str(marketplace),
        "marketplace_list": listed,
        "plugin_install": installed,
        "restart_required": True,
        "auth_policy": "OpenClaw keeps ownership of provider credentials; Oída does not read them.",
    }


def _opencode_config_path() -> Path:
    explicit = os.getenv("OPENCODE_CONFIG")
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = Path(os.getenv("OPENCODE_CONFIG_DIR", Path.home() / ".config" / "opencode")).expanduser()
    jsonc = root / "opencode.jsonc"
    json_path = root / "opencode.json"
    if jsonc.exists() and not json_path.exists():
        return jsonc
    return json_path


def _opencode_installed() -> bool:
    path = _opencode_config_path()
    if not path.exists() or path.suffix.lower() == ".jsonc":
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    mcp = value.get("mcp") if isinstance(value, dict) else None
    return isinstance(mcp, dict) and isinstance(mcp.get("oida"), dict)


def _install_opencode() -> dict[str, Any]:
    """Install a global Oída skill and a pinned local stdio MCP entry.

    OpenCode supports JSONC, but rewriting a user's commented configuration
    would destroy comments.  In that case the installer stops with an explicit
    instruction instead of guessing or replacing the file.
    """
    executable = shutil.which("opencode")
    if not executable:
        return {"target": "opencode", "installed": False, "detail": "opencode executable not found"}
    config_path = _opencode_config_path()
    if config_path.suffix.lower() == ".jsonc" and config_path.exists():
        return {
            "target": "opencode",
            "installed": False,
            "config": str(config_path),
            "detail": "OpenCode uses a commented opencode.jsonc; add Oída through `opencode mcp add oida` or convert it to JSON before retrying so comments are not destroyed.",
        }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {"target": "opencode", "installed": False, "config": str(config_path), "detail": f"invalid JSON: {exc}"}
        if not isinstance(loaded, dict):
            return {"target": "opencode", "installed": False, "config": str(config_path), "detail": "OpenCode config root must be an object"}
        config = loaded
        backup = config_path.with_suffix(config_path.suffix + ".oida.bak")
        shutil.copy2(config_path, backup)
    else:
        backup = None
    config.setdefault("$schema", "https://opencode.ai/config.json")
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    mcp["oida"] = {
        "type": "local",
        "command": [sys.executable, "-m", "oida.cli", "gateway", "--stdio", "--ensure-daemon"],
        "environment": {"OIDA_MCP_ENSURE_DAEMON": "1", "OIDA_MOSS_PREWARM": "0"},
        "enabled": True,
        "timeout": 45000,
    }
    config["mcp"] = mcp
    write_json_atomic(config_path, config)

    source_skill = assets_root() / "opencode" / "skills" / "oida-listening"
    skill_root = Path(os.getenv("OPENCODE_CONFIG_DIR", config_path.parent)) / "skills" / "oida-listening"
    skill_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_skill, skill_root, dirs_exist_ok=True)
    status = _run([executable, "mcp", "list"], timeout=60)
    return {
        "target": "opencode",
        "installed": True,
        "config": str(config_path),
        "backup": str(backup) if backup else None,
        "skill": str(skill_root),
        "mcp_status": status,
        "restart_required": True,
        "auth_policy": "OpenCode keeps ownership of provider credentials; Oída does not read them.",
    }


def _hermes_home() -> Path:
    explicit = os.getenv("HERMES_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path.home() / ".hermes"
    active_file = base / "active_profile"
    if active_file.exists():
        active = active_file.read_text(encoding="utf-8").strip()
        candidate = base / "profiles" / active
        if active and active != "default" and candidate.exists():
            return candidate
    return base


def _stage_marketplace(target: str) -> Path:
    """Copy a host marketplace and pin its MCP command to this Oída runtime."""
    source = assets_root() / target
    destination = data_dir() / "integrations" / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    config_path = destination / "plugins" / "oida" / ".mcp.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    servers = config.get("mcpServers") if target == "codex" else config
    server = servers.get("oida") if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise ValueError(f"{target} Oída plugin has no oida MCP server")
    server["command"] = sys.executable
    server["args"] = [
        "-m",
        "oida.cli",
        "gateway",
        "--stdio",
        "--ensure-daemon",
    ]
    environment = server.get("env") if isinstance(server.get("env"), dict) else {}
    environment.update({"OIDA_MCP_ENSURE_DAEMON": "1", "OIDA_MOSS_PREWARM": "0"})
    server["env"] = environment
    write_json_atomic(config_path, config)
    return destination


def _run(
    command: list[str],
    *,
    timeout: int = 120,
    input_text: str | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            input=input_text,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "status": None, "output": str(exc), "command": command}
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return {"ok": completed.returncode == 0, "status": completed.returncode, "output": output, "command": command}
