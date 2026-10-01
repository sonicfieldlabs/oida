import numpy as np
import pytest
from oida.dsp_multires import pyramid


def test_digital_high_band_and_filtered_conversion():
    from scipy.signal import resample_poly

    rate = 192000
    t = np.arange(rate) / rate
    signal = (
        0.25 * np.sin(2 * np.pi * 1000 * t) + 0.25 * np.sin(2 * np.pi * 40000 * t)
    )[:, None]
    levels = [v for v in pyramid(signal, rate) if v["state"] == "available"]
    long = levels[-1]
    assert long["band_power"]["above_human"].sum() > 0.01
    filtered = resample_poly(signal, 1, 4, axis=0, window=("kaiser", 8.6))
    low = [v for v in pyramid(filtered, 48000) if v["state"] == "available"][-1]
    assert low["band_power"]["above_human"].sum() < 1e-8
    up = resample_poly(filtered, 4, 1, axis=0, window=("kaiser", 8.6))
    restored = [v for v in pyramid(up, rate) if v["state"] == "available"][-1]
    assert (
        restored["band_power"]["above_human"].sum()
        < long["band_power"]["above_human"].sum() * 1e-6
    )


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_silence_dc_stereo_short(value):
    levels = list(pyramid(np.full((64, 2), value), 48000))
    assert any(v["state"] == "omitted" for v in levels)
    for v in levels:
        if v["state"] == "available":
            assert v["complex"].shape[0] == 2
            assert np.isfinite(v["complex"]).all()


def test_nonfinite_cancel_and_time_budget():
    with pytest.raises(ValueError):
        list(pyramid(np.full((64, 1), np.nan), 48000))
    with pytest.raises(InterruptedError):
        list(pyramid(np.zeros((64, 1)), 48000, cancelled=lambda: True))
    ticks = iter([0, 31])
    with pytest.raises(TimeoutError):
        list(pyramid(np.zeros((64, 1)), 48000, clock=lambda: next(ticks)))


def test_off_bin_tone_noise_and_resource_omissions():
    rng = np.random.default_rng(27)
    rate = 48000
    samples = (
        np.sin(2 * np.pi * 1000.37 * np.arange(rate) / rate)
        + rng.normal(0, 0.001, rate)
    )[:, None]
    for level in pyramid(samples, rate):
        if level["state"] == "available" and level["window_samples"] == rate:
            assert abs(level["ridge_hz"][0, 0] - 1000.37) <= level["bin_spacing_hz"]
            assert np.isfinite(level["onset_energy"]).all()
    omitted = []
    for level in pyramid(np.zeros((192000 * 2, 2), dtype=np.float32), 192000):
        if level["state"] == "omitted":
            omitted.append(level["reason"])
        level.clear()
    assert any("FFT budget" in reason for reason in omitted)
    assert any("Frame" in reason for reason in omitted)
