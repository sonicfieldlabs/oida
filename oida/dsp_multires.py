"""Bounded multiwindow measurements; every yielded level has explicit support."""

from __future__ import annotations

import math
import time
import numpy as np
from scipy.signal import ShortTimeFFT, windows

MAX_SCALARS = 24_000_000
MAX_WORK_BYTES = 256 * 1024**2
MAX_FRAMES = 2048
MAX_FFT = 262144
MAX_SECONDS = 60
MAX_WALL_SECONDS = 30


def pyramid(samples, rate, *, cancelled=lambda: False, clock=time.monotonic):
    """Yield one level at a time; callers must release arrays before advancing.

    Native axes are channel, frequency, time. Short windows do not establish
    low-frequency behaviour. Padding changes neither observed time nor resolution.
    """
    if not isinstance(samples, np.ndarray) or samples.ndim != 2:
        raise ValueError("Samples must have sample/channel axes")
    n, channels = samples.shape
    if (
        type(rate) is not int
        or not 8000 <= rate <= 192000
        or not 1 <= channels <= 2
        or not 2 <= n <= rate * MAX_SECONDS
        or samples.size > MAX_SCALARS
        or samples.nbytes > 96 * 1024**2
        or samples.dtype.kind != "f"
    ):
        raise ValueError("Input exceeds spectral admission limits")
    if not np.isfinite(samples).all():
        raise ValueError("Nonfinite audio")
    started = clock()

    def check():
        if cancelled():
            raise InterruptedError("Pyramid cancelled")
        if clock() - started > MAX_WALL_SECONDS:
            raise TimeoutError("Pyramid analysis deadline exceeded")

    sizes = sorted(
        {max(2, round(size * rate / 48000)) for size in (48, 1024, 8192, 65536)} | {n}
    )
    for length in sizes:
        check()
        if length > n or length > MAX_FFT:
            yield {
                "state": "omitted",
                "window_samples": length,
                "reason": "Window exceeds observed samples or FFT budget",
            }
            continue
        single = length == n
        hop = length if single else max(1, length // 2)
        frames = 1 if single else math.ceil(n / hop)
        bins = length // 2 + 1
        # complex128 output plus magnitude/power and per-chunk temporaries.
        estimate = channels * bins * frames * 32 + channels * length * 64
        if frames > MAX_FRAMES or estimate > MAX_WORK_BYTES:
            yield {
                "state": "omitted",
                "window_samples": length,
                "reason": "Frame or working-memory budget exceeded",
            }
            continue
        transform = ShortTimeFFT(
            windows.hann(length, sym=False),
            hop,
            rate,
            fft_mode="onesided",
            scale_to="magnitude",
            phase_shift=0,
        )
        output = np.empty((channels, bins, frames), dtype=np.complex128)
        for start in range(0, frames, 16):
            check()
            end = min(frames, start + 16)
            output[:, :, start:end] = transform.stft(
                samples.T, p0=start, p1=end, k_offset=length // 2 if single else 0
            )
        power = np.abs(output) ** 2
        frequency = transform.f
        ridges = frequency[np.argmax(power, axis=1)]
        energy = power.sum(axis=1)
        onset = np.maximum(0, np.diff(energy, axis=1, prepend=energy[:, :1]))
        yield {
            "state": "available",
            "window_samples": length,
            "hop": hop,
            "bin_spacing_hz": rate / length,
            "time_step_s": hop / rate,
            "observed_samples": n,
            "padding": "zeros",
            "truncation_samples": 0,
            "estimated_work_bytes": estimate,
            "complex": output,
            "ridge_hz": ridges,
            "onset_energy": onset,
            "band_power": {
                name: power[:, (frequency >= lo) & (frequency < hi), :].sum(axis=1)
                for name, lo, hi in [
                    ("below_human", 0, 20),
                    ("human_reference", 20, 20000),
                    ("above_human", 20000, rate / 2 + 1),
                ]
            },
            "limitations": [
                "Bin spacing is not universal uncertainty; edge windows include declared zero padding.",
                "Digital measurements do not establish capture bandwidth or audibility.",
            ],
        }
        del output, power, ridges, energy, onset
