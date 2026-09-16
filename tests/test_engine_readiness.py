"""The fixes that made the ear reachable, and the substitution policy.

15 September 2026. Before these, a cold engine and a broken one were
indistinguishable from outside, a failed primary silently produced stub
accounts, and a capture failure blamed the source configuration whatever the
cause. Each of those is now a stated fact somewhere, and these tests keep it so.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from oida.config import load_config
from oida.engine import FallbackEngine, build_engine
from oida.engine_base import EngineUnavailable
from oida.engine_mps import MpsMossEngine
from oida.engine_stub import StubMossEngine


def _mps(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_config(profile="mac-mps")


def test_ensure_ready_names_what_is_missing(monkeypatch, tmp_path):
    """Cheap: imports the MOSS modules, loads no weights, says where it looked."""
    config = _mps(monkeypatch, OIDA_MOSS_AUDIO_REPO=str(tmp_path / "nowhere"))
    engine = MpsMossEngine(config)
    with patch.dict("sys.modules", {"src": None, "src.audio_io": None}):
        with pytest.raises(EngineUnavailable) as caught:
            engine.ensure_ready()
    message = str(caught.value)
    assert "unavailable" in message and "nowhere" in message
    assert "install the moss extras" in message


def test_status_distinguishes_cold_from_broken(monkeypatch, tmp_path):
    config = _mps(monkeypatch, OIDA_MOSS_AUDIO_REPO=str(tmp_path / "nowhere"))
    primary = MpsMossEngine(config)
    engine = FallbackEngine(primary, StubMossEngine())
    with patch.dict("sys.modules", {"src": None, "src.audio_io": None}):
        status = engine.runtime_status()
    assert status["falls_back_to_stub"] is True
    assert "unavailable" in status["primary_unavailable_reason"]

    ready = FallbackEngine(StubMossEngine(), StubMossEngine())
    status = ready.runtime_status()
    assert "primary_unavailable_reason" not in status, "a ready engine carries no reason"


def test_without_a_backup_an_unavailable_primary_refuses(monkeypatch, tmp_path):
    """The stub is a profile, not a fallback."""
    monkeypatch.delenv("OIDA_MOSS_BACKUP_MODEL", raising=False)
    config = _mps(monkeypatch, OIDA_MOSS_AUDIO_REPO=str(tmp_path / "nowhere"))
    engine = build_engine(config)
    assert isinstance(engine, MpsMossEngine), "no backup configured, so no substitute stands behind mac-mps"
    with patch.dict("sys.modules", {"src": None, "src.audio_io": None}):
        with pytest.raises(EngineUnavailable):
            engine.ensure_ready()


def test_status_names_real_backup_without_claiming_stub_or_loading_it(monkeypatch, tmp_path):
    config = _mps(monkeypatch, OIDA_MOSS_AUDIO_REPO=str(tmp_path / "nowhere"),
                  OIDA_MOSS_BACKUP_MODEL="Backup-Model-1B")
    engine = build_engine(config)
    with patch.dict("sys.modules", {"src": None, "src.audio_io": None}), \
            patch.object(engine.fallback, "ensure_ready") as backup_ready:
        status = engine.runtime_status()
    assert status["falls_back_to_stub"] is False
    assert status["configured_backup_model"] == "Backup-Model-1B"
    assert status["configured_fallback_profile"] == "mac-mps"
    assert status["fallback_permitted_for_selection"] is True
    assert "unavailable" in status["primary_unavailable_reason"]
    backup_ready.assert_not_called()


def test_explicit_selection_status_agrees_with_no_substitution_policy(monkeypatch, tmp_path):
    from oida.reasoning.audio_selection import selector, use_audio_model
    from oida.reasoning.model_catalog import MODEL_SPECS

    config = _mps(monkeypatch, OIDA_MOSS_AUDIO_REPO=str(tmp_path / "nowhere"))
    engine = FallbackEngine(MpsMossEngine(config), StubMossEngine())
    selected = selector(next(spec for spec in MODEL_SPECS if spec.id == "instruct"))
    with patch.dict("sys.modules", {"src": None, "src.audio_io": None}):
        with use_audio_model(selected):
            status = engine.runtime_status()
        restored = engine.runtime_status()
    assert status["fallback_permitted_for_selection"] is False
    assert status["falls_back_to_stub"] is False
    assert restored["fallback_permitted_for_selection"] is True
    assert restored["falls_back_to_stub"] is True


def test_with_a_backup_the_substitution_is_recorded(monkeypatch, tmp_path):
    config = _mps(monkeypatch, OIDA_MOSS_BACKUP_MODEL="Backup-Model-1B")
    engine = build_engine(config)
    assert isinstance(engine, FallbackEngine)
    assert engine.backup_model == "Backup-Model-1B"
    assert engine.fallback.model_id_for_kind("instruct") == "Backup-Model-1B"

    class Failing(StubMossEngine):
        def generate(self, *args, **kwargs):
            raise EngineUnavailable("primary is down")

    from oida.recipes import get_recipe

    recorded = FallbackEngine(Failing(), StubMossEngine(), backup_model="Backup-Model-1B")
    result = recorded.generate("fixture.wav", "Describe", get_recipe("caption_dense").settings)
    assert result.unavailable_reason == "primary is down"
    marks = [p.get("substituted_for_unavailable_primary") for p in result.pass_provenance if isinstance(p, dict)]
    assert marks and marks[0]["backup_model"] == "Backup-Model-1B"
    assert marks[0]["reason"] == "primary is down"


def test_a_capture_failure_carries_its_real_cause():
    """It used to read 'source configuration and native format must match'
    whatever had actually failed, which once hid a missing FFmpeg."""
    import inspect

    from oida import source_api

    source = inspect.getsource(source_api)
    assert "acquisition or listening failed " in source
    assert "({type(exc).__name__}: {exc})" in source
    assert "LOGGER.exception(\"capture listen failed" in source
    assert "source configuration and native format must match" not in source
