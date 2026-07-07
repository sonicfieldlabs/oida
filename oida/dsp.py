from __future__ import annotations

import copy
import hashlib
import math
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import lfilter, resample_poly

HEAVY_ANALYSIS_MAX_SECONDS = 600
ONSET_SEGMENT_MAX_SECONDS = 60
SURVEY_WINDOW = 2048
SURVEY_MAX_FRAMES = 900
ONSET_WINDOW = 1024
ONSET_HOP = 512
ONSET_MIN_SEPARATION_SECONDS = 0.05
CLIP_THRESHOLD = 0.999


@dataclass
class AudioData:
    samples: np.ndarray
    sample_rate: int
    channels: int
    duration_s: float


@dataclass
class AudioFeatures:
    peakDbfs: float | None = None
    rmsDbfs: float | None = None
    crestFactorDb: float | None = None
    silenceRatio: float | None = None
    zeroCrossingRate: float | None = None
    dcOffset: float | None = None
    spectralCentroidHz: float | None = None
    spectralRolloffHz: float | None = None
    spectralFlatness: float | None = None
    spectralCentroidStdHz: float | None = None
    bandEnergy: dict[str, float] | None = None
    integratedLufs: float | None = None
    loudnessRangeLu: float | None = None
    onsetCount: int | None = None
    onsetDensityPerSec: float | None = None
    onsetTimes: list[float] | None = None
    bpmCandidate: float | None = None
    interChannelCorrelation: float | None = None
    stereoWidth: float | None = None
    channelBalanceDb: float | None = None
    clippedSampleRatio: float | None = None
    analyzedSeconds: float | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_audio(path: str | Path, target_sr: int | None = None, mono: bool = False, max_seconds: float | None = None) -> AudioData:
    audio_path = Path(path)
    frames = None
    if isinstance(max_seconds, (int, float)) and max_seconds > 0:
        frames = max(1, int(math.ceil(float(max_seconds) * _samplerate_hint(audio_path))))
    try:
        samples, sample_rate = sf.read(str(audio_path), frames=frames, always_2d=True, dtype="float32")
    except Exception:
        samples, sample_rate = _load_wave(audio_path, max_frames=frames)

    if samples.ndim == 1:
        samples = samples[:, None]

    if target_sr and sample_rate != target_sr:
        samples = _resample(samples, sample_rate, target_sr)
        sample_rate = target_sr

    if mono and samples.shape[1] > 1:
        samples = samples.mean(axis=1, keepdims=True)

    channels = int(samples.shape[1]) if samples.ndim == 2 else 1
    duration_s = float(samples.shape[0] / sample_rate) if sample_rate else 0.0
    return AudioData(samples=samples, sample_rate=int(sample_rate), channels=channels, duration_s=duration_s)


def _load_wave(path: Path, max_frames: int | None = None) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        frame_count = min(handle.getnframes(), max_frames) if max_frames is not None else handle.getnframes()
        frames = handle.readframes(frame_count)

    if sample_width == 1:
        raw = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        raw = (raw - 128.0) / 128.0
    elif sample_width == 2:
        raw = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 3:
        data = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        raw_int = data[:, 0].astype(np.int32) | (data[:, 1].astype(np.int32) << 8) | (data[:, 2].astype(np.int32) << 16)
        raw_int = np.where(raw_int & 0x800000, raw_int - 0x1000000, raw_int)
        raw = raw_int.astype(np.float32) / 8388608.0
    elif sample_width == 4:
        raw = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported WAV sample width: {sample_width}")

    return raw.reshape(-1, channels), sample_rate


def _resample(samples: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    gcd = math.gcd(source_sr, target_sr)
    up = target_sr // gcd
    down = source_sr // gcd
    return resample_poly(samples, up, down, axis=0).astype(np.float32)


def _samplerate_hint(path: Path) -> int:
    try:
        info = sf.info(str(path))
        if info.samplerate > 0:
            return int(info.samplerate)
    except Exception:
        pass
    try:
        with wave.open(str(path), "rb") as handle:
            return int(handle.getframerate())
    except Exception:
        return 48_000


_INSPECT_CACHE: dict[str, tuple[tuple[int, int], dict[str, object]]] = {}
_INSPECT_CACHE_MAX = 8


def inspect_path(path: str | Path) -> dict[str, object]:
    """Full-decode DSP inspection, memoized on (path, mtime_ns, size).

    One /listen-event otherwise decodes, analyzes, and sha256-hashes the same
    file two or three times (segment contract, report, live capture).
    """
    resolved = Path(path).expanduser().resolve()
    try:
        stat = resolved.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return _inspect_path_uncached(path)
    key = str(resolved)
    cached = _INSPECT_CACHE.get(key)
    if cached and cached[0] == stamp:
        return copy.deepcopy(cached[1])
    result = _inspect_path_uncached(path)
    if len(_INSPECT_CACHE) >= _INSPECT_CACHE_MAX:
        _INSPECT_CACHE.pop(next(iter(_INSPECT_CACHE)))
    _INSPECT_CACHE[key] = (stamp, copy.deepcopy(result))
    return result


def _inspect_path_uncached(path: str | Path) -> dict[str, object]:
    info = audio_info(path)
    audio = load_audio(path, max_seconds=HEAVY_ANALYSIS_MAX_SECONDS)
    features = analyze_audio(audio)
    return {
        "path": str(Path(path).expanduser().resolve()),
        "durationSeconds": info["durationSeconds"] if info else audio.duration_s,
        "sampleRate": info["sampleRate"] if info else audio.sample_rate,
        "channelCount": info["channelCount"] if info else audio.channels,
        "sha256": sha256_file(path),
        "features": clean_dict(asdict(features)),
    }


def audio_info(path: str | Path) -> dict[str, object] | None:
    try:
        info = sf.info(str(path))
    except Exception:
        return None
    duration = float(info.frames / info.samplerate) if info.samplerate > 0 and info.frames >= 0 else 0.0
    return {
        "durationSeconds": duration,
        "sampleRate": int(info.samplerate),
        "channelCount": int(info.channels),
    }


def analyze_audio(audio: AudioData) -> AudioFeatures:
    features = AudioFeatures()
    if audio.samples.size == 0 or audio.sample_rate <= 0:
        return features

    analyzed_length = min(audio.samples.shape[0], int(HEAVY_ANALYSIS_MAX_SECONDS * audio.sample_rate))
    if analyzed_length <= 0:
        return features
    features.analyzedSeconds = analyzed_length / audio.sample_rate

    # Amplitude-domain metrics (peak, clipping, DC, RMS, silence) MUST be computed on
    # every sample: a true full-scale transient or clipped sample can fall between
    # decimated indices, so decimating here silently reported -200 dBFS / 0.0 clipping
    # for any file longer than ~10 s. These are O(n) reductions and stay within the
    # HEAVY_ANALYSIS_MAX_SECONDS cap; the cost is negligible next to the FFT/onset work.
    sample_block = audio.samples[:analyzed_length]
    flat = np.ascontiguousarray(sample_block).reshape(-1)
    silence_threshold = 10 ** (-60 / 20)

    if flat.size:
        absolute = np.abs(flat)
        peak = float(absolute.max())
        rms = float(np.sqrt(np.mean(np.square(flat))))
        features.peakDbfs = to_dbfs(peak)
        features.rmsDbfs = to_dbfs(rms)
        features.crestFactorDb = features.peakDbfs - features.rmsDbfs
        features.silenceRatio = float(np.mean(absolute < silence_threshold))
        features.dcOffset = float(np.mean(flat))
        features.clippedSampleRatio = float(np.mean(absolute >= CLIP_THRESHOLD))
    features.zeroCrossingRate = contiguous_zero_crossing_rate(audio)

    survey = analyze_spectral_survey(audio, analyzed_length)
    features.spectralCentroidHz = survey["centroidMeanHz"]
    features.spectralCentroidStdHz = survey["centroidStdHz"]
    features.spectralRolloffHz = survey["rolloffMeanHz"]
    features.spectralFlatness = survey["flatnessMean"]
    features.bandEnergy = survey["bandEnergy"]

    loudness = analyze_loudness(audio, analyzed_length)
    features.integratedLufs = loudness["integratedLufs"]
    features.loudnessRangeLu = loudness["loudnessRangeLu"]

    onsets = analyze_onsets(audio)
    features.onsetCount = onsets["onsetCount"]
    features.onsetDensityPerSec = onsets["onsetDensityPerSec"]
    features.onsetTimes = onsets["onsetTimes"] if isinstance(onsets.get("onsetTimes"), list) else None
    features.bpmCandidate = onsets["bpmCandidate"]

    stereo = analyze_stereo(audio, analyzed_length)
    features.interChannelCorrelation = stereo["correlation"]
    features.stereoWidth = stereo["width"]
    features.channelBalanceDb = stereo["balanceDb"]
    return features


def contiguous_zero_crossing_rate(audio: AudioData) -> float | None:
    channel = audio.samples[:, 0]
    window_length = min(channel.shape[0], audio.sample_rate * 10)
    if window_length < 2:
        return None
    start = max(0, (channel.shape[0] - window_length) // 2)
    window = channel[start : start + window_length]
    previous = window[:-1]
    current = window[1:]
    crossings = np.logical_or(np.logical_and(previous < 0, current >= 0), np.logical_and(previous >= 0, current < 0))
    return float(np.count_nonzero(crossings) / (window_length / audio.sample_rate))


def analyze_spectral_survey(audio: AudioData, analyzed_length: int) -> dict[str, object]:
    empty = {
        "centroidMeanHz": None,
        "centroidStdHz": None,
        "rolloffMeanHz": None,
        "flatnessMean": None,
        "bandEnergy": None,
    }
    if analyzed_length < SURVEY_WINDOW:
        return empty

    channel = audio.samples[:analyzed_length, 0]
    frame_count = min(SURVEY_MAX_FRAMES, analyzed_length // SURVEY_WINDOW)
    if frame_count < 1:
        return empty

    window = np.hanning(SURVEY_WINDOW).astype(np.float32)
    freqs = np.fft.rfftfreq(SURVEY_WINDOW, 1.0 / audio.sample_rate)
    usable = slice(1, None)
    centroids: list[float] = []
    rolloffs: list[float] = []
    flatnesses: list[float] = []
    band_sums = {"sub": 0.0, "bass": 0.0, "lowMid": 0.0, "mid": 0.0, "high": 0.0, "air": 0.0}
    band_total = 0.0

    starts = [0] if frame_count == 1 else [int(frame * (analyzed_length - SURVEY_WINDOW) / (frame_count - 1)) for frame in range(frame_count)]
    for start in starts:
        frame = channel[start : start + SURVEY_WINDOW] * window
        spectrum = np.fft.rfft(frame)
        magnitudes = np.abs(spectrum)[usable]
        bin_freqs = freqs[usable]
        magnitude_sum = float(np.sum(magnitudes))
        if magnitude_sum <= 0:
            continue

        centroids.append(float(np.sum(bin_freqs * magnitudes) / magnitude_sum))
        cumulative = np.cumsum(magnitudes)
        rolloff_index = int(np.searchsorted(cumulative, magnitude_sum * 0.85, side="left"))
        rolloffs.append(float(bin_freqs[min(rolloff_index, len(bin_freqs) - 1)]))

        arithmetic_mean = float(np.mean(magnitudes))
        if arithmetic_mean > 0:
            flatnesses.append(float(np.exp(np.mean(np.log(magnitudes + 1e-12))) / arithmetic_mean))

        energy = magnitudes * magnitudes
        band_total += float(np.sum(energy))
        band_sums["sub"] += float(np.sum(energy[bin_freqs < 60]))
        band_sums["bass"] += float(np.sum(energy[(bin_freqs >= 60) & (bin_freqs < 250)]))
        band_sums["lowMid"] += float(np.sum(energy[(bin_freqs >= 250) & (bin_freqs < 1000)]))
        band_sums["mid"] += float(np.sum(energy[(bin_freqs >= 1000) & (bin_freqs < 4000)]))
        band_sums["high"] += float(np.sum(energy[(bin_freqs >= 4000) & (bin_freqs < 10000)]))
        band_sums["air"] += float(np.sum(energy[bin_freqs >= 10000]))

    if not centroids:
        return empty
    return {
        "centroidMeanHz": float(np.mean(centroids)),
        "centroidStdHz": float(np.std(centroids)) if len(centroids) > 1 else None,
        "rolloffMeanHz": float(np.mean(rolloffs)) if rolloffs else None,
        "flatnessMean": float(np.mean(flatnesses)) if flatnesses else None,
        "bandEnergy": {key: value / band_total for key, value in band_sums.items()} if band_total > 0 else None,
    }


def analyze_loudness(audio: AudioData, analyzed_length: int) -> dict[str, float | None]:
    sample_rate = audio.sample_rate
    chunk_samples = round(sample_rate / 10)
    chunk_count = analyzed_length // chunk_samples if chunk_samples else 0
    if chunk_count < 4:
        return {"integratedLufs": None, "loudnessRangeLu": None}

    chunk_energy = np.zeros(chunk_count, dtype=np.float64)
    shelf = k_weighting_shelf_coefficients(sample_rate)
    highpass = k_weighting_highpass_coefficients(sample_rate)

    for channel_index in range(audio.channels):
        channel = audio.samples[: analyzed_length, channel_index]
        stage1 = _biquad_filter(channel, shelf)
        stage2 = _biquad_filter(stage1, highpass)
        usable = stage2[: chunk_count * chunk_samples].reshape(chunk_count, chunk_samples)
        chunk_energy += np.sum(usable * usable, axis=1)

    def block_loudness(mean_square: float) -> float:
        return -0.691 + 10 * math.log10(mean_square + 1e-15)

    momentary = []
    for block in range(0, chunk_count - 3):
        energy = float(np.sum(chunk_energy[block : block + 4]))
        mean_square = energy / (4 * chunk_samples)
        momentary.append({"loudness": block_loudness(mean_square), "meanSquare": mean_square})

    integrated_lufs = None
    abs_gated = [block for block in momentary if block["loudness"] > -70]
    if abs_gated:
        abs_mean = sum(block["meanSquare"] for block in abs_gated) / len(abs_gated)
        relative_threshold = block_loudness(abs_mean) - 10
        rel_gated = [block for block in abs_gated if block["loudness"] > relative_threshold]
        if rel_gated:
            rel_mean = sum(block["meanSquare"] for block in rel_gated) / len(rel_gated)
            integrated_lufs = block_loudness(rel_mean)

    loudness_range = None
    short_term = []
    for block in range(0, chunk_count - 29, 10):
        energy = float(np.sum(chunk_energy[block : block + 30]))
        mean_square = energy / (30 * chunk_samples)
        short_term.append({"loudness": block_loudness(mean_square), "meanSquare": mean_square})

    st_abs_gated = [block for block in short_term if block["loudness"] > -70]
    if len(st_abs_gated) >= 2:
        st_mean = sum(block["meanSquare"] for block in st_abs_gated) / len(st_abs_gated)
        st_threshold = block_loudness(st_mean) - 20
        st_gated = sorted(block["loudness"] for block in st_abs_gated if block["loudness"] > st_threshold)
        if len(st_gated) >= 2:
            loudness_range = percentile(st_gated, 0.95) - percentile(st_gated, 0.10)

    return {"integratedLufs": integrated_lufs, "loudnessRangeLu": loudness_range}


def analyze_onsets(audio: AudioData) -> dict[str, object]:
    empty: dict[str, object] = {"onsetCount": None, "onsetDensityPerSec": None, "onsetTimes": None, "bpmCandidate": None}
    channel = audio.samples[:, 0]
    segment_length = min(channel.shape[0], int(ONSET_SEGMENT_MAX_SECONDS * audio.sample_rate))
    if segment_length < ONSET_WINDOW * 4:
        return empty
    segment_start = max(0, (channel.shape[0] - segment_length) // 2)
    segment = channel[segment_start : segment_start + segment_length]
    frame_count = ((segment_length - ONSET_WINDOW) // ONSET_HOP) + 1
    if frame_count < 8:
        return empty

    window = np.hanning(ONSET_WINDOW).astype(np.float32)
    previous_magnitudes: np.ndarray | None = None
    flux = np.zeros(frame_count, dtype=np.float64)
    for frame_index in range(frame_count):
        start = frame_index * ONSET_HOP
        frame = segment[start : start + ONSET_WINDOW] * window
        magnitudes = np.abs(np.fft.rfft(frame))
        if previous_magnitudes is not None:
            rises = magnitudes - previous_magnitudes
            flux[frame_index] = float(np.sum(rises[rises > 0]))
        previous_magnitudes = magnitudes

    flux_mean = float(np.mean(flux[1:]))
    flux_std = float(np.std(flux[1:]))
    if flux_std == 0:
        return {"onsetCount": 0, "onsetDensityPerSec": 0.0, "onsetTimes": [], "bpmCandidate": None}

    threshold = flux_mean + flux_std
    min_separation_frames = max(1, round((ONSET_MIN_SEPARATION_SECONDS * audio.sample_rate) / ONSET_HOP))
    onset_times: list[float] = []
    last_onset_frame = -min_separation_frames
    for frame in range(2, frame_count - 1):
        if (
            flux[frame] > threshold
            and flux[frame] >= flux[frame - 1]
            and flux[frame] >= flux[frame + 1]
            and frame - last_onset_frame >= min_separation_frames
        ):
            onset_times.append((segment_start + frame * ONSET_HOP + ONSET_WINDOW / 2) / audio.sample_rate)
            last_onset_frame = frame

    segment_seconds = segment_length / audio.sample_rate
    bpm_candidate = None
    if len(onset_times) >= 8:
        intervals = sorted(onset_times[index] - onset_times[index - 1] for index in range(1, len(onset_times)))
        median_interval = percentile(intervals, 0.5)
        mean_interval = sum(intervals) / len(intervals)
        cv = (math.sqrt(sum((interval - mean_interval) ** 2 for interval in intervals) / len(intervals)) / mean_interval) if mean_interval > 0 else 1
        if median_interval > 0 and cv < 0.25:
            bpm = 60 / median_interval
            while bpm < 70:
                bpm *= 2
            while bpm > 180:
                bpm /= 2
            bpm_candidate = bpm

    return {
        "onsetCount": len(onset_times),
        "onsetDensityPerSec": len(onset_times) / segment_seconds,
        "onsetTimes": [round(value, 4) for value in onset_times],
        "bpmCandidate": bpm_candidate,
    }


def analyze_stereo(audio: AudioData, analyzed_length: int) -> dict[str, float | None]:
    if audio.channels < 2:
        return {"correlation": None, "width": None, "balanceDb": None}
    step = max(1, analyzed_length // 500000)
    left = audio.samples[:analyzed_length:step, 0]
    right = audio.samples[:analyzed_length:step, 1]
    n = left.shape[0]
    if n == 0:
        return {"correlation": None, "width": None, "balanceDb": None}
    sum_l = float(np.sum(left))
    sum_r = float(np.sum(right))
    sum_ll = float(np.sum(left * left))
    sum_rr = float(np.sum(right * right))
    sum_lr = float(np.sum(left * right))
    covariance = n * sum_lr - sum_l * sum_r
    variance_l = n * sum_ll - sum_l * sum_l
    variance_r = n * sum_rr - sum_r * sum_r
    denominator = math.sqrt(max(variance_l * variance_r, 0.0))
    mid = (left + right) / 2
    side = (left - right) / 2
    mid_energy = float(np.sum(mid * mid))
    side_energy = float(np.sum(side * side))
    return {
        "correlation": covariance / denominator if denominator > 0 else None,
        "width": side_energy / (mid_energy + side_energy) if mid_energy + side_energy > 0 else None,
        "balanceDb": 10 * math.log10(sum_ll / sum_rr) if sum_ll > 0 and sum_rr > 0 else None,
    }


def k_weighting_shelf_coefficients(sample_rate: int) -> dict[str, float]:
    f0 = 1681.974450955533
    gain_db = 3.999843853973347
    q = 0.7071752369554196
    k = math.tan((math.pi * f0) / sample_rate)
    vh = 10 ** (gain_db / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    return {
        "b0": (vh + (vb * k) / q + k * k) / a0,
        "b1": (2 * (k * k - vh)) / a0,
        "b2": (vh - (vb * k) / q + k * k) / a0,
        "a1": (2 * (k * k - 1)) / a0,
        "a2": (1 - k / q + k * k) / a0,
    }


def k_weighting_highpass_coefficients(sample_rate: int) -> dict[str, float]:
    f0 = 38.13547087602444
    q = 0.5003270373238773
    k = math.tan((math.pi * f0) / sample_rate)
    a0 = 1 + k / q + k * k
    return {
        "b0": 1 / a0,
        "b1": -2 / a0,
        "b2": 1 / a0,
        "a1": (2 * (k * k - 1)) / a0,
        "a2": (1 - k / q + k * k) / a0,
    }


def _biquad_filter(samples: np.ndarray, coeffs: dict[str, float]) -> np.ndarray:
    # Direct-form recursion via scipy.signal.lfilter (a0 already normalized to 1).
    # Bit-equivalent to the manual per-sample loop but vectorized in C, which removes
    # ~16 s of pure-Python overhead on a 10-minute stereo file.
    b = [coeffs["b0"], coeffs["b1"], coeffs["b2"]]
    a = [1.0, coeffs["a1"], coeffs["a2"]]
    return lfilter(b, a, np.asarray(samples, dtype=np.float64))


def percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def to_dbfs(value: float) -> float:
    return 20 * math.log10(value or 1e-10)


def clean_dict(value: object) -> object:
    if isinstance(value, dict):
        return {key: clean_dict(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [clean_dict(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value
