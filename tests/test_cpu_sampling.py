"""A sampled generation on Apple's GPU draws its tokens on the CPU."""

from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from oida.cpu_sampling import CpuSampler, sample_on_cpu  # noqa: E402


def chosen(scores):
    kept = torch.isfinite(scores)
    assert kept.sum(dim=-1).tolist() == [1] * scores.shape[0]
    return scores.argmax(dim=-1).tolist()


def test_a_draw_leaves_one_token_and_it_is_among_the_top_k():
    torch.manual_seed(3)
    scores = torch.randn(1, 500)
    allowed = set(torch.topk(scores, 5)[1][0].tolist())
    sampler = CpuSampler(torch, temperature=0.7, top_k=5, top_p=1.0)
    seen = set()
    for _ in range(400):
        seen.update(chosen(sampler(None, scores.clone())))
    assert seen <= allowed
    assert len(seen) > 1
    assert sampler.draws == 400


def test_top_p_keeps_only_the_likely_tokens():
    scores = torch.full((1, 6), -20.0)
    scores[0, 2] = 5.0
    scores[0, 4] = 4.0
    sampler = CpuSampler(torch, temperature=1.0, top_k=0, top_p=0.9)
    seen = set()
    for _ in range(300):
        seen.update(chosen(sampler(None, scores.clone())))
    assert seen <= {2, 4}


def test_a_forced_token_stays_forced_and_rows_are_drawn_apart():
    scores = torch.full((2, 9), float("-inf"))
    scores[0, 7] = 0.0
    scores[1, 1] = 0.0
    sampler = CpuSampler(torch, temperature=0.7, top_k=50, top_p=1.0)
    assert chosen(sampler(None, scores)) == [7, 1]


def test_invalid_scores_are_never_drawn():
    scores = torch.zeros(1, 8)
    scores[0, 3] = float("nan")
    scores[0, 5] = float("-inf")
    sampler = CpuSampler(torch, temperature=1.0, top_k=0, top_p=1.0)
    seen = set()
    for _ in range(300):
        seen.update(chosen(sampler(None, scores.clone())))
    assert not seen & {3, 5}


def test_scores_with_nothing_left_are_returned_as_they_came():
    scores = torch.full((1, 4), float("-inf"))
    sampler = CpuSampler(torch, temperature=1.0, top_k=0, top_p=1.0)
    assert sampler(None, scores) is scores
    assert sampler.draws == 0


def test_only_a_model_on_mps_samples_on_the_cpu(monkeypatch):
    monkeypatch.delenv("OIDA_MPS_SAMPLE_ON_CPU", raising=False)
    assert sample_on_cpu(SimpleNamespace(device="mps:0"))
    assert not sample_on_cpu(SimpleNamespace(device="cuda:0"))
    assert not sample_on_cpu(SimpleNamespace(device="cpu"))
    monkeypatch.setenv("OIDA_MPS_SAMPLE_ON_CPU", "0")
    assert not sample_on_cpu(SimpleNamespace(device="mps:0"))


def test_a_temperature_of_zero_is_not_sampling():
    with pytest.raises(ValueError):
        CpuSampler(torch, temperature=0.0, top_k=50, top_p=1.0)
