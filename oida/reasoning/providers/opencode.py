"""Attached or Oída-managed OpenCode server adapter."""

from __future__ import annotations

import base64
import contextlib
import json
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Iterator
from typing import Any

from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRequest,
    ProviderResult,
)

from .base import (
    JsonTransport,
    ProviderTransportError,
    UrllibJsonTransport,
    endpoint_locality,
    error_result,
    executable_path,
    host_environment,
    join_url,
    normalized_result,
    require_matching_provider,
    sanitize_error,
    validate_http_url,
)


EndpointFactory = Callable[[float], contextlib.AbstractContextManager[str]]


_DISABLED_TOOLS = {
    name: False
    for name in (
        "bash",
        "read",
        "write",
        "edit",
        "patch",
        "glob",
        "grep",
        "list",
        "webfetch",
        "websearch",
        "codesearch",
        "task",
        "question",
        "todowrite",
        "todoread",
        "lsp",
        "skill",
        "external_directory",
    )
}


class OpenCodeProvider:
    provider_id = "opencode"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        managed: bool = False,
        executable: str = "opencode",
        password: str | None = None,
        password_getter: Callable[[], str | None] | None = None,
        username: str = "opencode",
        enabled: bool = True,
        transport: JsonTransport | None = None,
        endpoint_factory: EndpointFactory | None = None,
    ) -> None:
        if managed and base_url:
            raise ValueError("OpenCode can be managed or attached, not both")
        if not managed and not base_url:
            base_url = "http://127.0.0.1:4096"
        if base_url:
            validate_http_url(base_url)
            if endpoint_locality(base_url) != "local":
                raise ValueError("Attached OpenCode servers must use a loopback URL")
        self.base_url = base_url.rstrip("/") if base_url else None
        self.managed = managed
        self.executable = executable
        self.enabled = enabled
        self._password = password
        self._password_getter = password_getter
        self._username = username
        self._transport = transport or UrllibJsonTransport()
        self._endpoint_factory = endpoint_factory

    def _secret(self) -> str | None:
        return self._password_getter() if self._password_getter else self._password

    def _headers(self, secret: str | None) -> dict[str, str]:
        if not secret:
            return {}
        token = base64.b64encode(f"{self._username}:{secret}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def managed_argv(executable: str, port: int) -> list[str]:
        return [
            executable,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            "--pure",
        ]

    @contextlib.contextmanager
    def _managed_endpoint(self, timeout: float, password: str) -> Iterator[str]:
        path = executable_path(self.executable)
        if not path:
            raise ProviderTransportError("OpenCode CLI is not installed")
        port = self._free_port()
        base_url = f"http://127.0.0.1:{port}"
        if not password:
            raise ProviderTransportError("Managed OpenCode requires an ephemeral server password")
        extra = {
            "OPENCODE_SERVER_USERNAME": self._username,
            "OPENCODE_SERVER_PASSWORD": password,
        }
        with tempfile.TemporaryDirectory(prefix="oida-opencode-") as cwd:
            try:
                process = subprocess.Popen(
                    self.managed_argv(path, port),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=cwd,
                    env=host_environment(
                        "OPENCODE_",
                        "OPENAI_",
                        "ANTHROPIC_",
                        "OPENROUTER_",
                        "GOOGLE_",
                        "GEMINI_",
                        extra=extra,
                    ),
                    shell=False,
                )
            except OSError as exc:
                raise ProviderTransportError(f"OpenCode server could not start: {exc}") from exc
            deadline = time.monotonic() + min(timeout, 15.0)
            try:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise ProviderTransportError("OpenCode server exited during startup")
                    try:
                        self._transport.request(
                            "GET",
                            join_url(base_url, "/global/health"),
                            headers=self._headers(password),
                            timeout=0.5,
                        )
                        break
                    except ProviderTransportError:
                        time.sleep(0.05)
                else:
                    raise ProviderTransportError("OpenCode server did not become ready")
                yield base_url
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)

    @contextlib.contextmanager
    def _endpoint(
        self, timeout: float, attached_secret: str | None
    ) -> Iterator[tuple[str, str | None]]:
        if self._endpoint_factory is not None:
            with self._endpoint_factory(timeout) as endpoint:
                yield endpoint, attached_secret
            return
        if self.managed:
            # Never run an unauthenticated managed control server. The secret
            # exists only in this subprocess environment and request headers;
            # it is regenerated for every short-lived server process.
            managed_secret = secrets.token_urlsafe(32)
            with self._managed_endpoint(timeout, managed_secret) as endpoint:
                yield endpoint, managed_secret
            return
        assert self.base_url is not None
        yield self.base_url, attached_secret

    def probe(self) -> ProviderDescriptor:
        available = False
        authenticated: bool | None = None
        detail = "Disabled"
        if self.managed:
            available = executable_path(self.executable) is not None
            authenticated = True if available else None
            detail = (
                "OpenCode CLI is ready for an isolated managed server"
                if available
                else "OpenCode CLI is not installed"
            )
        elif self.base_url:
            secret = self._secret() if self.enabled else None
            try:
                self._transport.request(
                    "GET",
                    join_url(self.base_url, "/global/health"),
                    headers=self._headers(secret),
                    timeout=3,
                )
                available = True
                authenticated = True
                detail = "Attached OpenCode server is reachable" + (
                    " (disabled)" if not self.enabled else ""
                )
            except ProviderTransportError as exc:
                detail = sanitize_error(exc, secrets=[secret] if secret else [])
        return ProviderDescriptor(
            id=self.provider_id,
            name="OpenCode",
            kind="host_cli",
            # The control server is loopback-only, but its selected inference
            # provider may still be cloud-hosted.
            locality="unknown",
            enabled=self.enabled,
            available=available,
            authenticated=authenticated,
            capabilities=["models", "host_credentials", "ephemeral_session", "zero_tools"],
            detail=detail,
        )

    def list_models(self) -> list[ModelDescriptor]:
        if not self.enabled:
            return []
        secret = self._secret()
        try:
            with self._endpoint(20, secret) as (base_url, endpoint_secret):
                try:
                    # This returns only configured/connected providers and is
                    # intentionally much smaller than the multi-megabyte full
                    # public catalog returned by `/provider` in current builds.
                    response = self._transport.request(
                        "GET",
                        join_url(base_url, "/config/providers"),
                        headers=self._headers(endpoint_secret),
                        timeout=15,
                    )
                except ProviderTransportError:
                    # Compatibility with older OpenCode servers.
                    response = self._transport.request(
                        "GET",
                        join_url(base_url, "/provider"),
                        headers=self._headers(endpoint_secret),
                        timeout=15,
                    )
        except ProviderTransportError:
            return []
        payload = response.data if isinstance(response.data, dict) else {}
        connected = set(payload.get("connected", [])) if isinstance(payload.get("connected"), list) else set()
        provider_rows = payload.get("providers", payload.get("all", []))
        if not connected and isinstance(payload.get("providers"), list):
            connected = {
                str(provider.get("id"))
                for provider in payload["providers"]
                if isinstance(provider, dict) and provider.get("id")
            }
        result: list[ModelDescriptor] = []
        for provider in provider_rows if isinstance(provider_rows, list) else []:
            if not isinstance(provider, dict):
                continue
            provider_id = str(provider.get("id") or "")
            models = provider.get("models") if isinstance(provider.get("models"), dict) else {}
            for model_key, model in models.items():
                if not isinstance(model, dict):
                    continue
                model_id = f"{provider_id}/{model.get('id') or model_key}"
                limits = model.get("limit") if isinstance(model.get("limit"), dict) else {}
                capabilities = ["text"]
                model_capabilities = (
                    model.get("capabilities")
                    if isinstance(model.get("capabilities"), dict)
                    else {}
                )
                input_capabilities = (
                    model_capabilities.get("input")
                    if isinstance(model_capabilities.get("input"), dict)
                    else {}
                )
                if model.get("reasoning") or model_capabilities.get("reasoning"):
                    capabilities.append("reasoning")
                capabilities.extend(
                    modality
                    for modality in ("audio", "image", "video", "pdf")
                    if input_capabilities.get(modality)
                )
                result.append(
                    ModelDescriptor(
                        id=model_id,
                        provider_id=self.provider_id,
                        name=str(model.get("name") or model_id),
                        capabilities=capabilities,
                        locality="unknown",
                        context_window=(
                            limits["context"]
                            if isinstance(limits.get("context"), int) and limits["context"] > 0
                            else None
                        ),
                        metadata={"connected": provider_id in connected, "upstream_provider": provider_id},
                    )
                )
        return result

    @staticmethod
    def _model(model_id: str | None) -> dict[str, str] | None:
        if not model_id:
            return None
        if "/" not in model_id:
            raise ValueError("OpenCode model must use provider/model form")
        provider_id, upstream_model = model_id.split("/", 1)
        if not provider_id or not upstream_model:
            raise ValueError("OpenCode model must use provider/model form")
        return {"providerID": provider_id, "modelID": upstream_model}

    def complete(self, request: ProviderRequest) -> ProviderResult:
        require_matching_provider(request, self.provider_id)
        started = time.monotonic()
        session_id: str | None = None
        session_deleted: bool | None = None
        secret = self._secret()
        try:
            with tempfile.TemporaryDirectory(prefix="oida-opencode-request-") as request_cwd, self._endpoint(
                request.timeout_seconds, secret
            ) as (endpoint, endpoint_secret):
                headers = {
                    **self._headers(endpoint_secret),
                    "x-opencode-directory": urllib.parse.quote(request_cwd, safe=""),
                }
                created = self._transport.request(
                    "POST",
                    join_url(endpoint, "/session"),
                    payload={"title": "Oída ephemeral reasoning"},
                    headers=headers,
                    timeout=min(request.timeout_seconds, 15),
                )
                session = created.data if isinstance(created.data, dict) else {}
                session_id = session.get("id")
                if not isinstance(session_id, str):
                    raise ProviderTransportError("OpenCode did not create a session")
                try:
                    tool_response = self._transport.request(
                        "GET",
                        join_url(endpoint, "/experimental/tool/ids"),
                        headers=headers,
                        timeout=min(request.timeout_seconds, 10),
                    )
                    if not isinstance(tool_response.data, list) or not all(
                        isinstance(tool_id, str) for tool_id in tool_response.data
                    ):
                        raise ProviderTransportError(
                            "OpenCode did not expose its tool inventory; refusing to run"
                        )
                    disabled_tools = {
                        **_DISABLED_TOOLS,
                        **{tool_id: False for tool_id in tool_response.data},
                    }
                    schema = json.dumps(
                        request.response_schema, ensure_ascii=False, separators=(",", ":")
                    )
                    body: dict[str, Any] = {
                        "system": request.system_prompt,
                        "tools": disabled_tools,
                        "parts": [
                            {
                                "type": "text",
                                "text": (
                                    f"{request.user_prompt}\n\n"
                                    "Return only one JSON value satisfying this response schema; "
                                    f"do not use Markdown fences:\n{schema}"
                                ),
                            }
                        ],
                    }
                    if model := self._model(request.model_id):
                        body["model"] = model
                    response = self._transport.request(
                        "POST",
                        join_url(endpoint, f"/session/{urllib.parse.quote(session_id, safe='')}/message"),
                        payload=body,
                        headers=headers,
                        timeout=request.timeout_seconds,
                    )
                finally:
                    session_deleted = False
                    self._transport.request(
                        "DELETE",
                        join_url(endpoint, f"/session/{urllib.parse.quote(session_id, safe='')}"),
                        headers=headers,
                        timeout=min(request.timeout_seconds, 10),
                    )
                    session_deleted = True
            payload = response.data if isinstance(response.data, dict) else {}
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            if info.get("error"):
                raise ProviderTransportError(info["error"])
            parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
            content = "\n".join(
                part["text"]
                for part in parts
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            ).strip()
            if not content:
                raise ProviderTransportError("OpenCode returned no text response")
            tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
            return normalized_result(
                request,
                content=content,
                usage={
                    "input_tokens": tokens.get("input"),
                    "output_tokens": tokens.get("output"),
                },
                latency_ms=(time.monotonic() - started) * 1000,
                metadata={"session_deleted": session_deleted is True, "upstream_provider": info.get("providerID")},
            )
        except (ProviderTransportError, ValueError) as exc:
            # If deletion itself failed, do not silently claim an ephemeral run.
            return error_result(
                request,
                exc,
                latency_ms=(time.monotonic() - started) * 1000,
                secrets=[secret] if secret else [],
                metadata={"session_deleted": session_deleted},
            )
