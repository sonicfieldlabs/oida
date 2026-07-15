"""Hermes Agent host adapter with its customization/tool surface suppressed."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRequest,
    ProviderResult,
)

from .base import (
    CommandRunner,
    ProviderTransportError,
    error_result,
    executable_path,
    host_environment,
    normalized_result,
    require_matching_provider,
    run_command,
)


_NO_TOOLS_SENTINEL = "oida-no-tools"
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _clean_quiet_output(stdout: str) -> str:
    """Remove only Hermes' deterministic warning for our empty toolset.

    Hermes does not currently expose a native ``--tools ''`` switch.  An
    unresolved, explicitly supplied toolset produces an empty tool surface,
    but some releases print one Rich warning before the otherwise clean quiet
    response.  Accept that exact compatibility warning and reject every other
    kind of surrounding prose through the normal strict JSON parser.
    """

    lines = _ANSI_ESCAPE.sub("", stdout).splitlines()
    warning = f"Warning: Unknown toolsets: {_NO_TOOLS_SENTINEL}"
    return "\n".join(line for line in lines if line.strip() != warning).strip()


def _seed_ephemeral_credentials(home: Path) -> None:
    """Copy only Hermes' provider credential file, never its durable state."""

    configured = os.environ.get("HERMES_HOME")
    source_home = Path(configured).expanduser() if configured else Path.home() / ".hermes"
    source = source_home / ".env"
    if not source.is_file():
        return
    destination = home / ".env"
    shutil.copyfile(source, destination)
    destination.chmod(0o600)


class HermesProvider:
    provider_id = "hermes"

    def __init__(self, *, executable: str = "hermes", runner: CommandRunner = run_command) -> None:
        self.executable = executable
        self._runner = runner

    def probe(self) -> ProviderDescriptor:
        path = executable_path(self.executable)
        version = None
        if path:
            result = self._runner([path, "--version"], timeout=5, env=host_environment("HERMES_"))
            version = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else None
        detail = (
            f"{version}; isolated runs require an explicit model and use a no-tool compatibility sentinel"
            if version
            else (
                "Hermes CLI is installed; isolated runs require an explicit model "
                "and use a no-tool compatibility sentinel"
            )
            if path
            else "Hermes CLI is not installed"
        )
        return ProviderDescriptor(
            id=self.provider_id,
            name="Hermes Agent",
            kind="host_cli",
            locality="unknown",
            enabled=True,
            available=path is not None,
            authenticated=None,
            capabilities=["host_subscription", "text", "compat_zero_tools"],
            detail=detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        return []

    @staticmethod
    def _prompt(request: ProviderRequest) -> str:
        schema = json.dumps(request.response_schema, ensure_ascii=False, separators=(",", ":"))
        return (
            f"SYSTEM INSTRUCTIONS:\n{request.system_prompt}\n\n"
            f"USER REQUEST:\n{request.user_prompt}\n\n"
            "Return only one JSON value satisfying this response schema; do not use Markdown fences:\n"
            f"{schema}"
        )

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        path = executable_path(self.executable)
        if not path:
            return error_result(request, "Hermes CLI is not installed")
        if not request.model_id:
            return error_result(request, "Hermes isolated mode requires an explicit model")
        # Hermes currently has no documented `--tools ""` or no-persistence
        # switch.  `--toolsets` with an intentionally unresolved name causes
        # the current CLI to expose zero definitions; the one-turn/source flags
        # bound the remaining behavior.  This is advertised as compatibility,
        # not as a native security guarantee.
        argv = [
            path,
            "chat",
            "-Q",
            "-q",
            self._prompt(request),
            "--toolsets",
            _NO_TOOLS_SENTINEL,
            "--safe-mode",
            "--ignore-rules",
            "--max-turns",
            "1",
            "--source",
            "tool",
        ]
        if request.model_id:
            argv.extend(["--model", request.model_id])
        started = time.monotonic()
        isolated_state_deleted = False
        try:
            with tempfile.TemporaryDirectory(prefix="oida-hermes-home-") as hermes_home, tempfile.TemporaryDirectory(
                prefix="oida-hermes-cwd-"
            ) as cwd:
                _seed_ephemeral_credentials(Path(hermes_home))
                env = host_environment(
                    "HERMES_",
                    "OPENAI_",
                    "ANTHROPIC_",
                    "OPENROUTER_",
                    "GOOGLE_",
                    "GEMINI_",
                    "AWS_",
                    extra={
                        "HERMES_HOME": hermes_home,
                        "HERMES_IGNORE_USER_CONFIG": "1",
                        "HERMES_EPHEMERAL_SYSTEM_PROMPT": request.system_prompt,
                    },
                )
                result = self._runner(
                    argv,
                    timeout=request.timeout_seconds,
                    cwd=cwd,
                    env=env,
                )
                if result.returncode != 0:
                    raise ProviderTransportError(
                        result.stderr or f"Hermes exited with {result.returncode}"
                    )
                content = _clean_quiet_output(result.stdout)
                if not content:
                    raise ProviderTransportError("Hermes returned no final response")
            isolated_state_deleted = True
            return normalized_result(
                request,
                content=content,
                latency_ms=result.latency_ms or (time.monotonic() - started) * 1000,
                metadata={
                    "isolation": "native_temp_home",
                    "session_source": "tool",
                    "session_deleted": True,
                    "isolated_state_deleted": isolated_state_deleted,
                },
            )
        except (OSError, ProviderTransportError, ValueError) as exc:
            isolated_state_deleted = True
            return error_result(
                request,
                exc,
                latency_ms=(time.monotonic() - started) * 1000,
                metadata={
                    "isolation": "native_temp_home",
                    "isolated_state_deleted": isolated_state_deleted,
                },
            )
