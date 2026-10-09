"""Resolve a file's sampled representation separately from capture/model support."""

from __future__ import annotations

import hashlib
import math
import os
import stat
from io import BytesIO
from pathlib import Path

import soundfile as sf
from akouo_contract.apertures import aperture_decision


def read_source_bytes(path):
    """Read one operator-selected regular file, bounded before and after opening."""
    path = Path(path)
    budget = 96 * 1024**2
    if not path.is_file() or path.stat().st_size > budget:
        raise ValueError("Aperture source must be a regular file within 96 MiB")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > budget:
            raise ValueError("Aperture source must be a regular file within 96 MiB")
        payload = source.read(budget + 1)
    if len(payload) > budget:
        raise ValueError("Aperture source exceeds byte budget")
    return payload


def file_aperture(
    path,
    *,
    mode="centaur",
    bands_hz=None,
    claim_kind="digital_dsp",
    apparatus_resolver=None,
    source_bytes=None,
    window_s=None,
):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 96 * 1024**2:
        raise ValueError("Aperture source must be a regular file within 96 MiB")
    if source_bytes is None:
        source_bytes = read_source_bytes(path)
    if len(source_bytes) > 96 * 1024**2:
        raise ValueError("Aperture source exceeds byte budget")
    info = sf.info(BytesIO(source_bytes))
    if not info.frames or info.samplerate <= 0:
        raise ValueError("Empty or invalid sampled representation")
    subject = hashlib.sha256(source_bytes).hexdigest()
    duration = info.frames / info.samplerate
    window = [0, duration] if window_s is None else list(window_s)
    if (
        len(window) != 2
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in window)
        or not 0 <= window[0] < window[1] <= duration
    ):
        raise ValueError(
            "Aperture window must be finite and within the sampled representation"
        )
    nyquist = info.samplerate / 2
    if mode not in {"centaur", "human_reference", "beyond"}:
        raise ValueError("Unknown aperture mode")
    bands = (
        bands_hz
        if bands_hz is not None
        else (
            [[20, 20000]]
            if mode == "human_reference"
            else [[0, min(20, nyquist)]]
            + ([[20000, nyquist]] if nyquist > 20000 else [])
            if mode == "beyond"
            else [[0, nyquist]]
        )
    )
    request = dict(
        contract="akouo/aperture-request/v1",
        request_id="aperture:"
        + hashlib.sha256(
            repr((subject, mode, bands, claim_kind, window)).encode()
        ).hexdigest(),
        mode=mode,
        bands_hz=bands,
        subject_ref=subject,
        representation_ref="samples:" + subject,
        route_ref="oida:pyramid:v1",
        claim_kind=claim_kind,
        window_s=window,
    )
    known = dict(
        status="known",
        validated=True,
        bands_hz=[[0, nyquist]],
        window_s=request["window_s"],
    )
    route = {k: request[k] for k in ("subject_ref", "representation_ref", "route_ref")}
    route.update(
        sampled_representation=known,
        analysis=known,
        capture={"status": "unknown"},
        model_input={"status": "not_applicable"},
    )
    if apparatus_resolver is not None:
        # Evidence is resolved by the owner, never accepted from request JSON.
        resolved = apparatus_resolver(subject, request["representation_ref"])
        if resolved:
            for key in ("capture", "model_input"):
                if key in resolved:
                    route[key] = resolved[key]
    decision = aperture_decision(
        request,
        resolve_route=lambda ref: route if ref == request["route_ref"] else None,
    )
    return dict(
        request=request,
        requested_bands_hz=bands,
        effective_bands_hz=[
            entry["band_hz"]
            for entry in decision["bands"]
            if entry["support"] == "supported"
        ],
        decision=decision,
        source_sha256=subject,
        sample_rate=info.samplerate,
        channels=info.channels,
        samples=info.frames,
        capture=route["capture"],
        model_input=route["model_input"],
    )
