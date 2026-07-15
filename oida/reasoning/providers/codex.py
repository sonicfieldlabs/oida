"""Codex app-server adapter using the user's existing host authentication."""

from __future__ import annotations

import json
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from oida import __version__ as OIDA_VERSION
from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRequest,
    ProviderResult,
)

from .base import (
    CommandRunner,
    MAX_CAPTURE_CHARS,
    ProviderTransportError,
    error_result,
    executable_path,
    host_environment,
    normalized_result,
    require_matching_provider,
    run_command,
    sanitize_error,
)


CodexTurnExecutor = Callable[..., Mapping[str, Any]]
CodexModelLister = Callable[..., list[Mapping[str, Any]]]

_DISABLED_CODEX_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "multi_agent",
    "plugins",
    "plugin_sharing",
    "shell_snapshot",
    "shell_tool",
    "skill_mcp_dependency_install",
    "tool_call_mcp_elicitation",
    "tool_suggest",
    "unified_exec",
    "workspace_dependencies",
)


class _AppServerProcess:
    """Minimal newline-delimited JSON-RPC client for one Codex subprocess."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        timeout: float,
    ) -> None:
        self._deadline = time.monotonic() + timeout
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._notifications: list[dict[str, Any]] = []
        self._stderr: list[str] = []
        self._next_id = 1
        try:
            self._process = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=cwd,
                env=dict(env),
                shell=False,
            )
        except OSError as exc:
            raise ProviderTransportError(f"Codex app-server could not start: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        self._messages.put(message)
                except json.JSONDecodeError as exc:
                    self._messages.put(exc)
                    return
            self._messages.put(
                ProviderTransportError("Codex app-server closed before completing the request")
            )
        except BaseException as exc:  # pragma: no cover - pipe/runtime failure
            self._messages.put(exc)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        count = 0
        for line in self._process.stderr:
            if count < MAX_CAPTURE_CHARS:
                self._stderr.append(line)
                count += len(line)

    def _send(self, message: Mapping[str, Any]) -> None:
        if self._process.stdin is None:
            raise ProviderTransportError("Codex app-server stdin is unavailable")
        try:
            self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProviderTransportError("Codex app-server closed its input") from exc

    def _next(self) -> dict[str, Any]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTransportError("Codex app-server request timed out")
        try:
            message = self._messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise ProviderTransportError("Codex app-server request timed out") from exc
        if isinstance(message, BaseException):
            raise ProviderTransportError(f"Invalid Codex app-server message: {message}")
        return message

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": dict(params)})
        while True:
            message = self._next()
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise ProviderTransportError(
                        f"Codex {method} failed: {sanitize_error(message['error'])}"
                    )
                return message.get("result")
            if "method" in message and "id" in message:
                # Oída never delegates approval/tool requests back to a host.
                self._send(
                    {
                        "id": message["id"],
                        "error": {"code": -32601, "message": "Oída host tools are disabled"},
                    }
                )
            elif "method" in message:
                self._notifications.append(message)

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send(message)

    def notification(self) -> dict[str, Any]:
        if self._notifications:
            return self._notifications.pop(0)
        while True:
            message = self._next()
            if "method" in message and "id" in message:
                self._send(
                    {
                        "id": message["id"],
                        "error": {"code": -32601, "message": "Oída host tools are disabled"},
                    }
                )
                continue
            if "method" in message:
                return message

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {"name": "oida", "title": "Oída", "version": OIDA_VERSION},
                "capabilities": {"experimentalApi": False, "requestAttestation": False},
            },
        )
        self.notify("initialized")

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)

    def __enter__(self) -> _AppServerProcess:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _execute_codex_turn(
    *,
    argv: Sequence[str],
    thread_params: Mapping[str, Any],
    turn_params: Mapping[str, Any],
    timeout: float,
    cwd: str,
    env: Mapping[str, str],
) -> Mapping[str, Any]:
    with _AppServerProcess(argv, cwd=cwd, env=env, timeout=timeout) as rpc:
        rpc.initialize()
        started = rpc.request("thread/start", thread_params)
        thread = started.get("thread", {}) if isinstance(started, dict) else {}
        thread_id = thread.get("id")
        if not isinstance(thread_id, str):
            raise ProviderTransportError("Codex thread/start returned no thread id")
        rpc.request("turn/start", {**dict(turn_params), "threadId": thread_id})
        final_text = ""
        duration_ms = None
        while True:
            notification = rpc.notification()
            method = notification.get("method")
            params = notification.get("params") if isinstance(notification.get("params"), dict) else {}
            if method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    final_text = item["text"]
            elif method == "turn/completed":
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                if turn.get("status") == "failed":
                    error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                    raise ProviderTransportError(error.get("message") or "Codex turn failed")
                duration_ms = turn.get("durationMs")
                if not final_text:
                    for item in reversed(turn.get("items", [])):
                        if isinstance(item, dict) and item.get("type") == "agentMessage":
                            final_text = str(item.get("text") or "")
                            break
                break
        if not final_text:
            raise ProviderTransportError("Codex returned no final agent message")
        return {"content": final_text, "duration_ms": duration_ms}


def _list_codex_models(
    *,
    argv: Sequence[str],
    timeout: float,
    cwd: str,
    env: Mapping[str, str],
) -> list[Mapping[str, Any]]:
    with _AppServerProcess(argv, cwd=cwd, env=env, timeout=timeout) as rpc:
        rpc.initialize()
        result = rpc.request("model/list", {"limit": 100, "includeHidden": False})
    return result.get("data", []) if isinstance(result, dict) else []


class CodexProvider:
    provider_id = "codex"

    def __init__(
        self,
        *,
        executable: str = "codex",
        runner: CommandRunner = run_command,
        turn_executor: CodexTurnExecutor = _execute_codex_turn,
        model_lister: CodexModelLister = _list_codex_models,
    ) -> None:
        self.executable = executable
        self._runner = runner
        self._turn_executor = turn_executor
        self._model_lister = model_lister

    def _argv(self, path: str) -> list[str]:
        disabled = [part for feature in _DISABLED_CODEX_FEATURES for part in ("--disable", feature)]
        return [
            path,
            *disabled,
            "-c",
            'model_reasoning_effort="high"',
            "-c",
            'web_search="disabled"',
            "-c",
            "mcp_servers={}",
            "-c",
            'shell_environment_policy.inherit="none"',
            "app-server",
            "--stdio",
        ]

    def probe(self) -> ProviderDescriptor:
        path = executable_path(self.executable)
        authenticated = False
        detail = "Codex CLI is not installed"
        if path:
            result = self._runner(
                [path, "-c", 'model_reasoning_effort="high"', "login", "status"],
                timeout=10,
                env=host_environment("CODEX_", "OPENAI_"),
            )
            # Current Codex releases write the human-readable login status to
            # stderr, while older releases used stdout.
            status_text = f"{result.stdout}\n{result.stderr}".lower()
            authenticated = result.returncode == 0 and "logged in" in status_text
            detail = (
                "Codex host authentication is available"
                if authenticated
                else "Codex CLI is installed but not authenticated"
            )
        return ProviderDescriptor(
            id=self.provider_id,
            name="Codex",
            kind="host_cli",
            locality="unknown",
            enabled=True,
            available=path is not None,
            authenticated=authenticated,
            capabilities=["host_subscription", "structured_output", "ephemeral", "read_only"],
            detail=detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        path = executable_path(self.executable)
        if not path:
            return []
        try:
            with tempfile.TemporaryDirectory(prefix="oida-codex-models-") as cwd:
                rows = self._model_lister(
                    argv=self._argv(path),
                    timeout=15,
                    cwd=cwd,
                    env=host_environment("CODEX_", "OPENAI_"),
                )
        except (ProviderTransportError, ValueError):
            return []
        output: list[ModelDescriptor] = []
        for row in rows:
            model_id = str(row.get("model") or row.get("id") or "")
            if not model_id:
                continue
            efforts = row.get("supportedReasoningEfforts")
            output.append(
                ModelDescriptor(
                    id=model_id,
                    provider_id=self.provider_id,
                    name=str(row.get("displayName") or model_id),
                    capabilities=["text", "structured_output", "reasoning"],
                    locality="unknown",
                    metadata={
                        "default": bool(row.get("isDefault")),
                        "reasoning_efforts": efforts if isinstance(efforts, list) else [],
                    },
                )
            )
        return output

    @staticmethod
    def thread_params(request: ProviderRequest, cwd: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "cwd": cwd,
            "developerInstructions": request.system_prompt,
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "personality": "none",
            "config": {
                "web_search": "disabled",
                "mcp_servers": {},
                "shell_environment_policy": {"inherit": "none"},
            },
        }
        if request.model_id:
            params["model"] = request.model_id
        return params

    @staticmethod
    def turn_params(request: ProviderRequest) -> dict[str, Any]:
        effort = str(request.metadata.get("reasoning_effort", "medium"))
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            effort = "medium"
        return {
            "input": [{"type": "text", "text": request.user_prompt}],
            "outputSchema": request.response_schema,
            "effort": effort,
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            "approvalPolicy": "never",
            "personality": "none",
        }

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        path = executable_path(self.executable)
        if not path:
            return error_result(request, "Codex CLI is not installed")
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="oida-codex-") as cwd:
                result = self._turn_executor(
                    argv=self._argv(path),
                    thread_params=self.thread_params(request, cwd),
                    turn_params=self.turn_params(request),
                    timeout=request.timeout_seconds,
                    cwd=cwd,
                    env=host_environment("CODEX_", "OPENAI_"),
                )
            content = result.get("content")
            if not isinstance(content, str):
                raise ProviderTransportError("Codex returned no content")
            duration = result.get("duration_ms")
            return normalized_result(
                request,
                content=content,
                latency_ms=(
                    float(duration)
                    if isinstance(duration, (float, int))
                    else (time.monotonic() - started) * 1000
                ),
                metadata={"isolation": "ephemeral_no_tools_read_only_no_network"},
            )
        except (ProviderTransportError, ValueError) as exc:
            return error_result(request, exc, latency_ms=(time.monotonic() - started) * 1000)
