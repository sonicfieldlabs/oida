"""M1 explicit selections: fixtures only, no provider calls or real credentials."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from oida.engine_base import EngineUnavailable
from oida.reasoning.audio_selection import (
    resolve,
    selector,
    selected_audio_model,
    use_audio_model,
    validate_endpoint,
)
from oida.reasoning.contracts import ReasoningSettings
from oida.reasoning.model_catalog import find_model_spec
from oida.reasoning.providers.base import UrllibJsonTransport
from oida.recipes import INSTRUCT_CAPTION, THINKING_REASONING
from test_reasoning_audio_router import FakeTransport, DictSecrets, _router, _audio


def selection(model="gemini-3.5-flash-lite"):
    return selector(find_model_spec("google", model))


def configured():
    settings = ReasoningSettings()
    providers = dict(settings.providers)
    providers["google"] = providers["google"].model_copy(update={"enabled": True})
    return settings.model_copy(
        update={"providers": providers, "allow_external_audio": True}
    )


def response():
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "hidden", "thought": True}, {"text": "a bell"}]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 17,
            "candidatesTokenCount": 3,
            "thoughtsTokenCount": 6,
        },
    }


def test_override_all_passes_without_mutating_roles(tmp_path):
    transport = FakeTransport(response())
    router, local = _router(
        tmp_path,
        configured(),
        transport=transport,
        secrets=DictSecrets({("google", "api_key"): "fixture"}),
    )
    before = router.settings_store.load()
    with use_audio_model(selection()):
        for recipe in [INSTRUCT_CAPTION, THINKING_REASONING]:
            result = router.generate(str(_audio(tmp_path)), "Describe", recipe)
            assert result.text == "a bell"
            assert result.reasoning_trace is None
            assert result.usage == dict(
                input_tokens=17, output_tokens=3, reasoning_tokens=6, total_tokens=None
            )
            assert (
                result.pass_provenance[0]["requested_audio_model"]
                == selection().model_dump()
            )
    assert len(transport.calls) == 2 and not local.calls
    assert all(
        "gemini-3.5-flash-lite:generateContent" in c["url"] for c in transport.calls
    )
    assert "inlineData" in transport.calls[0]["payload"]["contents"][0]["parts"][0]
    assert router.settings_store.load() == before
    assert selected_audio_model() is None


@pytest.mark.parametrize(
    "case", ["disabled", "incognito", "malformed", "thought_only", "external_denied"]
)
def test_explicit_failures_never_fallback(tmp_path, case):
    settings = configured()
    data = response()
    if case == "disabled":
        settings.providers["google"].enabled = False
    if case == "external_denied":
        settings.allow_external_audio = False
    if case == "malformed":
        data = {"error": "fixture"}
    if case == "thought_only":
        data["candidates"][0]["content"]["parts"] = [
            {"text": "hidden", "thought": True}
        ]
    transport = FakeTransport(data)
    router, local = _router(
        tmp_path,
        settings,
        transport=transport,
        secrets=DictSecrets({("google", "api_key"): "fixture"}),
    )
    with (
        use_audio_model(selection()),
        router.request_policy(
            privacy_mode="incognito" if case == "incognito" else "ephemeral"
        ),
    ):
        with pytest.raises(EngineUnavailable):
            router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)
    assert not local.calls
    if case in {"disabled", "incognito", "external_denied"}:
        assert not transport.calls


def test_production_cloud_transport_is_closed_before_credentials(tmp_path):
    router, local = _router(tmp_path, configured(), transport=UrllibJsonTransport())
    with (
        use_audio_model(selection()),
        pytest.raises(EngineUnavailable, match="pending M2"),
    ):
        router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)
    assert not local.calls


def test_concurrent_selection_and_exception_reset():
    barrier = Barrier(2)

    def task(model):
        selected = selection(model)
        with use_audio_model(selected):
            barrier.wait(timeout=5)
            assert selected_audio_model() == selected
        assert selected_audio_model() is None
        return selected.model_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert (
            len(
                set(pool.map(task, ["gemini-3.5-flash-lite", "gemini-3.1-pro-preview"]))
            )
            == 2
        )
    with pytest.raises(RuntimeError):
        with use_audio_model(selection()):
            raise RuntimeError()
    assert selected_audio_model() is None


@pytest.mark.parametrize(
    "url",
    [
        "http://generativelanguage.googleapis.com/v1beta",
        "https://generativelanguage.googleapis.com.evil/v1beta",
        "https://secret@generativelanguage.googleapis.com/v1beta",
        "https://generativelanguage.googleapis.com/v1beta?key=fixture",
    ],
)
def test_endpoint_refusals(url):
    with pytest.raises(ValueError):
        validate_endpoint("google", url)


def test_opaque_deployment_refuses_cross_model_substitution():
    with pytest.raises(ValueError):
        resolve(selection().model_copy(update={"model_id": "gemini-3.8-flash"}))


@pytest.mark.parametrize(
    "alias,recipe,checkpoint",
    [
        ("instruct", INSTRUCT_CAPTION, "MOSS-Audio-4B-Instruct"),
        ("thinking", THINKING_REASONING, "MOSS-Audio-4B-Thinking"),
    ],
)
@pytest.mark.parametrize("use_alias", [True, False])
def test_local_selection_records_resolved_identity(
    tmp_path, alias, recipe, checkpoint, use_alias
):
    from oida.engine_base import EngineResult, use_listening_model

    actual = find_model_spec("oida_moss", "OpenMOSS-Team/" + checkpoint)
    requested = selector(find_model_spec("oida_moss", alias) if use_alias else actual)
    router, local = _router(tmp_path, ReasoningSettings(), transport=FakeTransport({}))
    local.generate = lambda *args: EngineResult(
        text="technical fixture",
        model="/local/" + checkpoint,
        profile="fake-local",
        settings=recipe,
        pass_provenance=[{}],
    )
    with use_audio_model(requested), use_listening_model("/local/" + checkpoint):
        result = router.generate(str(_audio(tmp_path)), "Describe", recipe)
    assert result.pass_provenance[0]["requested_audio_model"] == requested.model_dump()
    assert (
        result.pass_provenance[0]["actual_audio_model"] == selector(actual).model_dump()
    )


@pytest.mark.parametrize("pinned", [None, "instruct", "/local/unknown-model"])
def test_local_alias_rejects_unresolved_target_before_inference(tmp_path, pinned):
    from oida.engine_base import use_listening_model

    router, local = _router(tmp_path, ReasoningSettings(), transport=FakeTransport({}))
    requested = selector(find_model_spec("oida_moss", "instruct"))
    with (
        use_audio_model(requested),
        use_listening_model(pinned),
        pytest.raises(EngineUnavailable),
    ):
        router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)
    assert not local.calls


@pytest.mark.parametrize(
    "requested_id", ["instruct", "OpenMOSS-Team/MOSS-Audio-4B-Instruct"]
)
@pytest.mark.parametrize(
    "actual_id", ["/local/MOSS-Audio-4B-Thinking", "unknown-model", "instruct"]
)
def test_local_selection_rejects_substitution(tmp_path, requested_id, actual_id):
    from oida.engine_base import EngineResult, use_listening_model

    router, local = _router(tmp_path, ReasoningSettings(), transport=FakeTransport({}))
    local.generate = lambda *args: EngineResult(
        text="technical fixture",
        model=actual_id,
        profile="fake-local",
        settings=INSTRUCT_CAPTION,
        pass_provenance=[{}],
    )
    requested = selector(find_model_spec("oida_moss", requested_id))
    with (
        use_audio_model(requested),
        use_listening_model("/local/MOSS-Audio-4B-Instruct"),
        pytest.raises(EngineUnavailable),
    ):
        router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)


@pytest.mark.parametrize("pinned", [None, "/local/MOSS-Audio-4B-Thinking"])
def test_local_alias_refuses_missing_or_mismatched_resolution(tmp_path, pinned):
    from oida.engine_base import EngineResult, use_listening_model

    router, local = _router(tmp_path, ReasoningSettings(), transport=FakeTransport({}))
    local.generate = lambda *args: EngineResult(
        text="technical fixture",
        model="/local/MOSS-Audio-4B-Instruct",
        profile="fake-local",
        settings=INSTRUCT_CAPTION,
        pass_provenance=[{}],
    )
    requested = selector(find_model_spec("oida_moss", "instruct"))
    with (
        use_audio_model(requested),
        use_listening_model(pinned),
        pytest.raises(EngineUnavailable),
    ):
        router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)


@pytest.mark.parametrize("actual", ["qwen3.5-omni-flash", "wrong-model"])
def test_qwen_sse_usage_text_only_and_model_identity(tmp_path, actual):
    import json
    from oida.reasoning.providers.base import _aggregate_openai_sse

    events = [
        {
            "model": actual,
            "choices": [
                {"delta": {"reasoning_content": "hidden", "content": "a bell"}}
            ],
        },
        {"usage": {"prompt_tokens": 9, "completion_tokens": 4}, "choices": []},
    ]
    data = _aggregate_openai_sse(
        "\n".join("data: " + json.dumps(e) for e in events) + "\ndata: [DONE]\n"
    )
    settings = configured()
    settings.providers["alibaba"].enabled = True
    transport = FakeTransport(data)
    router, local = _router(
        tmp_path,
        settings,
        transport=transport,
        secrets=DictSecrets({("alibaba", "api_key"): "fixture"}),
    )
    chosen = selector(find_model_spec("alibaba", "qwen3.5-omni-flash"))
    with use_audio_model(chosen):
        if actual == "wrong-model":
            with pytest.raises(EngineUnavailable, match="different model"):
                router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)
        else:
            result = router.generate(
                str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION
            )
            assert result.text == "a bell" and result.reasoning_trace is None
            assert result.usage["input_tokens"] == 9
    payload = transport.calls[0]["payload"]
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["modalities"] == ["text"]
    assert not local.calls


def test_explicit_selection_refuses_inner_local_stub_fallback():
    from oida.engine import FallbackEngine
    from test_reasoning_audio_router import FakeEngine

    class Unavailable(FakeEngine):
        def generate(self, *args, **kwargs):
            raise EngineUnavailable("fixture unavailable")

    fallback = FakeEngine()
    engine = FallbackEngine(Unavailable(), fallback)
    with use_audio_model(
        selector(find_model_spec("oida_moss", "OpenMOSS-Team/MOSS-Audio-4B-Thinking"))
    ):
        with pytest.raises(EngineUnavailable):
            engine.generate("fixture.wav", "Describe", INSTRUCT_CAPTION)
    assert not fallback.calls


def test_local_selection_receipt_and_mismatch(tmp_path):
    from dataclasses import replace
    from test_reasoning_audio_router import FakeEngine

    chosen = selector(find_model_spec("oida_moss", "MOSS-Audio-4B-Thinking"))
    router, _ = _router(tmp_path, configured(), transport=FakeTransport({}))

    class Local(FakeEngine):
        def generate(self, *args, **kwargs):
            return replace(
                super().generate(*args, **kwargs),
                model=chosen.model_id,
                reasoning_trace="hidden",
                pass_provenance=[{}],
            )

    router.local_engine = Local()
    with use_audio_model(chosen):
        result = router.generate(str(_audio(tmp_path)), "Describe", THINKING_REASONING)
        assert result.reasoning_trace is None
        assert result.pass_provenance[0]["actual_audio_model"] == chosen.model_dump()
        router.local_engine = FakeEngine()
        with pytest.raises(EngineUnavailable, match="selected audio model"):
            router.generate(str(_audio(tmp_path)), "Describe", THINKING_REASONING)
