"""Isolated CPU inference. Invoked by the owner with verified, local artifacts only."""

from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time


def run(request):
    import os
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    import torch

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)

    def no_download(*a, **k):
        raise RuntimeError("Model downloads are provisioning-only")

    torch.hub.load_state_dict_from_url = no_download
    path = Path(request["path"])
    info = sf.info(path)
    if not 0 < info.duration <= 10.01 or info.channels > 8 or info.samplerate > 384000:
        raise ValueError(
            "Specialists require an already bounded excerpt of at most 10 seconds"
        )
    audio, original_rate = sf.read(path, dtype="float32", always_2d=True)
    if not np.isfinite(audio).all():
        raise ValueError("Nonfinite source audio")
    rate = 32000 if request["task"] == "tag_events" else 22050
    mono = audio.mean(axis=1)
    divisor = math.gcd(rate, original_rate)
    view = np.asarray(
        resample_poly(mono, rate // divisor, original_rate // divisor), dtype="<f4"
    )
    view_hash = hashlib.sha256(view.tobytes()).hexdigest()
    started = time.perf_counter()
    if request["task"] == "tag_events":
        sys.path.insert(0, request["repository"])
        os.chdir(request["repository"])
        from models.mn.model import get_model
        from models.preprocess import AugmentMelSTFT

        model = get_model(width_mult=0.4, pretrained_name=None)
        model.load_state_dict(
            torch.load(request["checkpoint"], map_location="cpu", weights_only=True)
        )
        model.eval()
        mel = AugmentMelSTFT(n_mels=128, sr=rate, win_length=800, hopsize=320).eval()
        with torch.inference_mode():
            processor_input = mel(torch.from_numpy(view[None, :])).unsqueeze(0)
            logits, _ = model(processor_input)
        scores = torch.sigmoid(logits.float()).squeeze().numpy()
        with open(
            Path(request["repository"]) / "metadata/class_labels_indices.csv"
        ) as stream:
            labels = list(csv.DictReader(stream))
        candidates = [
            dict(
                label=labels[i]["display_name"],
                ontology_id=labels[i]["mid"],
                score=float(scores[i]),
            )
            for i in np.argsort(scores)[-10:][::-1]
        ]
        silent = float(np.sqrt(np.mean(view**2))) < 1e-5
        result = dict(
            status="undetermined" if silent else "hypotheses",
            labels=[] if silent else candidates,
            ontology="AudioSet",
            scope="whole excerpt",
            threshold_calibrated=False,
        )
        limitations = [
            "Uncalibrated class scores; top labels are hypotheses, not exhaustive events or source identities."
        ]
    elif request["task"] == "track_beats":
        from beat_this.inference import Audio2Frames
        from beat_this.model.postprocessor import Postprocessor

        model = Audio2Frames(checkpoint_path=request["checkpoint"], device="cpu")
        with torch.inference_mode():
            processor_input = model.signal2spect(view, rate)
            beats_logits, down_logits = model.spect2frames(processor_input)
            beats, downbeats = Postprocessor(type="minimal")(beats_logits, down_logits)
        beats = [float(x) for x in beats if 0 <= x < info.duration]
        downbeats = [float(x) for x in downbeats if 0 <= x < info.duration]
        max_activation = float(torch.sigmoid(beats_logits).max())
        uncertain = (
            float(np.sqrt(np.mean(view**2))) < 1e-5
            or len(beats) < 3
            or max_activation < 0.5
        )
        result = dict(
            status="undetermined" if uncertain else "hypotheses",
            beats_seconds=[] if uncertain else beats,
            downbeats_seconds=[] if uncertain else downbeats,
            max_beat_activation=max_activation,
            abstention_rule="silence, fewer than three beat candidates, or max activation below 0.5",
            threshold_calibrated=False,
        )
        limitations = [
            "Beat/downbeat hypotheses; no meter or tempo imposed. Abstention heuristic is not calibrated for all beatless or polymetric music."
        ]
    else:
        raise ValueError("Unsupported specialist task")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    return dict(
        processor_view=dict(
            kind="log-mel features",
            shape=list(processor_input.shape),
            dtype="float32 little-endian",
            sha256=hashlib.sha256(
                np.asarray(processor_input.cpu(), dtype="<f4").tobytes()
            ).hexdigest(),
        ),
        result=result,
        limitations=limitations,
        sample_rate_hz=rate,
        channels=1,
        view_sha256=view_hash,
        duration_seconds=info.duration,
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        transformations=[
            f"arithmetic mono mix from {info.channels} channels",
            f"scipy resample_poly {original_rate} to {rate} Hz",
            "float32 little-endian",
        ],
        peak_memory_mib=peak,
        wall_seconds=time.perf_counter() - started,
    )


if __name__ == "__main__":
    import contextlib

    request = json.loads(Path(sys.argv[1]).read_text())
    with contextlib.redirect_stdout(sys.stderr):
        value = run(request)
    Path(sys.argv[2]).write_text(json.dumps(value, allow_nan=False))
