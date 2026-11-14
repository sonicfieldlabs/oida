"""Draw each new token on the CPU when the model runs on Apple's GPU.

`torch.multinomial` on the mps device can return a token whose probability is zero. Measured on
12 November 2026 with torch 2.13.0: 139 of 30,000 draws from a distribution that top-k had left
with 50 tokens landed on one of the other 151,886; torch 2.10.0 gave none. In a caption that is
one word from anywhere in the vocabulary inside an otherwise readable sentence, and the caption
check then declines the whole account: 15 of 100 sampled passes on test audio, none of 100 with
the draw on the CPU.

`CpuSampler` is the last logits processor of a sampled generation. It applies the temperature,
top-k and top-p the generation asked for, draws the token on the CPU, and hands back scores in
which only that token is left, so the generation itself runs greedily and picks it. The scores
arrive after every other processor, so a token another processor forced stays forced.
"""

from __future__ import annotations

import os


def sample_on_cpu(model) -> bool:
    """Whether a sampled generation on this model draws its tokens on the CPU."""
    if not str(getattr(model, "device", "")).startswith("mps"):
        return False
    return os.getenv("OIDA_MPS_SAMPLE_ON_CPU", "1").strip().lower() not in {"0", "false", "no", "off"}


class CpuSampler:
    def __init__(self, torch, *, temperature: float, top_k: int | None, top_p: float | None):
        if temperature <= 0:
            raise ValueError("sampling needs a temperature above zero")
        self.torch = torch
        self.temperature = float(temperature)
        self.top_k = int(top_k) if top_k else 0
        self.top_p = float(top_p) if top_p is not None else 1.0
        self.draws = 0

    def __call__(self, input_ids, scores):
        torch = self.torch
        low = float("-inf")
        logits = scores.detach().to("cpu", torch.float32)
        logits = torch.nan_to_num(logits, nan=low, posinf=torch.finfo(torch.float32).max, neginf=low)
        logits = logits / self.temperature
        if 0 < self.top_k < logits.shape[-1]:
            kth = torch.topk(logits, self.top_k)[0][..., -1, None]
            logits = logits.masked_fill(logits < kth, low)
        if self.top_p < 1.0:
            # As transformers' top-p: drop the least likely tokens whose probabilities together
            # stay within 1 - top_p, and always keep the most likely one.
            ordered, order = torch.sort(logits, descending=False)
            drop = ordered.softmax(dim=-1).cumsum(dim=-1) <= (1 - self.top_p)
            drop[..., -1:] = False
            logits = logits.masked_fill(drop.scatter(1, order, drop), low)
        probabilities = torch.softmax(logits, dim=-1)
        if not bool(torch.isfinite(probabilities).all()):
            # No token is left with a finite score; leave the choice to the generation.
            return scores
        chosen = torch.multinomial(probabilities, num_samples=1)
        self.draws += 1
        only = torch.full_like(scores, low)
        only.scatter_(1, chosen.to(scores.device), 0.0)
        return only
