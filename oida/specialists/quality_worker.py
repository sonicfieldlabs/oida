"""Isolated DNSMOS P.835 speech-quality inference (opt-in specialist lane).

Runs only from an admitted, hash-pinned deployment: the host's verify() gate
requires a checkpoint manifest, a license review, a validation receipt and a
measured memory profile before anything here executes. The lane estimates
speech quality (SIG/BAK/OVRL) and must never be presented as a universal
sound, music or aesthetic quality score — the evidence contract records the
speech-domain limitation explicitly.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time


def run(request):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    path = Path(request["path"])
    info = sf.info(path)
    if not 0 < info.duration <= 10.01 or info.channels > 8 or info.samplerate > 384000:
        raise ValueError(
            "Quality estimates require an already bounded excerpt of at most 10 seconds"
        )
    audio, original_rate = sf.read(path, dtype="float32", always_2d=True)
    if not np.isfinite(audio).all():
        raise ValueError("Nonfinite source audio")
    rate = 16000
    mono = audio.mean(axis=1)
    divisor = math.gcd(rate, original_rate)
    view = np.asarray(
        resample_poly(mono, rate // divisor, original_rate // divisor), dtype="<f4"
    )
    view_hash = hashlib.sha256(view.tobytes()).hexdigest()
    started = time.perf_counter()
    if not view.size or float(np.sqrt(np.mean(view.astype(np.float64) ** 2))) < 1e-5:
        return (
            {
                "status": "undetermined",
                "hypotheses": [],
                "limitations": [
                    "Near-silence provides no basis for a speech-quality estimate"
                ],
            },
            view,
            view_hash,
            time.perf_counter() - started,
            rate,
        )
    # The admitted single P.835 checkpoint produces SIG, BAK and OVRL.
    # https://github.com/microsoft/DNS-Challenge/blob/master/DNSMOS/dnsmos_local.py
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError(
            "The quality worker's pinned environment is missing"
        ) from exc

    def session():
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        return ort.InferenceSession(
            str(Path(request["checkpoint"])),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def window(view, size, step):
        if view.size < size:
            if not view.size:
                raise ValueError("Empty audio")
            padded = np.tile(view, math.ceil(size / view.size))[:size]
            yield 0.0, padded
            return
        for start in range(0, view.size - size + 1, step):
            yield start / rate, view[start : start + size]

    model = session()
    rows = []
    for offset, frame in window(view, 144160, 16000):
        begin = offset
        end = min(begin + 9.01, view.size / rate)
        chunk = chunk_view(np, frame)
        raw = np.asarray(model.run(None, {"input_1": chunk[None, :]})[0][0])
        if raw.shape != (3,) or not np.isfinite(raw).all():
            raise ValueError("Invalid P.835 checkpoint output")
        scores = dict(zip(("sig", "bak", "ovrl"), map(float, raw), strict=True))
        rows.append(
            dict(
                start_seconds=round(begin, 3),
                end_seconds=round(end, 3),
                sig=scores["sig"],
                bak=scores["bak"],
                ovrl=scores["ovrl"],
                score_kind="raw checkpoint estimates; no local calibration",
                applicability="undetermined; no independent speech-domain verification",
                domain_limitation="speech-domain estimate; not a music or aesthetic quality score",
            )
        )
    elapsed = time.perf_counter() - started
    result = dict(
        status="complete" if rows else "undetermined",
        hypotheses=rows,
        limitations=[
            "Speech-domain quality estimate; never a universal sound quality score",
            "Raw SIG, BAK and OVRL outputs; not calibrated MOS",
            "Short excerpts are repeated to the checkpoint's 9.01-second input",
        ],
    )
    return result, view, view_hash, elapsed, rate


def chunk_view(np, frame):
    # The P.835 ONNX input shape is 9.01 seconds at 16 kHz; keep the conversion
    # in one place so a changed checkpoint length fails loudly here.
    if frame.size != 144160:
        raise ValueError("P.835 expects 9.01-second windows at 16 kHz")
    return frame.astype(np.float32)


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    duration = _duration(request)
    result, view, view_hash, elapsed, rate = run(request)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    import hashlib as _hashlib

    source_sha = _hashlib.sha256(Path(request["path"]).read_bytes()).hexdigest()
    output = Path(sys.argv[2])
    output.write_text(
        json.dumps(
            dict(
                result=result,
                view_sha256=view_hash,
                duration_seconds=duration,
                sample_rate_hz=rate,
                source_sha256=source_sha,
                transformations=["mono", "resample-16k"],
                wall_seconds=elapsed,
                peak_memory_mib=peak
                / (1024 * 1024 if sys.platform == "darwin" else 1024),
            )
        )
    )


def _duration(request):
    import soundfile as sf

    return sf.info(request["path"]).duration


if __name__ == "__main__":
    main()
