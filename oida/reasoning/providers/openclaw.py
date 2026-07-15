"""OpenClaw's lean, one-shot model inference adapter."""

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


class OpenClawProvider:
    provider_id = "openclaw"

    def __init__(self, *, executable: str = "openclaw", runner: CommandRunner = run_command) -> None:
        self.executable = executable
        self._runner = runner

    def probe(self) -> ProviderDescriptor:
        path = executable_path(self.executable)
        return ProviderDescriptor(
            id=self.provider_id,
            name="OpenClaw",
            kind="host_cli",
            locality="unknown",
            enabled=True,
            available=path is not None,
            authenticated=None,
            capabilities=["host_credentials", "lean_inference", "zero_tools"],
            detail=(
                "OpenClaw infer is available (local route may still use a remote model)"
                if path
                else "OpenClaw CLI is not installed"
            ),
        )

    def list_models(self) -> list[ModelDescriptor]:
        path = executable_path(self.executable)
        if not path:
            return []
        result = self._runner(
            [path, "models", "list", "--json"],
            timeout=15,
            env=host_environment("OPENCLAW_"),
        )
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        items = payload.get("models", payload) if isinstance(payload, dict) else payload
        output: list[ModelDescriptor] = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                model_id, name = item, item
            elif isinstance(item, dict):
                model_id = str(item.get("key") or item.get("id") or item.get("model") or "")
                name = str(item.get("name") or model_id)
            else:
                continue
            if model_id:
                lowered = model_id.lower()
                is_cloud_ollama = (
                    lowered.startswith("ollama/")
                    and ":" in lowered
                    and lowered.endswith("cloud")
                )
                is_local = bool(item.get("local")) if isinstance(item, dict) else False
                input_mode = str(item.get("input") or "text") if isinstance(item, dict) else "text"
                output.append(
                    ModelDescriptor(
                        id=model_id,
                        provider_id=self.provider_id,
                        name=name,
                        capabilities=[part for part in ("text", "image") if part in input_mode],
                        locality="external" if is_cloud_ollama or not is_local else "local",
                        context_window=(
                            item.get("contextWindow")
                            if isinstance(item, dict) and isinstance(item.get("contextWindow"), int)
                            else None
                        ),
                        metadata={
                            "available": item.get("available"),
                            "tags": item.get("tags", []),
                        }
                        if isinstance(item, dict)
                        else {},
                    )
                )
        return output

    @staticmethod
    def _prompt(request: ProviderRequest) -> str:
        schema = json.dumps(request.response_schema, ensure_ascii=False, separators=(",", ":"))
        return (
            f"{request.system_prompt}\n\n"
            f"USER REQUEST:\n{request.user_prompt}\n\n"
            "Return only one JSON value satisfying this schema:\n"
            f"{schema}"
        )

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        path = executable_path(self.executable)
        if not path:
            return error_result(request, "OpenClaw CLI is not installed")
        if not request.model_id:
            return error_result(request, "OpenClaw requires an explicit provider/model")
        # `infer model run --local` is a direct provider completion, not an
        # OpenClaw agent/session.  The CLI currently accepts prompt text only as
        # an argument, so it can be briefly visible to same-user process tools.
        argv = [
            path,
            "infer",
            "model",
            "run",
            "--local",
            "--model",
            request.model_id,
            "--prompt",
            self._prompt(request),
            "--json",
        ]
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="oida-openclaw-") as cwd:
                result = self._runner(
                    argv,
                    timeout=request.timeout_seconds,
                    cwd=cwd,
                    env=host_environment(
                        "OPENCLAW_", "OPENAI_", "ANTHROPIC_", "OPENROUTER_", "GOOGLE_", "GEMINI_"
                    ),
                )
            if result.returncode != 0:
                raise ProviderTransportError(result.stderr or f"OpenClaw exited with {result.returncode}")
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict) or payload.get("ok") is False:
                raise ProviderTransportError("OpenClaw inference failed")
            outputs = payload.get("outputs") if isinstance(payload.get("outputs"), list) else []
            content = "\n".join(
                str(item["text"])
                for item in outputs
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ).strip()
            if not content and isinstance(payload.get("result"), dict):
                result_payload = payload["result"]
                content = str(result_payload.get("text") or result_payload.get("content") or "").strip()
            if not content and isinstance(payload.get("result"), str):
                content = payload["result"].strip()
            if not content:
                content = str(payload.get("text") or payload.get("content") or "").strip()
            if not content:
                raise ProviderTransportError("OpenClaw returned no text output")
            return normalized_result(
                request,
                content=content,
                latency_ms=result.latency_ms or (time.monotonic() - started) * 1000,
                metadata={
                    "transport": payload.get("transport"),
                    "upstream_provider": payload.get("provider"),
                },
            )
        except (ProviderTransportError, json.JSONDecodeError, ValueError) as exc:
            return error_result(request, exc, latency_ms=(time.monotonic() - started) * 1000)
