"""A listening's own bounds on its model passes, and the Metal pool kept from growing."""

from __future__ import annotations

from pathlib import Path

import pytest

from oida.engine_base import EngineResult, MossEngine
from oida.engine_mps import MetalPool
from oida.reasoning.audio_router import RoutedAudioEngine
from oida.reasoning.contracts import ReasoningSettings
from oida.reasoning.secrets import SecretStore
from oida.reasoning.settings import ReasoningSettingsStore
from oida.recipes import INSTRUCT_CAPTION, THINKING_REASONING, PassLimits


def test_a_ceiling_lowers_a_recipes_limit_and_never_raises_it():
    lowered = PassLimits(max_new_tokens=384).apply(INSTRUCT_CAPTION)
    assert (lowered.max_new_tokens, lowered.limit_source) == (384, "listening")
    assert lowered.temperature == INSTRUCT_CAPTION.temperature
    kept = PassLimits(max_new_tokens=4000).apply(INSTRUCT_CAPTION)
    assert kept is INSTRUCT_CAPTION and kept.limit_source == "recipe"
    assert PassLimits().apply(INSTRUCT_CAPTION) is INSTRUCT_CAPTION


def test_a_listenings_thinking_budget_rides_on_the_settings():
    bounded = PassLimits(max_new_tokens=768, thinking_budget=512).apply(THINKING_REASONING)
    assert (bounded.max_new_tokens, bounded.thinking_budget) == (768, 512)
    # A budget the settings already carry is theirs.
    assert PassLimits(thinking_budget=100).apply(bounded).thinking_budget == 512


def test_a_request_refuses_a_budget_that_leaves_no_answer():
    pytest.importorskip("fastapi")
    from pydantic import ValidationError

    from oida.server import GatewayListenRequest, pass_limits
    from oida.source_api import AcquisitionRequest

    request = GatewayListenRequest(path="/tmp/x.wav", max_new_tokens=512, thinking_budget=256)
    assert pass_limits(request) == PassLimits(max_new_tokens=512, thinking_budget=256)
    assert pass_limits(GatewayListenRequest(path="/tmp/x.wav")) is None
    with pytest.raises(ValidationError):
        GatewayListenRequest(path="/tmp/x.wav", max_new_tokens=256, thinking_budget=256)
    with pytest.raises(ValidationError):
        GatewayListenRequest(path="/tmp/x.wav", max_new_tokens=8)
    capture = AcquisitionRequest(acquisition_id="acq_1", seconds=10.0, max_new_tokens=384, thinking_budget=128)
    assert (capture.max_new_tokens, capture.thinking_budget) == (384, 128)
    assert AcquisitionRequest(acquisition_id="acq_2", seconds=10.0).max_new_tokens is None


class _Local(MossEngine):
    profile = "fake-local"

    def __init__(self):
        self.settings = []

    def generate(self, audio_path, prompt, settings, thinking_budget=None):
        self.settings.append(settings)
        return EngineResult(text="heard", model="local/model", profile=self.profile, settings=settings)

    def runtime_status(self):
        return {"assignments": {}, "loaded_models": []}


class _NoSecrets(SecretStore):
    def get(self, provider_id, name="api_key"):
        return None

    def set(self, provider_id, value, name="api_key"):
        raise AssertionError("no secret is written")

    def delete(self, provider_id, name="api_key"):
        return False


def test_every_pass_of_a_bounded_listening_runs_under_its_ceiling(tmp_path: Path):
    store = ReasoningSettingsStore(tmp_path / "reasoning.json")
    store.save(ReasoningSettings())
    local = _Local()
    router = RoutedAudioEngine(local, settings_store=store, secret_store=_NoSecrets())
    with router.request_policy(limits=PassLimits(max_new_tokens=256, thinking_budget=64)):
        router.generate("/tmp/x.wav", "Describe.", INSTRUCT_CAPTION)
        router.generate("/tmp/x.wav", "Think.", THINKING_REASONING)
    router.generate("/tmp/x.wav", "Describe.", INSTRUCT_CAPTION)
    assert [s.max_new_tokens for s in local.settings] == [256, 256, INSTRUCT_CAPTION.max_new_tokens]
    assert [s.limit_source for s in local.settings] == ["listening", "listening", "recipe"]
    assert [s.thinking_budget for s in local.settings] == [64, 64, None]


class _Metal:
    """A driver whose memory grows by 300 MB a step until the cache is emptied."""

    def __init__(self):
        self.held = 10_000 * 2**20
        self.emptied = 0

    def driver_allocated_memory(self):
        return self.held

    def empty_cache(self):
        self.held = 10_000 * 2**20
        self.emptied += 1

    def step(self):
        self.held += 300 * 2**20


def test_the_metal_pool_is_released_at_its_headroom_and_when_the_pass_ends():
    metal = _Metal()
    pool = MetalPool(metal, headroom_mb=1024)
    scores = object()
    for _ in range(20):
        metal.step()
        assert pool(None, scores) is scores, "the watcher changes no score"
    assert pool.releases == 5, "one release each time four steps passed the headroom"
    assert pool.peak == (10_000 + 1200) * 2**20
    metal.step()
    pool.finish()
    assert metal.held == 10_000 * 2**20
    assert pool.receipt() == {
        "headroom_mb": 1024,
        "driver_start_mb": 10_000,
        "driver_peak_mb": 11_200,
        "driver_end_mb": 10_000,
        "releases": 5,
    }


def test_the_watcher_stays_off_other_devices_and_when_the_operator_says_so(monkeypatch):
    class Model:
        device = "cpu"

    class Torch:
        __version__ = "2.13.0"
        mps = _Metal()

    assert MetalPool.watching(Model(), Torch) is None
    Model.device = "mps:0"
    monkeypatch.setenv("OIDA_MPS_POOL_HEADROOM_MB", "0")
    assert MetalPool.watching(Model(), Torch) is None
    monkeypatch.setenv("OIDA_MPS_POOL_HEADROOM_MB", "512")
    pool = MetalPool.watching(Model(), Torch)
    assert pool is not None and pool.receipt()["headroom_mb"] == 512
    assert Torch.mps.emptied == 1, "the cache is emptied once before the pass is measured"
    monkeypatch.setenv("OIDA_MPS_POOL_HEADROOM_MB", "many")
    assert MetalPool.watching(Model(), Torch).receipt()["headroom_mb"] == 2048


def test_the_cache_is_not_emptied_on_a_torch_where_that_was_not_verified(monkeypatch):
    class Model:
        device = "mps:0"

    class Torch:
        __version__ = "2.10.0"
        mps = _Metal()

    monkeypatch.delenv("OIDA_MPS_POOL_HEADROOM_MB", raising=False)
    assert MetalPool.watching(Model(), Torch) is None
    assert Torch.mps.emptied == 0
    Torch.__version__ = "2.13.0+local"
    assert MetalPool.watching(Model(), Torch) is not None
    Torch.__version__ = "unknown"
    assert MetalPool.watching(Model(), Torch) is None
