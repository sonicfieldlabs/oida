"""Unqualified research entries cannot self-enable through configuration."""
import pytest
from oida.reasoning.model_catalog import MODEL_SPECS, find_model_spec
from oida.reasoning.audio_selection import selector, resolve
from oida.reasoning.budget import quote_cost


@pytest.mark.parametrize("spec", [s for s in MODEL_SPECS if s.provider_id in {"stepfun", "groq"} or "MOSS-Music" in s.id])
def test_unqualified_specialists_are_catalog_only(spec):
    assert not spec.selectable
    with pytest.raises(ValueError):
        resolve(selector(spec))
    assert spec.revision is None
    if spec.provider_id in {"stepfun", "groq"}:
        assert quote_cost(spec.provider_id, spec.id, audio_seconds=10.0, max_output_tokens=128) is None


def test_whisper_stays_transcription_only():
    assert find_model_spec("groq", "whisper-large-v3-turbo").roles == ("transcription",)
