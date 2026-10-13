import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from oida.reasoning.contracts import ModelRole, RoleAssignment
from oida.reasoning.settings import (
    ReasoningSettings,
    ReasoningSettingsStore,
)
from oida.reasoning.budget import (
    BudgetLedger,
    BudgetDenied,
    quote_cost,
)
from oida.reasoning.audio_router import (
    RoutedAudioEngine,
    _sanitize_audio_error,
)
from oida.engine_base import EngineUnavailable
from oida.reasoning.audio_selection import selector, use_audio_model
from oida.reasoning.model_catalog import find_model_spec
from oida.reasoning.providers.base import JsonResponse, ProviderTransportError
from test_reasoning_audio_router import (
    DictSecrets,
    FakeEngine,
    FakeTransport,
)
from oida.recipes import INSTRUCT_CAPTION


def _audio(root):
    import wave

    path = root / "fixture.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(bytes(32000))
    return path


def _setup_test_router(
    root: Path,
    *,
    transport: Any,
    secrets: DictSecrets | None = None,
    budget_enabled: bool = True,
    total_ceiling: float = 5.0,
) -> tuple[RoutedAudioEngine, FakeEngine, BudgetLedger]:
    settings_store = ReasoningSettingsStore(root / "reasoning.json")
    settings = ReasoningSettings(allow_external_audio=True)
    providers = dict(settings.providers)
    providers["google"] = providers["google"].model_copy(update={"enabled": True})
    providers["alibaba"] = providers["alibaba"].model_copy(update={"enabled": True})
    roles = dict(settings.roles)
    roles[ModelRole.FAST_PERCEPTION] = RoleAssignment(
        provider_id="google",
        model_id="gemini-3.5-flash-lite",
    )
    settings = settings.model_copy(update={"providers": providers, "roles": roles})
    settings_store.save(settings)
    local = FakeEngine()
    budget = BudgetLedger(root / "budget")
    if budget_enabled:
        budget.update_config(
            {
                "enabled": True,
                "total_ceiling_usd": total_ceiling,
            }
        )
    router = RoutedAudioEngine(
        local,
        settings_store=settings_store,
        secret_store=secrets
        or DictSecrets(
            {
                ("google", "api_key"): "google-secret",
                ("alibaba", "api_key"): "alibaba-secret",
            }
        ),
        budget_ledger=budget,
        transport=transport,
    )
    return router, local, budget


def test_default_budget_config_and_persistence():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = BudgetLedger(root / "budget")
        state = ledger.state()
        assert state["enabled"] is False
        assert state["total_ceiling_usd"] == 5.0
        assert state["remaining_usd"] == 5.0
        assert state["committed_usd"] == 0.0

        updated = ledger.update_config({"enabled": True, "total_ceiling_usd": 4.0})
        assert updated.enabled is True
        assert updated.total_ceiling_usd == 4.0

        # Reload from disk
        reloaded = BudgetLedger(root / "budget")
        assert reloaded.config.enabled is True
        assert reloaded.config.total_ceiling_usd == 4.0


def test_two_instances_share_one_atomic_trial_configuration_and_reservations():
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "shared-trial"
        first = BudgetLedger(root)
        second = BudgetLedger(root)
        estimate = quote_cost(
            "google",
            "gemini-3.5-flash-lite",
            audio_seconds=0.5,
            max_output_tokens=128,
        )
        assert estimate is not None
        first.update_config(
            {
                "enabled": True,
                "total_ceiling_usd": estimate["worst_case_usd"] * 1.5,
                "max_output_tokens_per_call": 128,
            }
        )
        first.reserve(
            "google",
            "gemini-3.5-flash-lite",
            audio_seconds=0.5,
            max_output_tokens=128,
        )
        with pytest.raises(BudgetDenied):
            second.reserve(
                "google",
                "gemini-3.5-flash-lite",
                audio_seconds=0.5,
                max_output_tokens=128,
            )
        assert first.trial_id == second.trial_id
        assert second.state()["calls_used"] == 1


def test_probe_fingerprint_and_invalidation_are_durable():
    with TemporaryDirectory() as tmp:
        ledger = BudgetLedger(Path(tmp) / "trial")
        ledger.record_probe("google", "model", fingerprint="settings-a")
        assert ledger.is_probed("google", "model", fingerprint="settings-a")
        assert not ledger.is_probed("google", "model", fingerprint="settings-b")
        ledger.invalidate_probes("google")
        assert not ledger.is_probed("google", "model", fingerprint="settings-a")


def test_pricing_and_quote_cost():
    for prov_id, model_id in [
        ("google", "gemini-3.5-flash-lite"),
        ("google", "gemini-3.8-flash"),
        ("google", "gemini-3.1-pro-preview"),
        ("alibaba", "qwen3.5-omni-flash"),
        ("alibaba", "qwen3.5-omni-plus"),
    ]:
        quote = quote_cost(prov_id, model_id, audio_seconds=10.0, max_output_tokens=100)
        assert quote is not None
        assert quote["worst_case_usd"] > 0.0

    missing = quote_cost(
        "unknown", "unknown-model", audio_seconds=10.0, max_output_tokens=100
    )
    assert missing is None


def test_zero_outbound_requests_when_budget_denied():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        transport = FakeTransport(
            {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}
        )
        router, local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=False
        )
        audio = _audio(root)

        # 1. Budget disabled -> fallback, 0 transport calls
        res = router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
        assert res.text == "local observation"
        assert len(transport.calls) == 0

        # 2. Budget exhausted -> fallback, 0 transport calls
        budget.update_config({"enabled": True, "total_ceiling_usd": 0.00001})
        res = router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
        assert res.text == "local observation"
        assert len(transport.calls) == 0


def test_reservation_settlement_and_receipt_enrichment():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        google_resp = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Observed audio details"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 320,
                "candidatesTokenCount": 45,
                "thoughtsTokenCount": 0,
                "totalTokenCount": 365,
            },
        }
        transport = FakeTransport(google_resp)
        router, local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=True
        )
        audio = _audio(root)

        sel = selector(find_model_spec("google", "gemini-3.5-flash-lite"))
        with use_audio_model(sel):
            res = router.generate(
                str(audio),
                "Describe",
                settings=INSTRUCT_CAPTION,
            )

        assert res.text == "Observed audio details"
        assert len(transport.calls) == 1
        assert len(res.pass_provenance) == 1
        receipt = res.pass_provenance[0]
        assert receipt.get("status") == "complete"
        assert receipt.get("estimated_cost_usd") is not None
        assert receipt.get("estimated_cost_usd") > 0
        assert receipt.get("reservation_id") is not None

        # Ledger state settled
        state = budget.state()
        assert state["committed_usd"] > 0
        assert len(state["reservations"]) == 1
        assert state["reservations"][0]["status"] == "settled"
        assert state["reservations"][0]["settled_usd"] > 0


def test_unknown_remote_outcome_retains_full_reservation():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Sequence transport that raises ProviderTransportError with submitted=True
        # simulating a 500 error or timeout during read
        class FailingTransport:
            def __init__(self):
                self.calls = []

            def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
                self.calls.append({"method": method, "url": url, **kwargs})
                raise ProviderTransportError(
                    "Remote server error: HTTP 500 Internal Error",
                    status=500,
                    submitted=True,
                )

        transport = FailingTransport()
        router, local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=True
        )
        audio = _audio(root)

        sel = selector(find_model_spec("google", "gemini-3.5-flash-lite"))
        with use_audio_model(sel):
            with pytest.raises(EngineUnavailable):
                router.generate(
                    str(audio),
                    "Describe",
                    settings=INSTRUCT_CAPTION,
                )

        assert len(transport.calls) == 1

        # Check budget ledger: reservation status is "unresolved"
        # committed_usd must retain the full reservation amount and NOT be zero!
        state = budget.state()
        reservations = state["reservations"]
        assert len(reservations) == 1
        assert reservations[0]["status"] == "unresolved"
        assert state["committed_usd"] > 0
        assert state["committed_usd"] == reservations[0]["reserved_usd"]


def test_presubmission_failure_and_bounded_retry():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Simulating socket connect error (submitted=False)
        class ConnFailTransport:
            def __init__(self):
                self.calls = []

            def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
                self.calls.append({"method": method, "url": url, **kwargs})
                err = urllib.error.URLError("Connection refused")
                raise ProviderTransportError(
                    "Connection failed", submitted=False
                ) from err

        transport = ConnFailTransport()
        router, local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=True
        )
        audio = _audio(root)

        sel = selector(find_model_spec("google", "gemini-3.5-flash-lite"))
        with use_audio_model(sel):
            with pytest.raises(EngineUnavailable):
                router.generate(
                    str(audio),
                    "Describe",
                    settings=INSTRUCT_CAPTION,
                )

        # Bounded retry: exactly 2 attempts (1 initial + 1 retry)
        assert len(transport.calls) == 2

        # Pre-submission failure releases the reservation
        state = budget.state()
        assert state["committed_usd"] == 0.0
        assert state["reservations"][0]["status"] == "released"


def test_disable_between_unsubmitted_attempts_prevents_retry_and_releases():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        class DisableAfterConnectFailure:
            def __init__(self):
                self.calls = []
                self.budget = None

            def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
                self.calls.append({"method": method, "url": url, **kwargs})
                assert self.budget is not None
                self.budget.update_config({"enabled": False})
                error = urllib.error.URLError("Connection refused")
                raise ProviderTransportError(
                    "Connection failed", submitted=False
                ) from error

        transport = DisableAfterConnectFailure()
        router, _local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=True
        )
        transport.budget = budget
        audio = _audio(root)
        selected = selector(find_model_spec("google", "gemini-3.5-flash-lite"))
        with use_audio_model(selected):
            with pytest.raises(EngineUnavailable):
                router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
        assert len(transport.calls) == 1
        state = budget.state()
        assert state["committed_usd"] == 0.0
        assert state["reservations"][0]["status"] == "released"


def test_cancellation_stops_before_and_after_dispatch():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        google_resp = {
            "candidates": [
                {"content": {"parts": [{"text": "Late sound observation"}]}}
            ],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
        }
        transport = FakeTransport(google_resp)
        router, local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=True
        )
        audio = _audio(root)

        # 1. Stop check before dispatch: releases reservation, 0 transport calls
        sel = selector(find_model_spec("google", "gemini-3.5-flash-lite"))
        stopped = True
        with use_audio_model(sel), router.request_policy(stop_check=lambda: stopped):
            with pytest.raises(EngineUnavailable):
                router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
        assert len(transport.calls) == 0
        assert budget.state()["committed_usd"] == 0.0

        # 2. Stop check after response: settles usage and discards response
        def stop_after():
            return len(transport.calls) > 0

        with use_audio_model(sel), router.request_policy(stop_check=stop_after):
            with pytest.raises(EngineUnavailable):
                router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
        # Transport was called and settled
        assert len(transport.calls) == 1
        assert budget.state()["committed_usd"] > 0


def test_error_sanitization_no_raw_bytes():
    raw_b64 = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="
    error_str = f"Failed to upload audio payload: {raw_b64} returned 400"
    sanitized = _sanitize_audio_error(error_str)
    assert raw_b64 not in sanitized
    assert "[audio_data_redacted]" in sanitized


def test_source_specific_external_admission():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        transport = FakeTransport(
            {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )
        router, local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=True
        )
        audio = _audio(root)

        sel = selector(find_model_spec("google", "gemini-3.5-flash-lite"))
        with use_audio_model(sel):
            # 1. Microphone without allow_external_source -> blocked
            with router.request_policy(
                source_type="microphone", allow_external_source=False
            ):
                with pytest.raises(EngineUnavailable) as exc:
                    router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
                assert (
                    "live microphone audio requires explicit external audio permission"
                    in str(exc.value)
                )
            assert len(transport.calls) == 0

            # 2. Microphone with allow_external_source -> admitted
            with router.request_policy(
                source_type="microphone", allow_external_source=True
            ):
                res = router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
            assert res.text == "ok"
            assert len(transport.calls) == 1

            # 3. Incognito mode -> blocked
            with router.request_policy(privacy_mode="incognito"):
                with pytest.raises(EngineUnavailable) as exc:
                    router.generate(str(audio), "Describe", settings=INSTRUCT_CAPTION)
            assert len(transport.calls) == 1


def test_authorized_audio_probe():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        google_resp = {
            "candidates": [
                {"content": {"parts": [{"text": "Probe detected silence"}]}}
            ],
            "usageMetadata": {"promptTokenCount": 16, "candidatesTokenCount": 5},
        }
        transport = FakeTransport(google_resp)
        router, local, budget = _setup_test_router(
            root, transport=transport, budget_enabled=True
        )

        assert router.is_model_probed("google", "gemini-3.5-flash-lite") is False

        probe_res = router.probe_audio("google", "gemini-3.5-flash-lite")
        assert probe_res["ok"] is True
        assert probe_res["status"] == "inference_tested"
        assert router.is_model_probed("google", "gemini-3.5-flash-lite") is True

        # Probe persisted in budget database
        assert budget.is_probed("google", "gemini-3.5-flash-lite") is True


def test_failed_audio_probe_does_not_expose_provider_exception(tmp_path, monkeypatch):
    router, _, budget = _setup_test_router(
        tmp_path, transport=FakeTransport({}), budget_enabled=True
    )

    def fail(*args, **kwargs):
        raise RuntimeError("private provider credential and local source details")

    monkeypatch.setattr(router, "generate", fail)
    result = router.probe_audio("google", "gemini-3.5-flash-lite")
    assert result == {
        "ok": False,
        "status": "failed",
        "error": "Audio probe failed; check provider configuration and availability",
    }
    assert not budget.is_probed("google", "gemini-3.5-flash-lite")
