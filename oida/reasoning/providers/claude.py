"""Claude Code host-subscription adapter."""

from __future__ import annotations

import json
import tempfile
import time

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


class ClaudeProvider:
    provider_id = "claude"

    def __init__(self, *, executable: str = "claude", runner: CommandRunner = run_command) -> None:
        self.executable = executable
        self._runner = runner

    def probe(self) -> ProviderDescriptor:
        path = executable_path(self.executable)
        available = path is not None
        authenticated = False
        detail = "Claude CLI is not installed"
        if path:
            result = self._runner(
                [path, "auth", "status", "--json"],
                timeout=10,
                env=host_environment("ANTHROPIC_", "CLAUDE_"),
            )
            detail = "Claude CLI is installed"
            if result.returncode == 0:
                try:
                    status = json.loads(result.stdout)
                    authenticated = bool(status.get("loggedIn"))
                    method = status.get("authMethod") or status.get("apiProvider")
                    detail = f"Authenticated ({method})" if authenticated and method else "Authentication available"
                except (json.JSONDecodeError, AttributeError):
                    detail = "Claude CLI auth status was not machine-readable"
        return ProviderDescriptor(
            id=self.provider_id,
            name="Claude Code",
            kind="host_cli",
            locality="unknown",
            enabled=True,
            available=available,
            authenticated=authenticated,
            capabilities=["host_subscription", "structured_output", "zero_tools", "ephemeral"],
            detail=detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        # Claude Code intentionally accepts aliases/full IDs but has no stable
        # machine-readable catalog command.  The selected model is validated on use.
        return []

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        path = executable_path(self.executable)
        if not path:
            return error_result(request, "Claude CLI is not installed")
        argv = [
            path,
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--safe-mode",
            "--tools",
            "",
            "--disable-slash-commands",
            "--no-chrome",
            "--system-prompt",
            request.system_prompt,
            "--json-schema",
            json.dumps(request.response_schema, separators=(",", ":"), ensure_ascii=False),
        ]
        if request.model_id:
            argv.extend(["--model", request.model_id])
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="oida-claude-") as cwd:
                result = self._runner(
                    argv,
                    input_text=request.user_prompt,
                    timeout=request.timeout_seconds,
                    cwd=cwd,
                    env=host_environment("ANTHROPIC_", "CLAUDE_"),
                )
            if result.returncode != 0:
                raise ProviderTransportError(result.stderr or f"Claude exited with {result.returncode}")
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                raise ProviderTransportError("Claude returned an invalid JSON envelope")
            structured = payload.get("structured_output")
            raw_content = payload.get("result")
            if structured is not None:
                content = json.dumps(structured, ensure_ascii=False)
            elif isinstance(raw_content, str):
                content = raw_content
            elif isinstance(raw_content, dict):
                structured = raw_content
                content = json.dumps(raw_content, ensure_ascii=False)
            else:
                raise ProviderTransportError("Claude returned no structured result")
            if payload.get("is_error"):
                raise ProviderTransportError(content)
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            return normalized_result(
                request,
                content=content,
                parsed=structured,
                usage=usage,
                latency_ms=result.latency_ms or (time.monotonic() - started) * 1000,
                metadata={"duration_ms": payload.get("duration_ms")},
            )
        except (ProviderTransportError, json.JSONDecodeError, ValueError) as exc:
            return error_result(request, exc, latency_ms=(time.monotonic() - started) * 1000)
