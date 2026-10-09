"""Regression checks for budget and privacy defects found in M0-M6 review."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
import pytest

from oida.reasoning.budget import BudgetLedger, BudgetDenied
from oida.reasoning.audio_selection import selector, use_audio_model
from oida.reasoning.model_catalog import find_model_spec
from oida.reasoning.providers.base import ProviderTransportError
from oida.engine_base import EngineUnavailable
from oida.recipes import INSTRUCT_CAPTION
from test_audio_budget import _setup_test_router, _audio
from test_reasoning_audio_router import FakeTransport


def test_separate_ledger_instances_cannot_overbook(tmp_path):
    ledgers = [BudgetLedger(tmp_path), BudgetLedger(tmp_path)]
    ledgers[0].configure(dict(enabled=True, max_calls=1))
    barrier = Barrier(2)
    def reserve(ledger):
        barrier.wait()
        try:
            return ledger.reserve("google", "gemini-3.5-flash-lite", audio_seconds=1.0)["id"]
        except BudgetDenied:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(reserve, ledgers))
    assert sum(r is not None for r in results) == 1
    assert ledgers[1].state()["calls_used"] == 1
    ledgers[0].configure(dict(enabled=False))
    with pytest.raises(BudgetDenied):
        ledgers[1].reserve("google", "gemini-3.5-flash-lite", audio_seconds=1.0)


def test_trial_limit_and_unknown_holds_survive_restart(tmp_path):
    ledger = BudgetLedger(tmp_path)
    with pytest.raises(ValueError):
        ledger.configure(dict(total_ceiling_usd=5.01))
    ledger.configure(dict(enabled=True))
    reservation = ledger.reserve("google", "gemini-3.5-flash-lite", audio_seconds=1.0)
    ledger.settle(reservation["id"], usage=None, outcome="unknown_remote_outcome")
    with pytest.raises(ValueError):
        ledger.release(reservation["id"], reason="must not free a submitted request")
    assert BudgetLedger(tmp_path).state()["committed_usd"] == reservation["reserved_usd"]


def test_router_caps_actual_output_and_reserves_prompt_text(tmp_path):
    transport = FakeTransport({"candidates": [{"content": {"parts": [{"text": "bell"}]}}]})
    router, _, ledger = _setup_test_router(tmp_path, transport=transport)
    ledger.configure(dict(max_output_tokens_per_call=32))
    selected = selector(find_model_spec("google", "gemini-3.5-flash-lite"))
    with use_audio_model(selected):
        router.generate(str(_audio(tmp_path)), "x" * 4096, replace(INSTRUCT_CAPTION, max_new_tokens=2048))
    assert transport.calls[0]["payload"]["generationConfig"]["maxOutputTokens"] == 32
    row = ledger.state()["reservations"][0]
    assert row["estimate"]["text_input_tokens"] > 4096
    assert row["status"] == "unresolved"


def test_malformed_provider_output_remains_charged_and_no_retry(tmp_path):
    transport = FakeTransport({"error": "bad"})
    router, local, ledger = _setup_test_router(tmp_path, transport=transport)
    with use_audio_model(selector(find_model_spec("google", "gemini-3.5-flash-lite"))), pytest.raises(EngineUnavailable):
        router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)
    assert len(transport.calls) == 1 and not local.calls
    assert ledger.state()["reservations"][0]["status"] == "unresolved"
    assert ProviderTransportError("unknown").submitted is True


def test_probe_obeys_external_audio_permission_and_output_bound(tmp_path):
    transport = FakeTransport({"candidates": [{"content": {"parts": [{"text": "silence"}]}}]})
    router, _, ledger = _setup_test_router(tmp_path, transport=transport)
    config = router.settings_store.load()
    router.settings_store.save(config.model_copy(update={"allow_external_audio": False}))
    assert not router.probe_audio("google")["ok"]
    assert not transport.calls and ledger.state()["calls_used"] == 0
    router.settings_store.save(config)
    assert router.probe_audio("google")["ok"]
    assert transport.calls[0]["payload"]["generationConfig"]["maxOutputTokens"] == 128


def test_expired_deadline_prevents_any_dispatch(tmp_path):
    transport = FakeTransport({})
    router, _, ledger = _setup_test_router(tmp_path, transport=transport)
    with router.request_policy(deadline=0), pytest.raises(EngineUnavailable):
        router.generate(str(_audio(tmp_path)), "Describe", INSTRUCT_CAPTION)
    assert not transport.calls and ledger.state()["calls_used"] == 0
