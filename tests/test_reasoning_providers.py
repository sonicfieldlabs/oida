from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from typing import Any
from unittest.mock import patch

from oida.reasoning.contracts import (
    ModelDescriptor,
    ProviderDescriptor,
    ProviderKind,
    ProviderRequest,
    ProviderSettings,
    ReasoningSettings,
)
from oida.reasoning.providers.base import (
    MAX_CAPTURE_CHARS,
    CommandResult,
    JsonResponse,
    ProviderTransportError,
    _aggregate_openai_sse,
    endpoint_locality,
    run_command,
    validate_http_url,
)
from oida.reasoning.providers.claude import ClaudeProvider
from oida.reasoning.providers.codex import CodexProvider
from oida.reasoning.providers.hermes import HermesProvider
from oida.reasoning.providers.gemini import GeminiProvider
from oida.reasoning.providers.moss_catalog import MossCatalogProvider
from oida.reasoning.providers.openai_compatible import (
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
)
from oida.reasoning.providers.openclaw import OpenClawProvider
from oida.reasoning.providers.opencode import OpenCodeProvider
from oida.reasoning.registry import ProviderRegistry, build_provider_registry
from oida.reasoning.secrets import SecretStore


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def provider_request(provider_id: str, *, model_id: str | None = "model-a") -> ProviderRequest:
    return ProviderRequest(
        provider_id=provider_id,
        model_id=model_id,
        system_prompt="Stay grounded in the supplied listening evidence.",
        user_prompt="What was heard?",
        response_schema=SCHEMA,
        timeout_seconds=5,
    )


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        return self.handler(call)


class FakeRunner:
    def __init__(self, result: CommandResult):
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv, **kwargs: Any) -> CommandResult:
        self.calls.append({"argv": list(argv), **kwargs})
        return self.result


class DictSecretStore(SecretStore):
    def __init__(self, values: dict[tuple[str, str], str]):
        self.values = values

    def get(self, provider_id: str, name: str = "api_key") -> str | None:
        return self.values.get((provider_id, name))

    def set(self, provider_id: str, value: str, name: str = "api_key") -> None:
        self.values[(provider_id, name)] = value

    def delete(self, provider_id: str, name: str = "api_key") -> bool:
        return self.values.pop((provider_id, name), None) is not None


class HttpProviderTests(unittest.TestCase):
    def test_stream_aggregation_keeps_final_text_and_discards_reasoning_chunks(self) -> None:
        payload = _aggregate_openai_sse(
            "\n".join(
                [
                    'data: {"id":"one","model":"qwen","choices":[{"delta":{"reasoning_content":"private"}}]}',
                    'data: {"id":"one","model":"qwen","choices":[{"delta":{"content":"{\\"answer\\":"}}]}',
                    'data: {"id":"one","model":"qwen","choices":[{"delta":{"content":"\\"rain\\"}"},"finish_reason":"stop"}]}',
                    'data: {"id":"one","model":"qwen","choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}',
                    "data: [DONE]",
                ]
            )
        )

        self.assertEqual(payload["choices"][0]["message"]["content"], '{"answer":"rain"}')
        self.assertNotIn("private", json.dumps(payload))
        self.assertEqual(payload["usage"]["prompt_tokens"], 4)

    def test_safe_helpers_bound_output_and_reject_credential_urls(self) -> None:
        result = run_command(
            [sys.executable, "-c", f"print('x' * {MAX_CAPTURE_CHARS + 100})"]
        )
        self.assertLessEqual(len(result.stdout), MAX_CAPTURE_CHARS)
        with self.assertRaises(ValueError):
            validate_http_url("https://user:secret@models.example/v1")
        with self.assertRaises(ValueError):
            validate_http_url("https://models.example/v1?key=secret")
        self.assertEqual(
            validate_http_url("https://models.example/v1?cursor=next", allow_query=True).query,
            "cursor=next",
        )
        with self.assertRaisesRegex(ValueError, "invalid port"):
            validate_http_url("http://127.0.0.1:not-a-port/v1")
        with self.assertRaisesRegex(ValueError, "loopback"):
            validate_http_url("http://models.example/v1")
        self.assertEqual(
            validate_http_url("http://127.0.0.1:8080/v1").hostname,
            "127.0.0.1",
        )

    def test_ollama_sends_schema_without_thinking_or_streaming(self) -> None:
        transport = FakeTransport(
            lambda _call: JsonResponse(
                200,
                {
                    "message": {"role": "assistant", "content": '{"answer":"a bell"}'},
                    "prompt_eval_count": 10,
                    "eval_count": 4,
                    "done_reason": "stop",
                },
                {},
            )
        )
        provider = OllamaProvider(transport=transport)
        result = provider.complete(provider_request("ollama"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.parsed, {"answer": "a bell"})
        call = transport.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:11434/api/chat")
        self.assertFalse(call["payload"]["stream"])
        self.assertFalse(call["payload"]["think"])
        self.assertEqual(call["payload"]["format"], SCHEMA)
        self.assertEqual(result.usage.total_tokens, 14)

    def test_ollama_cloud_models_are_not_classified_as_local(self) -> None:
        transport = FakeTransport(
            lambda _call: JsonResponse(
                200,
                {
                    "models": [
                        {"name": "qwen3:8b", "details": {}},
                        {"name": "qwen3-vl:235b-cloud", "details": {}},
                    ]
                },
                {},
            )
        )
        models = OllamaProvider(transport=transport).list_models()
        self.assertEqual(models[0].locality, "local")
        self.assertEqual(models[1].locality, "external")

    def test_openai_compatible_uses_native_json_schema_once(self) -> None:
        transport = FakeTransport(
            lambda _call: JsonResponse(
                200,
                {
                    "id": "response-1",
                    "choices": [
                        {
                            "message": {"content": '{"answer":"rain"}', "reasoning_content": "private"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
                },
                {},
            )
        )
        provider = OpenAICompatibleProvider(
            base_url="http://127.0.0.1:9000/v1",
            api_key="secret-value",
            enabled=True,
            transport=transport,
        )
        result = provider.complete(provider_request("openai_compatible"))

        self.assertEqual(result.status, "ok")
        call = transport.calls[0]
        self.assertEqual(call["payload"]["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(call["headers"]["Authorization"], "Bearer secret-value")
        self.assertNotIn("private", json.dumps(result.model_dump(mode="json")))
        self.assertNotIn("secret-value", json.dumps(result.model_dump(mode="json")))

    def test_gemini_uses_native_system_instruction_and_response_schema(self) -> None:
        transport = FakeTransport(
            lambda _call: JsonResponse(
                200,
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": '{"answer":"rain"}'}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 3,
                        "totalTokenCount": 13,
                    },
                },
                {},
            )
        )
        provider = GeminiProvider(api_key="google-secret", enabled=True, transport=transport)

        result = provider.complete(provider_request("google", model_id="gemini-3.5-flash"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.parsed, {"answer": "rain"})
        call = transport.calls[0]
        self.assertTrue(call["url"].endswith("/models/gemini-3.5-flash:generateContent"))
        self.assertEqual(call["headers"], {"x-goog-api-key": "google-secret"})
        self.assertEqual(call["payload"]["generationConfig"]["responseJsonSchema"], SCHEMA)
        self.assertEqual(result.usage.total_tokens, 13)

    def test_openrouter_uses_external_endpoint_and_redacts_key_from_error(self) -> None:
        from oida.reasoning.providers.base import ProviderTransportError

        def fail(_call):
            raise ProviderTransportError("rejected secret-router-key")

        transport = FakeTransport(fail)
        provider = OpenRouterProvider(
            api_key="secret-router-key",
            enabled=True,
            app_url="https://oida.example",
            transport=transport,
        )
        result = provider.complete(provider_request("openrouter"))

        self.assertEqual(result.status, "error")
        self.assertNotIn("secret-router-key", result.error or "")
        self.assertEqual(endpoint_locality(provider.base_url), "external")
        self.assertEqual(transport.calls[0]["headers"]["HTTP-Referer"], "https://oida.example")

    def test_probe_never_exposes_a_key_echoed_by_endpoint(self) -> None:
        from oida.reasoning.providers.base import ProviderTransportError

        transport = FakeTransport(
            lambda _call: (_ for _ in ()).throw(
                ProviderTransportError("bad Authorization: Bearer probe-secret")
            )
        )
        descriptor = OpenAICompatibleProvider(
            base_url="https://models.example/v1",
            api_key="probe-secret",
            enabled=True,
            transport=transport,
        ).probe()
        self.assertNotIn("probe-secret", descriptor.detail or "")
        self.assertIn("[redacted]", descriptor.detail or "")

    def test_non_json_provider_text_fails_the_response_contract(self) -> None:
        transport = FakeTransport(
            lambda _call: JsonResponse(
                200,
                {"choices": [{"message": {"content": "It sounds like rain."}}]},
                {},
            )
        )
        provider = OpenAICompatibleProvider(enabled=True, transport=transport)
        result = provider.complete(provider_request("openai_compatible"))
        self.assertEqual(result.status, "error")
        self.assertTrue(result.raw_metadata["structured_output_invalid"])


class HostCliProviderTests(unittest.TestCase):
    def test_codex_probe_accepts_status_on_stderr(self) -> None:
        provider = CodexProvider(
            executable="/bin/echo",
            runner=FakeRunner(CommandResult(0, stderr="Logged in using ChatGPT\n")),
        )
        self.assertTrue(provider.probe().authenticated)

    def test_claude_is_zero_tool_safe_mode_and_ephemeral(self) -> None:
        runner = FakeRunner(
            CommandResult(
                0,
                json.dumps(
                    {
                        "result": '{"answer":"voice"}',
                        "structured_output": {"answer": "voice"},
                        "usage": {"input_tokens": 8, "output_tokens": 3},
                    }
                ),
                latency_ms=12,
            )
        )
        provider = ClaudeProvider(executable="/bin/echo", runner=runner)
        result = provider.complete(provider_request("claude", model_id="sonnet"))

        self.assertEqual(result.status, "ok")
        call = runner.calls[0]
        argv = call["argv"]
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("--safe-mode", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--disable-slash-commands", argv)
        self.assertNotIn("What was heard?", argv)
        self.assertEqual(call["input_text"], "What was heard?")

    def test_hermes_embeds_full_system_envelope_and_bounds_tools(self) -> None:
        runner = FakeRunner(
            CommandResult(
                0,
                'Warning: Unknown toolsets: oida-no-tools\n{"answer":"a pulse"}\n',
                stderr="session_id: session-1\n",
                latency_ms=9,
            )
        )
        provider = HermesProvider(executable="/bin/echo", runner=runner)
        result = provider.complete(provider_request("hermes"))

        self.assertEqual(result.status, "ok")
        call = runner.calls[0]
        argv = call["argv"]
        prompt = argv[argv.index("-q") + 1]
        self.assertIn("SYSTEM INSTRUCTIONS", prompt)
        self.assertIn("Stay grounded", prompt)
        self.assertEqual(argv[argv.index("--toolsets") + 1], "oida-no-tools")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertEqual(argv[argv.index("--source") + 1], "tool")
        self.assertIn("--safe-mode", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertEqual(len(runner.calls), 1)
        isolated_home = call["env"]["HERMES_HOME"]
        self.assertNotEqual(isolated_home, os.environ.get("HERMES_HOME"))
        self.assertFalse(os.path.exists(isolated_home))
        self.assertEqual(call["env"]["HERMES_IGNORE_USER_CONFIG"], "1")
        self.assertTrue(result.raw_metadata["isolated_state_deleted"])

    def test_hermes_deletes_isolated_state_on_nonzero_and_timeout(self) -> None:
        failed = FakeRunner(CommandResult(2, stderr="session_id: failed-session\nprovider failed"))
        failure = HermesProvider(executable="/bin/echo", runner=failed).complete(
            provider_request("hermes")
        )
        self.assertEqual(failure.status, "error")
        self.assertFalse(os.path.exists(failed.calls[0]["env"]["HERMES_HOME"]))
        self.assertTrue(failure.raw_metadata["isolated_state_deleted"])

        class TimeoutRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def __call__(self, argv, **kwargs: Any):
                self.calls.append({"argv": list(argv), **kwargs})
                raise ProviderTransportError("Provider command timed out")

        timed_out = TimeoutRunner()
        timeout = HermesProvider(executable="/bin/echo", runner=timed_out).complete(
            provider_request("hermes")
        )
        self.assertEqual(timeout.status, "error")
        self.assertFalse(os.path.exists(timed_out.calls[0]["env"]["HERMES_HOME"]))
        self.assertTrue(timeout.raw_metadata["isolated_state_deleted"])

    def test_openclaw_uses_raw_local_inference_not_agent_session(self) -> None:
        runner = FakeRunner(
            CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "transport": "local",
                        "provider": "anthropic",
                        "outputs": [{"text": '{"answer":"wind"}'}],
                    }
                ),
                latency_ms=7,
            )
        )
        provider = OpenClawProvider(executable="/bin/echo", runner=runner)
        result = provider.complete(provider_request("openclaw", model_id="anthropic/sonnet"))

        self.assertEqual(result.status, "ok")
        argv = runner.calls[0]["argv"]
        self.assertEqual(argv[1:4], ["infer", "model", "run"])
        self.assertIn("--local", argv)
        self.assertNotIn("agent", argv)

    def test_openclaw_accepts_current_result_object_variant(self) -> None:
        runner = FakeRunner(
            CommandResult(0, json.dumps({"ok": True, "result": {"content": '{"answer":"hum"}'}}))
        )
        result = OpenClawProvider(executable="/bin/echo", runner=runner).complete(
            provider_request("openclaw", model_id="provider/model")
        )
        self.assertEqual(result.parsed, {"answer": "hum"})

    def test_openclaw_model_catalog_uses_key_and_marks_cloud_ollama_external(self) -> None:
        runner = FakeRunner(
            CommandResult(
                0,
                json.dumps(
                    {
                        "models": [
                            {
                                "key": "ollama/qwen3-vl:235b-cloud",
                                "name": "qwen3-vl",
                                "input": "text+image",
                                "contextWindow": 32768,
                                "local": True,
                                "available": True,
                                "tags": [],
                            },
                            {
                                "key": "ollama/qwen3:8b",
                                "name": "qwen3",
                                "input": "text",
                                "contextWindow": 8192,
                                "local": True,
                                "available": True,
                                "tags": [],
                            },
                        ]
                    }
                ),
            )
        )
        models = OpenClawProvider(executable="/bin/echo", runner=runner).list_models()
        self.assertEqual(models[0].id, "ollama/qwen3-vl:235b-cloud")
        self.assertEqual(models[0].locality, "external")
        self.assertEqual(models[1].locality, "local")

    def test_codex_app_server_params_are_ephemeral_read_only_and_no_network(self) -> None:
        captured: dict[str, Any] = {}

        def execute(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"content": '{"answer":"footsteps"}', "duration_ms": 21}

        provider = CodexProvider(executable="/bin/echo", turn_executor=execute)
        request = provider_request("codex", model_id="gpt-5.4")
        result = provider.complete(request)

        self.assertEqual(result.status, "ok")
        thread = captured["thread_params"]
        turn = captured["turn_params"]
        self.assertTrue(thread["ephemeral"])
        self.assertEqual(thread["sandbox"], "read-only")
        self.assertEqual(thread["approvalPolicy"], "never")
        self.assertEqual(thread["developerInstructions"], request.system_prompt)
        self.assertEqual(thread["config"]["web_search"], "disabled")
        self.assertEqual(thread["config"]["mcp_servers"], {})
        self.assertEqual(turn["outputSchema"], SCHEMA)
        self.assertEqual(turn["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        self.assertEqual(turn["approvalPolicy"], "never")
        self.assertIn("app-server", captured["argv"])
        for feature in ("shell_tool", "browser_use", "computer_use", "apps", "multi_agent"):
            index = captured["argv"].index(feature)
            self.assertEqual(captured["argv"][index - 1], "--disable")


class OpenCodeProviderTests(unittest.TestCase):
    def test_attached_server_is_loopback_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            OpenCodeProvider(base_url="https://opencode.example")

    def test_message_disables_every_advertised_tool_and_deletes_session(self) -> None:
        def handler(call: dict[str, Any]) -> JsonResponse:
            if call["method"] == "POST" and call["url"].endswith("/session"):
                return JsonResponse(200, {"id": "session-1"}, {})
            if call["method"] == "GET" and call["url"].endswith("/experimental/tool/ids"):
                return JsonResponse(200, ["custom-plugin-tool", "bash"], {})
            if call["method"] == "POST" and call["url"].endswith("/message"):
                return JsonResponse(
                    200,
                    {
                        "info": {
                            "providerID": "anthropic",
                            "tokens": {"input": 12, "output": 3},
                        },
                        "parts": [{"type": "text", "text": '{"answer":"strings"}'}],
                    },
                    {},
                )
            if call["method"] == "DELETE":
                return JsonResponse(200, True, {})
            raise AssertionError(call)

        transport = FakeTransport(handler)
        provider = OpenCodeProvider(
            base_url="http://127.0.0.1:4096",
            managed=False,
            transport=transport,
        )
        result = provider.complete(provider_request("opencode", model_id="anthropic/sonnet"))

        self.assertEqual(result.status, "ok")
        message = next(call for call in transport.calls if call["url"].endswith("/message"))
        self.assertTrue(message["payload"]["tools"])
        self.assertTrue(all(value is False for value in message["payload"]["tools"].values()))
        self.assertFalse(message["payload"]["tools"]["custom-plugin-tool"])
        self.assertEqual(
            message["payload"]["model"], {"providerID": "anthropic", "modelID": "sonnet"}
        )
        self.assertEqual(message["payload"]["system"], "Stay grounded in the supplied listening evidence.")
        self.assertTrue(message["headers"]["x-opencode-directory"].startswith("%2F"))
        self.assertEqual(transport.calls[-1]["method"], "DELETE")
        self.assertTrue(result.raw_metadata["session_deleted"])

    def test_managed_server_command_is_pure_and_loopback(self) -> None:
        self.assertEqual(
            OpenCodeProvider.managed_argv("opencode", 4567),
            ["opencode", "serve", "--hostname", "127.0.0.1", "--port", "4567", "--pure"],
        )

    def test_managed_server_always_uses_ephemeral_basic_auth(self) -> None:
        class Process:
            stopped = False

            def poll(self):
                return 0 if self.stopped else None

            def terminate(self):
                self.stopped = True

            def kill(self):
                self.stopped = True

            def wait(self, timeout=None):
                del timeout
                return 0

        transport = FakeTransport(
            lambda call: JsonResponse(
                200,
                {} if call["url"].endswith("/global/health") else {"providers": []},
                {},
            )
        )
        provider = OpenCodeProvider(managed=True, transport=transport)
        with (
            patch(
                "oida.reasoning.providers.opencode.executable_path",
                return_value="/usr/bin/opencode",
            ),
            patch.object(OpenCodeProvider, "_free_port", return_value=4567),
            patch(
                "oida.reasoning.providers.opencode.subprocess.Popen",
                side_effect=lambda *args, **kwargs: Process(),
            ) as popen,
        ):
            self.assertEqual(provider.list_models(), [])

        environment = popen.call_args.kwargs["env"]
        password = environment["OPENCODE_SERVER_PASSWORD"]
        self.assertGreaterEqual(len(password), 32)
        expected = base64.b64encode(f"opencode:{password}".encode()).decode()
        self.assertTrue(transport.calls)
        self.assertTrue(
            all(call["headers"]["Authorization"] == f"Basic {expected}" for call in transport.calls)
        )
        self.assertNotIn(password, popen.call_args.args[0])

    def test_model_list_uses_compact_configured_provider_catalog(self) -> None:
        transport = FakeTransport(
            lambda call: JsonResponse(
                200,
                {
                    "providers": [
                        {
                            "id": "ollama",
                            "models": {
                                "qwen": {
                                    "id": "qwen3:8b",
                                    "name": "Qwen 3",
                                    "capabilities": {
                                        "reasoning": True,
                                        "input": {"text": True, "image": True},
                                    },
                                    "limit": {"context": 0},
                                }
                            },
                        }
                    ]
                },
                {},
            )
        )
        provider = OpenCodeProvider(
            base_url="http://127.0.0.1:4096",
            managed=False,
            transport=transport,
        )
        models = provider.list_models()
        self.assertTrue(transport.calls[0]["url"].endswith("/config/providers"))
        self.assertEqual(models[0].id, "ollama/qwen3:8b")
        self.assertEqual(models[0].capabilities, ["text", "reasoning", "image"])
        self.assertIsNone(models[0].context_window)
        self.assertTrue(models[0].metadata["connected"])


class RegistryTests(unittest.TestCase):
    def test_registry_distinguishes_catalog_support_from_a_discovered_local_model(self) -> None:
        class LocalAudioFixture:
            provider_id = "local_audio"

            def probe(self):
                return ProviderDescriptor(
                    id=self.provider_id,
                    name="Fixture local audio",
                    kind="openai_compatible",
                    locality="local",
                    enabled=True,
                    available=True,
                )

            def list_models(self):
                return [
                    ModelDescriptor(
                        id="OpenMOSS-Team/MOSS-Transcribe-Diarize",
                        provider_id=self.provider_id,
                        name="Detected transcriber",
                        capabilities=["audio", "transcription"],
                        locality="local",
                    )
                ]

            def complete(self, request):  # pragma: no cover - registry contract only
                raise AssertionError(request)

        registry = ProviderRegistry()
        registry.register(LocalAudioFixture(), enabled=True)

        models = {model.id: model for model in registry.list_models("local_audio")}
        detected = models["OpenMOSS-Team/MOSS-Transcribe-Diarize"]
        supported_only = models["mispeech/midashenglm-0.6b-fp32"]

        self.assertTrue(detected.metadata["catalog"])
        self.assertTrue(detected.metadata["discovered"])
        self.assertTrue(detected.metadata["installed"])
        self.assertTrue(detected.metadata["available"])
        self.assertTrue(supported_only.metadata["catalog"])
        self.assertFalse(supported_only.metadata["installed"])
        self.assertFalse(supported_only.metadata["available"])

    def test_factory_overlays_settings_default_model_and_secret_getter(self) -> None:
        settings = ReasoningSettings()
        settings.providers["openrouter"] = ProviderSettings(
            kind=ProviderKind.OPENROUTER,
            enabled=True,
            locality="external",
            default_model="openai/gpt-test",
        )
        secrets = DictSecretStore({("openrouter", "api_key"): "router-key"})
        registry = build_provider_registry(settings, secret_store=secrets)
        provider = registry.get("openrouter")
        self.assertIsInstance(provider, OpenRouterProvider)

        transport = FakeTransport(
            lambda _call: JsonResponse(
                200,
                {"choices": [{"message": {"content": '{"answer":"tone"}'}}]},
                {},
            )
        )
        provider._transport = transport  # type: ignore[attr-defined]
        result = registry.complete(provider_request("openrouter", model_id=None))

        self.assertEqual(result.status, "ok")
        self.assertEqual(transport.calls[0]["payload"]["model"], "openai/gpt-test")
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer router-key")

    def test_registry_exposes_all_host_ids_and_moss_catalog(self) -> None:
        registry = build_provider_registry(secret_store=DictSecretStore({}))
        self.assertTrue(
            {"codex", "claude", "hermes", "openclaw", "opencode", "ollama", "openrouter"}
            <= set(registry.ids())
        )
        self.assertIn("oida_moss", registry.ids())
        self.assertIn("local_structured", registry.ids())
        models = registry.list_models("oida_moss")
        model_ids = {model.id for model in models}
        self.assertTrue(
            {
                "instruct",
                "thinking",
                "OpenMOSS-Team/MOSS-Audio-8B-Instruct",
                "OpenMOSS-Team/MOSS-Audio-8B-Thinking",
                "OpenMOSS-Team/MOSS-Music-8B-Instruct",
                "OpenMOSS-Team/MOSS-Music-8B-Thinking",
            }
            <= model_ids
        )
        self.assertTrue(
            {
                "local_audio",
                "google",
                "alibaba",
                "nvidia",
            }
            <= set(registry.ids())
        )
        refused = MossCatalogProvider().complete(provider_request("oida_moss", model_id="instruct"))
        self.assertEqual(refused.status, "error")
        self.assertIn("perception", refused.error or "")

    def test_dynamic_openai_compatible_provider_keeps_its_configured_id(self) -> None:
        settings = ReasoningSettings()
        settings.providers["studio_llm"] = ProviderSettings(
            kind=ProviderKind.OPENAI_COMPATIBLE,
            enabled=True,
            locality="local",
            base_url="http://127.0.0.1:1234/v1",
        )
        registry = build_provider_registry(settings, secret_store=DictSecretStore({}))
        self.assertEqual(registry.get("studio_llm").provider_id, "studio_llm")

    def test_disabled_host_exposes_only_its_configured_model_without_running_cli(self) -> None:
        settings = ReasoningSettings()
        settings.providers["claude"] = ProviderSettings(
            kind=ProviderKind.HOST_CLI,
            enabled=False,
            locality="unknown",
            default_model="sonnet",
        )
        registry = build_provider_registry(settings, secret_store=DictSecretStore({}))
        models = registry.list_models("claude")
        self.assertEqual([model.id for model in models], ["sonnet"])
        self.assertTrue(models[0].metadata["configured_default"])


if __name__ == "__main__":
    unittest.main()
