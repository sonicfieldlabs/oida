"""Shared contracts and hardened I/O helpers for reasoning providers.

Provider adapters are intentionally single-turn.  They receive only the prompt
packet composed by Oída, make one upstream request, and return one normalized
``ProviderResult``.  They do not own fallback, conversation history, or policy.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from jsonschema import SchemaError, ValidationError, validate

from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRequest,
    ProviderResult,
)


MAX_CAPTURE_CHARS = 1_000_000
MAX_ERROR_CHARS = 4_000


class ProviderAdapter(Protocol):
    """Common surface implemented by every reasoning provider."""

    provider_id: str

    def probe(self) -> ProviderDescriptor: ...

    def list_models(self) -> list[ModelDescriptor]: ...

    def complete(self, request: ProviderRequest) -> ProviderResult: ...


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    latency_ms: float | None = None


CommandRunner = Callable[..., CommandResult]


@dataclass(frozen=True)
class JsonResponse:
    status: int
    data: Any
    headers: Mapping[str, str]


class JsonTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> JsonResponse: ...


class ProviderTransportError(RuntimeError):
    """An expected subprocess or HTTP transport failure."""


class UrllibJsonTransport:
    """Small stdlib JSON transport; keeps runtime dependencies unchanged."""

    def __init__(self, *, max_response_bytes: int = MAX_CAPTURE_CHARS) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")
        self.max_response_bytes = int(max_response_bytes)

    def request(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> JsonResponse:
        validate_http_url(url, allow_query=True)
        body = None
        merged = {"Accept": "application/json", **dict(headers or {})}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=body, headers=merged, method=method)
        try:
            handlers: list[Any] = [_NoRedirectHandler()]
            if endpoint_locality(url) == "local":
                handlers.insert(0, urllib.request.ProxyHandler({}))
            opener = urllib.request.build_opener(*handlers)
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise ProviderTransportError("Provider response exceeded the capture limit")
                content_type = str(response.headers.get("Content-Type") or "").lower()
                text = raw.decode("utf-8") if raw else ""
                data = (
                    _aggregate_openai_sse(text)
                    if "text/event-stream" in content_type or text.lstrip().startswith("data:")
                    else json.loads(text) if text else None
                )
                return JsonResponse(
                    status=int(response.status),
                    data=data,
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_ERROR_CHARS).decode("utf-8", errors="replace")
            raise ProviderTransportError(
                f"HTTP {exc.code}: {sanitize_error(raw)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderTransportError(f"HTTP connection failed: {exc.reason}") from exc
        except (TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderTransportError(f"Invalid or timed-out provider response: {exc}") from exc


def _aggregate_openai_sse(value: str) -> dict[str, Any]:
    """Collapse OpenAI-compatible SSE deltas into the normal completion shape."""

    content: list[str] = []
    response_id: Any = None
    model: Any = None
    finish_reason: Any = None
    usage: dict[str, Any] = {}
    saw_event = False
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        raw_data = line[5:].strip()
        if not raw_data or raw_data == "[DONE]":
            continue
        event = json.loads(raw_data)
        if not isinstance(event, dict):
            continue
        saw_event = True
        response_id = event.get("id") or response_id
        model = event.get("model") or model
        if isinstance(event.get("usage"), dict):
            usage = dict(event["usage"])
        choices = event.get("choices") if isinstance(event.get("choices"), list) else []
        if not choices or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        finish_reason = choice.get("finish_reason") or finish_reason
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        piece = delta.get("content")
        if isinstance(piece, str):
            content.append(piece)
        elif isinstance(piece, list):
            for item in piece:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    content.append(item["text"])
    if not saw_event:
        raise json.JSONDecodeError("no SSE data events", value, 0)
    return {
        "id": response_id,
        "model": model,
        "choices": [
            {
                "message": {"role": "assistant", "content": "".join(content)},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }


def run_command(
    argv: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: float = 60.0,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run an argv-only command with bounded returned output and no shell."""

    if not argv or not str(argv[0]).strip():
        raise ValueError("Command argv must contain an executable")
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [str(part) for part in argv],
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            shell=False,
        )
    except OSError as exc:
        raise ProviderTransportError(f"Provider command could not start: {exc}") from exc

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def drain(stream: Any, chunks: list[str]) -> None:
        captured = 0
        try:
            while part := stream.read(8192):
                if captured < MAX_CAPTURE_CHARS:
                    keep = part[: MAX_CAPTURE_CHARS - captured]
                    chunks.append(keep)
                    captured += len(keep)
        finally:
            stream.close()

    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr_chunks), daemon=True),
    ]
    for reader in readers:
        reader.start()

    if input_text is not None and process.stdin is not None:
        def write_input() -> None:
            try:
                process.stdin.write(input_text)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        threading.Thread(target=write_input, daemon=True).start()

    try:
        returncode = process.wait(timeout=max(0.1, float(timeout)))
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        raise ProviderTransportError(f"Provider command timed out after {timeout:g}s") from exc
    finally:
        for reader in readers:
            reader.join(timeout=2)
    return CommandResult(
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        latency_ms=(time.monotonic() - started) * 1000,
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Provider requests must not forward prompts or credentials to another origin."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        return None


_BASE_ENV_KEYS = {
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
}


def host_environment(*prefixes: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a small environment containing host auth only for this adapter."""

    normalized = tuple(prefix.upper() for prefix in prefixes)
    result = {
        key: value
        for key, value in os.environ.items()
        if key in _BASE_ENV_KEYS or key.upper().startswith(normalized)
    }
    if extra:
        result.update({str(key): str(value) for key, value in extra.items()})
    return result


def executable_path(configured: str) -> str | None:
    if os.path.isabs(configured):
        return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
    return shutil.which(configured)


def validate_http_url(url: str, *, allow_query: bool = False) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provider URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Provider credentials must not be embedded in the URL")
    if (parsed.query and not allow_query) or parsed.fragment:
        raise ValueError("Provider base URLs must not contain a query or fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Provider URL contains an invalid port") from exc
    host = parsed.hostname.lower().rstrip(".")
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if parsed.scheme == "http" and not loopback:
        raise ValueError("Plain HTTP provider URLs are allowed only on a loopback host")
    return parsed


def endpoint_locality(url: str) -> str:
    parsed = validate_http_url(url, allow_query=True)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "localhost":
        return "local"
    try:
        return "local" if ipaddress.ip_address(host).is_loopback else "external"
    except ValueError:
        return "external"


def join_url(base_url: str, path: str) -> str:
    validate_http_url(base_url)
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def sanitize_error(message: object, *, secrets: Sequence[str] = ()) -> str:
    text = str(message).replace("\x00", "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text.strip()[:MAX_ERROR_CHARS]


_FENCED_JSON = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def extract_json(text: str) -> Any:
    """Extract one JSON value without accepting prose as structured output."""

    candidate = text.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = _FENCED_JSON.fullmatch(candidate)
        if match:
            return json.loads(match.group(1))
        raise


def normalized_result(
    request: ProviderRequest,
    *,
    content: str,
    usage: Mapping[str, Any] | None = None,
    latency_ms: float | None = None,
    metadata: Mapping[str, Any] | None = None,
    parsed: Any | None = None,
) -> ProviderResult:
    """Parse and validate the response contract before it reaches the core."""

    try:
        value = parsed if parsed is not None else extract_json(content)
        if not isinstance(value, dict):
            raise ValidationError("The Oída response contract must be a JSON object")
        if request.response_schema:
            validate(instance=value, schema=request.response_schema)
    except (json.JSONDecodeError, SchemaError, ValidationError) as exc:
        return ProviderResult(
            provider_id=request.provider_id,
            model_id=request.model_id,
            status="error",
            content=content,
            usage=_normalized_usage(usage),
            latency_ms=_latency(latency_ms),
            error=f"Provider returned an invalid structured response: {sanitize_error(exc)}",
            raw_metadata={**dict(metadata or {}), "structured_output_invalid": True},
        )
    return ProviderResult(
        provider_id=request.provider_id,
        model_id=request.model_id,
        status="ok",
        content=content,
        parsed=value,
        usage=_normalized_usage(usage),
        latency_ms=_latency(latency_ms),
        raw_metadata=dict(metadata or {}),
    )


def error_result(
    request: ProviderRequest,
    error: object,
    *,
    latency_ms: float | None = None,
    secrets: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> ProviderResult:
    return ProviderResult(
        provider_id=request.provider_id,
        model_id=request.model_id,
        status="error",
        usage=None,
        latency_ms=_latency(latency_ms),
        error=sanitize_error(error, secrets=secrets),
        raw_metadata=dict(metadata or {}),
    )


def require_matching_provider(request: ProviderRequest, provider_id: str) -> None:
    if request.provider_id != provider_id:
        raise ValueError(
            f"Request targets provider {request.provider_id!r}, not {provider_id!r}"
        )


def _latency(value: float | None) -> int | None:
    return max(0, round(value)) if value is not None else None


def _normalized_usage(value: Mapping[str, Any] | None) -> dict[str, int | None] | None:
    if not value:
        return None

    def token(*names: str) -> int | None:
        for name in names:
            candidate = value.get(name)
            if type(candidate) is int and candidate >= 0:
                return candidate
        return None

    input_tokens = token("input_tokens", "prompt_tokens", "prompt_eval_count")
    output_tokens = token("output_tokens", "completion_tokens", "eval_count")
    total_tokens = token("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if input_tokens is output_tokens is total_tokens is None:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
