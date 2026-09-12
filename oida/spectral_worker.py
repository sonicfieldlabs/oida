"""Isolated optional spectral worker. Only the installer provisions its environment."""

import json
import sys
from pathlib import Path


def compute(task, samples, rate):
    import numpy as np

    if (
        samples.ndim != 2
        or not 256 <= samples.shape[0] <= 16384
        or not 1 <= samples.shape[1] <= 2
    ):
        raise ValueError(
            "Optional worker requires 256..16384 samples and one or two channels"
        )
    if not np.isfinite(samples).all() or not 8000 <= rate <= 192000:
        raise ValueError("Invalid sampled input")
    if task == "nsgt":
        from nsgt import NSGT, LogScale

        # A bounded rectangular representation; no below-window resolution claim.
        fmin = max(50, rate * 8 / len(samples))
        transform = NSGT(
            LogScale(fmin, rate / 2, 48),
            rate,
            len(samples),
            real=True,
            matrixform=True,
            multichannel=True,
        )
        output = np.asarray(transform.forward(samples.T), dtype=np.complex128)
        settings = dict(
            window="NSGT LogScale 48 bands; real=True, matrixform=True",
            hop=1,
            fft_length=len(samples),
            scaling="NSGT native coefficients",
            boundary="periodic",
            f_min=fmin,
            f_max=rate / 2,
        )
        losses = [
            "Rectangular NSGT coefficients; frequency-dependent windows; no uniform STFT time grid.",
            "Support below f_min is not asserted; bin density does not extend observation time.",
        ]
    elif task == "kymatio":
        from kymatio.numpy import Scattering1D

        output = Scattering1D(J=6, shape=len(samples), Q=8, max_order=2)(samples.T)
        settings = dict(
            window="Kymatio Scattering1D J=6 Q=8 max_order=2",
            hop=64,
            fft_length=len(samples),
            scaling="modulus and averaging",
            boundary="Kymatio reflection padding",
            f_min=0,
            f_max=rate / 2,
        )
        losses = [
            "Modulus removes phase; temporal averaging and invariant coefficients lose waveform detail.",
            "Coefficient index is not a linear frequency axis.",
        ]
    else:
        raise ValueError("Unknown optional worker")
    if output.nbytes > 16 * 1024**2 or not np.isfinite(output).all():
        raise ValueError("Optional worker output exceeds budget or is nonfinite")
    return output, dict(settings=settings, losses=losses)


if __name__ == "__main__":
    import resource
    import numpy as np

    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (17 * 1024**2, 17 * 1024**2))
    task, source, rate, destination = sys.argv[1:]
    output, metadata = compute(task, np.load(source, allow_pickle=False), int(rate))
    np.save(destination, output, allow_pickle=False)
    Path(destination + ".json").write_text(json.dumps(metadata, allow_nan=False))
