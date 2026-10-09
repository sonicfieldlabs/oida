#!/usr/bin/env python3
"""Offline full-weight MPS profiling; synthetic audio, bounded decoding, no policy mutation."""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import threading
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory


def routed_generate(router, target, requested_id, audio_path, settings):
    """Check selection receipts on a real router; do not infer identity from text."""
    from oida.engine_base import use_listening_model
    from oida.reasoning.audio_selection import selector, use_audio_model
    from oida.reasoning.model_catalog import find_model_spec

    requested = selector(find_model_spec("oida_moss", requested_id))
    expected = selector(find_model_spec("oida_moss", str(target)))
    with use_listening_model(str(target)), use_audio_model(requested):
        result = router.generate(
            str(audio_path), "Describe the sounds briefly.", settings
        )
    if not result.pass_provenance:
        raise RuntimeError("Routed inference did not return pass provenance")
    for receipt in result.pass_provenance:
        if (
            receipt.get("requested_audio_model") != requested.model_dump()
            or receipt.get("actual_audio_model") != expected.model_dump()
        ):
            raise RuntimeError("Routed inference selection receipt mismatch")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--moss-repo", type=Path, required=True)
    parser.add_argument("--instruct", type=Path, required=True)
    parser.add_argument("--thinking", type=Path, required=True)
    parser.add_argument("--seconds", nargs="+", type=float, default=[1, 10, 30, 45])
    parser.add_argument("--tokens", type=int, default=64)
    parser.add_argument(
        "--kinds",
        nargs="+",
        choices=["instruct", "thinking"],
        default=["instruct", "thinking"],
    )
    parser.add_argument("--check-runtime-only", action="store_true")
    parser.add_argument(
        "--routed-selections",
        action="store_true",
        help="Exercise configured aliases and canonical IDs through the local router",
    )
    args = parser.parse_args()
    if (
        any(not 0 < value <= 45 for value in args.seconds)
        or not 1 <= args.tokens <= 256
    ):
        parser.error(
            "profiling is bounded to 45-second inputs and 256 generated tokens"
        )
    for path in (args.moss_repo, args.instruct, args.thinking):
        if not path.is_dir():
            parser.error("local code and both checkpoint directories are required")
    os.environ.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false"
    )
    import numpy as np
    import soundfile as sf
    import torch
    import transformers
    from oida.config import load_config
    from oida.engine_mps import MpsMossEngine
    from oida.recipes import GenerationSettings

    if not torch.backends.mps.is_available():
        raise RuntimeError("This hardware profile requires an available MPS device")
    config = replace(
        load_config(profile="mac-mps"),
        moss_audio_repo=args.moss_repo.resolve(),
        instruct_model=str(args.instruct.resolve()),
        thinking_model=str(args.thinking.resolve()),
        resident_mode="single",
        allow_hf_hub=False,
        hf_hub_offline=True,
        prewarm=False,
    )
    engine = MpsMossEngine(config)
    report = dict(
        contract="oida/moss-hardware-profile/v1",
        platform=platform.system(),
        architecture=platform.machine(),
        torch=torch.__version__,
        transformers=transformers.__version__,
        device="mps",
        resident_mode="single",
        concurrency=1,
        generated_token_cap=args.tokens,
        execution_path="local-router-selections"
        if args.routed_selections
        else "adapter",
        workload="deterministic synthetic tones/noise; not semantic or physical-capture qualification",
        mps_memory_basis="sampled at 50 ms; transient peaks may be missed; driver and tensor bytes overlap",
        rss_basis="process lifetime high-water RSS; not additive with unified MPS memory",
        recommended_mps_bytes=torch.mps.recommended_max_memory(),
        rows=[],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    def measure(label, function):
        stop = threading.Event()
        samples = []

        def sample():
            samples.append(
                (
                    torch.mps.current_allocated_memory(),
                    torch.mps.driver_allocated_memory(),
                )
            )

        def watch():
            while not stop.wait(0.05):
                sample()

        torch.mps.synchronize()
        sample()
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        start = time.perf_counter()
        row = dict(label=label)
        try:
            value = function()
            torch.mps.synchronize()
            row["status"] = "complete"
            if value is not None:
                row.update(
                    decoded_output_chars=len(value.text)
                    + len(value.reasoning_trace or ""),
                    pass_provenance=value.pass_provenance,
                )
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            row["wall_seconds"] = time.perf_counter() - start
            stop.set()
            watcher.join(timeout=2)
            sample()
            row.update(
                tensor_peak_bytes=max(s[0] for s in samples),
                driver_peak_bytes=max(s[1] for s in samples),
                tensor_end_bytes=samples[-1][0],
                driver_end_bytes=samples[-1][1],
                rss_lifetime_peak_bytes=resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
                loaded_models=engine.runtime_status()["loaded_models"],
            )
            report["rows"].append(row)
            save()
            print(
                json.dumps({k: v for k, v in row.items() if k != "pass_provenance"}),
                flush=True,
            )

    try:
        with TemporaryDirectory(prefix="oida-hardware-input-") as temporary:
            from src.audio_io import load_audio

            router = None
            if args.routed_selections:
                from oida.reasoning.audio_router import RoutedAudioEngine
                from oida.reasoning.settings import ReasoningSettingsStore
                from oida.reasoning.secrets import EnvironmentSecretStore

                class NoNetwork:
                    def post_json(self, *args, **kwargs):
                        raise RuntimeError(
                            "External transport forbidden in local qualification"
                        )

                router = RoutedAudioEngine(
                    engine,
                    settings_store=ReasoningSettingsStore(
                        Path(temporary) / "reasoning.json"
                    ),
                    secret_store=EnvironmentSecretStore({}),
                    transport=NoNetwork(),
                )
            probe = Path(temporary) / "decoder-probe.wav"
            sf.write(probe, np.zeros(1600, dtype=np.float32), 16000)

            def check_decoder():
                decoded = load_audio(str(probe), sample_rate=16000)
                if len(decoded) != 1600:
                    raise RuntimeError(
                        "audio decoder preflight returned the wrong length"
                    )

            measure("decoder-preflight", check_decoder)
            if args.check_runtime_only:
                return
            for kind in args.kinds:
                measure(
                    kind + ":load-or-switch", lambda kind=kind: engine.prewarm(kind)
                )
                settings = GenerationSettings(
                    kind, 0.0 if kind == "instruct" else 1.0, 1.0, 50, args.tokens
                )
                for seconds in args.seconds:
                    rate = 48000
                    t = np.arange(round(rate * seconds), dtype=np.float32) / rate
                    audio = (
                        0.04
                        * np.sin(2 * np.pi * 440 * t)
                        * (np.sin(2 * np.pi * 2 * t) > 0)
                    )
                    audio += (
                        np.random.default_rng(7)
                        .normal(0, 0.003, len(t))
                        .astype(np.float32)
                    )
                    path = Path(temporary) / "fixture.wav"
                    sf.write(path, audio, rate, subtype="PCM_16")
                    if router is not None:
                        from oida.reasoning.model_catalog import find_model_spec

                        target = (
                            args.instruct.resolve()
                            if kind == "instruct"
                            else args.thinking.resolve()
                        )
                        canonical = find_model_spec("oida_moss", str(target))
                        if canonical is None:
                            raise RuntimeError("Checkpoint has no catalogue identity")
                        for requested_id in (kind, canonical.id):
                            measure(
                                f"{kind}:{seconds:g}s:{requested_id}",
                                lambda requested_id=requested_id: routed_generate(
                                    router, target, requested_id, path, settings
                                ),
                            )
                    else:
                        measure(
                            f"{kind}:{seconds:g}s",
                            lambda: engine.generate(
                                str(path), "Describe the sounds briefly.", settings
                            ),
                        )
            if len(args.kinds) > 1:
                measure(
                    args.kinds[0] + ":reload", lambda: engine.prewarm(args.kinds[0])
                )
    finally:
        measure("release", lambda: engine._clear_loaded_models(except_model=None))


if __name__ == "__main__":
    main()
