"""Hermes adapter for the Oída local listening gateway."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_HELP = """/oida — local agentic listening

  /oida status      gateway, engine, and stack status
  /oida start       ensure the singleton gateway is running
  /oida stop        stop a gateway managed by Oída
  /oida doctor      inspect local integrations and dependencies
  /oida open        open the listening agent/dashboard
  /oida library     open the Akousmata navigator

For audio work load oida:oida-listening. The Oída MCP server supplies the
listening, routing, follow-up, live, and sonic-memory tools.
"""


def _run(*args: str) -> str:
    executable = os.getenv("OIDA_COMMAND") or shutil.which("oida")
    prefix: list[str] = []
    runtime_path = _ROOT / "runtime.json"
    if not executable and runtime_path.exists():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            executable = str(runtime.get("command") or "")
            prefix = [str(item) for item in runtime.get("args_prefix") or []]
        except (OSError, json.JSONDecodeError):
            executable = None
    if not executable:
        return "Oída is not installed on PATH. Install the oida package, then run: oida integrate hermes"
    try:
        environment = os.environ.copy()
        environment.setdefault("OIDA_MOSS_PREWARM", "0")
        completed = subprocess.run(
            [executable, *prefix, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Oída command failed: {exc}"
    output = (completed.stdout or completed.stderr).strip()
    return output or f"Oída exited with status {completed.returncode}."


def _handle(raw_args: str) -> str:
    args = raw_args.strip().split()
    if not args or args[0] in {"help", "-h", "--help"}:
        return _HELP
    action = args[0].lower()
    if action in {"status", "doctor"}:
        return _run(action, "--json")
    if action == "start":
        return _run("start", "--json")
    if action == "stop":
        return _run("stop", "--json")
    if action == "open":
        return _run("agent")
    if action == "library":
        return _run("agent", "--library")
    return f"Unknown /oida action: {action}\n\n{_HELP}"


def register(ctx) -> None:
    ctx.register_command(
        "oida",
        handler=_handle,
        description="Control Oída and inspect its local listening gateway.",
        args_hint="[status|start|stop|doctor|open|library]",
    )
    ctx.register_skill(
        "oida-listening",
        _ROOT / "skills" / "oida-listening" / "SKILL.md",
        "Choose Oída or host perception and apply accountable sonic memory.",
    )
