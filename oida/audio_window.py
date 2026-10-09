"""A bounded slice of an owner file, preserving native rate and channels."""

from contextlib import contextmanager
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import soundfile as sf

from oida.dsp import sha256_file
from oida.operation_control import checkpoint
from oida.source_capture import MAX_AUDIO_BYTES


@contextmanager
def audio_window(path: Path, *, start_seconds: float, seconds: float, temp_dir: Path):
    if not math.isfinite(start_seconds) or not math.isfinite(seconds) or start_seconds < 0 or not 0 < seconds <= 60:
        raise ValueError("File windows require a finite start and duration of at most 60 seconds")
    source_hash = sha256_file(path)
    with sf.SoundFile(path) as audio:
        start = int(start_seconds * audio.samplerate)
        frames = min(int(seconds * audio.samplerate), audio.frames - start)
        if frames <= 0:
            raise ValueError(
                "The selected file window is empty; rewind the source player"
            )
        if frames * audio.channels * 4 > MAX_AUDIO_BYTES:
            raise ValueError("File window exceeds the native PCM capture budget")
        source_rate = audio.samplerate
        source_duration = audio.frames / source_rate
        audio.seek(start)
        samples = audio.read(frames, dtype="float32", always_2d=True)
    metadata = dict(
        source_sha256=source_hash,
        start_seconds=start / source_rate,
        duration_seconds=len(samples) / source_rate,
        source_duration_seconds=source_duration,
    )
    if sha256_file(path) != source_hash:
        raise ValueError("Source changed during window capture")
    checkpoint()
    temp_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="file-window-", dir=temp_dir) as directory:
        window = Path(directory) / "window.wav"
        sf.write(window, samples, source_rate, subtype="FLOAT")
        yield window, metadata
