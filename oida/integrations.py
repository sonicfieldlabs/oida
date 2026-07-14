"""Install the local Oída adapters shipped inside the Oída distribution."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from oida.config import REPO_ROOT, data_dir, integration_settings_path
from oida.storage import write_json_atomic

TARGETS = ("hermes", "codex", "claude", "remote")


def assets_root() -> Path:
    packaged = Path(__file__).resolve().parent / "integration_assets"
    if packaged.exists():
        return packaged
    return REPO_ROOT / "integrations"


def install(target: str, *, serve: bool = False, https_port: int = 8443) -> dict[str, Any]:
    target = target.strip().lower()
    if target == "all":
        return {
            "target": "all",
            "results": [install(name, serve=serve if name == "remote" else False, https_port=https_port) for name in TARGETS],
        }
    if target == "hermes":
        return _install_hermes()
    if target == "codex":
        return _install_codex()
    if target == "claude":
        return _install_claude()
    if target == "remote":
        return _install_remote(serve=serve, https_port=https_port)
    raise ValueError(f"unknown integration {target!r}; choose {', '.join(TARGETS)} or all")


def inspect_integrations() -> dict[str, Any]:
    hermes_home = _hermes_home()
    settings = _load_integration_settings()
    return {
        "hermes": {
            "available": bool(shutil.which("hermes")),
            "plugin": str(hermes_home / "plugins" / "oida"),
            "installed": (hermes_home / "plugins" / "oida" / "plugin.yaml").exists(),
        },
        "codex": {"available": bool(shutil.which("codex"))},
        "claude": {"available": bool(shutil.which("claude"))},
        "remote": remote_status(settings=settings),
    }


def remote_status(*, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Describe the secure phone remote without changing system state.

    A raw ``http://192.168…`` URL is not useful for this feature: mobile
    browsers expose microphone capture only in a secure context.  Oída keeps
    the daemon loopback-bound and uses private-network Serve as the private HTTPS
    boundary instead.
    """
    loaded = settings if isinstance(settings, dict) else _load_integration_settings()
    configured = loaded.get("remote") if isinstance(loaded.get("remote"), dict) else {}
    executable = shutil.which("private-network")
    https_port = _remote_https_port(configured.get("https_port"))
    if not executable:
        return {
            "available": False,
            "configured": False,
            "enabled": False,
            "served": False,
            "secure": False,
            "microphone_ready": False,
            "https_port": https_port,
            "detail": "private-network is required for a private HTTPS phone microphone URL.",
        }

    status_result = _run([executable, "status", "--json"])
    status_payload: dict[str, Any] = {}
    if status_result["ok"]:
        try:
            parsed = json.loads(status_result["output"])
            if isinstance(parsed, dict):
                status_payload = parsed
        except json.JSONDecodeError:
            pass
    self_status = status_payload.get("Self") if isinstance(status_payload.get("Self"), dict) else {}
    dns_name = str(self_status.get("DNSName") or "").rstrip(".") or None
    running = status_payload.get("BackendState") == "Running" and bool(dns_name)
    base_url = f"https://{dns_name}:{https_port}/" if dns_name else None
    remote_ear_url = f"{base_url}remote" if base_url else None

    serve_result = _run([executable, "serve", "status", "--json"]) if running else None
    served = _private-network_serves_oida(serve_result, dns_name=dns_name, https_port=https_port)
    saved_enabled = bool(configured.get("enabled"))
    enabled = bool(running and served)
    secure = bool(remote_ear_url and remote_ear_url.startswith("https://"))
    if not running:
        detail = "private-network is installed but is not connected."
    elif not served:
        detail = "Secure phone access is ready to be enabled."
    else:
        detail = "Phone microphone access is available through private private-network HTTPS."
    return {
        "available": bool(running),
        "configured": saved_enabled,
        "enabled": enabled,
        "served": served,
        "secure": secure,
        "microphone_ready": bool(enabled and secure),
        "mode": "private-network-serve",
        "private-network_host": dns_name,
        "https_port": https_port,
        "url": base_url,
        "remote_ear_url": remote_ear_url,
        "library_url": f"{base_url}library/" if base_url else None,
        "detail": detail,
    }


def _remote_https_port(value: object) -> int:
    try:
        port = int(value or 8443)
    except (TypeError, ValueError):
        return 8443
    return port if 1 <= port <= 65535 else 8443


def _private-network_serves_oida(
    result: dict[str, Any] | None,
    *,
    dns_name: str | None,
    https_port: int,
) -> bool:
    if not result or not result.get("ok") or not dns_name:
        return False
    try:
        payload = json.loads(str(result.get("output") or ""))
    except json.JSONDecodeError:
        return False
    web = payload.get("Web") if isinstance(payload, dict) and isinstance(payload.get("Web"), dict) else {}
    host = web.get(f"{dns_name}:{https_port}") if isinstance(web, dict) else None
    handlers = host.get("Handlers") if isinstance(host, dict) and isinstance(host.get("Handlers"), dict) else {}
    root = handlers.get("/") if isinstance(handlers, dict) else None
    proxy = str(root.get("Proxy") or "") if isinstance(root, dict) else ""
    return proxy.rstrip("/") == "http://127.0.0.1:8765"


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


def _install_remote(*, serve: bool, https_port: int) -> dict[str, Any]:
    if https_port < 1 or https_port > 65535:
        raise ValueError("https_port must be between 1 and 65535")
    private-network = shutil.which("private-network")
    if not private-network:
        return {"target": "remote", "configured": False, "detail": "private-network executable not found"}
    status = _run([private-network, "status", "--json"])
    dns_name = None
    if status["ok"]:
        try:
            payload = json.loads(status["output"])
            dns_name = str((payload.get("Self") or {}).get("DNSName") or "").rstrip(".") or None
        except json.JSONDecodeError:
            pass
    if not dns_name:
        return {"target": "remote", "configured": False, "detail": "could not determine this machine's private-network DNS name", "status": status}
    settings = _load_integration_settings()
    remote = settings.get("remote") if isinstance(settings.get("remote"), dict) else {}
    trusted = remote.get("trusted_hosts") if isinstance(remote.get("trusted_hosts"), list) else []
    trusted = list(dict.fromkeys([*trusted, dns_name]))
    remote.update(
        {
            "enabled": True,
            "mode": "private-network-serve",
            "trusted_hosts": trusted,
            "https_port": https_port,
            "url": f"https://{dns_name}:{https_port}/",
            "library_url": f"https://{dns_name}:{https_port}/library/",
            "remote_ear_url": f"https://{dns_name}:{https_port}/remote",
        }
    )
    settings["remote"] = remote
    integration_settings_path().parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(integration_settings_path(), settings)
    serve_result = None
    if serve:
        serve_result = _run(
            [
                private-network,
                "serve",
                "--bg",
                "--yes",
                "--https",
                str(https_port),
                "http://127.0.0.1:8765",
            ]
        )
    return {
        "target": "remote",
        "configured": True,
        "url": remote["url"],
        "library_url": remote["library_url"],
        "remote_ear_url": remote["remote_ear_url"],
        "serve": serve_result,
        "restart_required": True,
        "security": "Oída remains loopback-bound; private-network ACLs are the remote authorization boundary.",
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


def _load_integration_settings() -> dict[str, Any]:
    path = integration_settings_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
