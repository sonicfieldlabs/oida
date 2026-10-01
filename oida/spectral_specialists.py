"""Hash-admitted optional workers; off by default, one bounded child at a time."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import time
import numpy as np
from oida.operation_control import checkpoint


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recover_temporary():
    for path in Path(tempfile.gettempdir()).glob('oida-spectral-worker-*'):
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            pid = int(path.name.split('-')[3])
            os.kill(pid, 0)
        except ProcessLookupError:
            shutil.rmtree(path)
        except (ValueError, PermissionError, IndexError):
            continue


def admission():
    config = os.environ.get("OIDA_SPECTRAL_WORKERS_CONFIG")
    if not config:
        return (
            None,
            "Optional spectral workers are off; provision through the installer",
        )
    try:
        path = Path(config)
        if path.stat().st_size > 2 * 1024**2:
            raise ValueError("Manifest too large")
        value = json.loads(path.read_text())
        worker = Path(__file__).with_name("spectral_worker.py")
        if value["contract"] != "oida/spectral-workers/v1" or not value["enabled"]:
            raise ValueError("Workers disabled")
        if value["worker_sha256"] != sha(worker) or value["python_sha256"] != sha(
            Path(value["python"]).resolve()
        ):
            raise ValueError("Worker or interpreter changed")
        if not value["files"] or any(
            sha(p) != digest for p, digest in value["files"].items()
        ):
            raise ValueError("Environment changed after validation")
        if value["receipt"]["status"] != "passed" or set(value["receipt"]["tasks"]) != {
            "nsgt",
            "kymatio",
        }:
            raise ValueError("Missing validation receipt")
        return value, None
    except (OSError, ValueError, KeyError, TypeError):
        return None, "Optional spectral worker deployment failed artifact admission"


def capabilities():
    config, reason = admission()
    return {
        task: dict(
            status="available" if config else "unavailable",
            reason=reason,
            max_samples=16384,
            max_channels=2,
            max_seconds=30,
            enabled_by_default=False,
        )
        for task in ("nsgt", "kymatio")
    }


def run(task, samples, rate):
    config, reason = admission()
    if config is None:
        raise ValueError(reason)
    if (
        task not in {"nsgt", "kymatio"}
        or not 256 <= len(samples) <= 16384
        or samples.shape[1] > 2
    ):
        raise ValueError("Optional worker input exceeds admitted sample/channel budget")
    checkpoint()
    with tempfile.TemporaryDirectory(prefix=f"oida-spectral-worker-{os.getpid()}-") as root:
        source, destination = Path(root) / "input.npy", Path(root) / "output.npy"
        np.save(source, samples, allow_pickle=False)
        environment = {
            k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}
        }
        environment.update(
            OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHONNOUSERSITE="1"
        )
        child = subprocess.Popen(
            [
                config["python"],
                "-I",
                str(Path(__file__).with_name("spectral_worker.py")),
                task,
                str(source),
                str(rate),
                str(destination),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        started = time.monotonic()
        try:
            while child.poll() is None:
                checkpoint()
                if time.monotonic() - started > 30:
                    raise ValueError("Optional worker deadline exceeded")
                rss = subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(child.pid)],
                    capture_output=True,
                    text=True,
                    timeout=1,
                ).stdout.strip()
                if rss and int(rss) > 512 * 1024:
                    raise ValueError("Optional worker RSS budget exceeded")
                time.sleep(0.02)
            if (
                child.returncode != 0
                or not destination.is_file()
                or destination.stat().st_size > 16 * 1024**2
            ):
                raise ValueError("Optional worker failed or exceeded output budget")
            from akousmata_app.derivatives import inspect_numeric

            data = destination.read_bytes()
            inspect_numeric(data)
            metadata_path = Path(str(destination) + ".json")
            if metadata_path.stat().st_size > 8192:
                raise ValueError("Optional worker metadata exceeds budget")
            return data, json.loads(metadata_path.read_text())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
