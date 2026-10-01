"""Owner task dispatch, bounded queue, isolated workers and additive evidence.

T5: a task may carry several admitted deployments (for example EfficientAT
``mn04_as`` and a separately pinned ``mn10_as``); the listener selects one
explicitly or takes the configured default. A named deployment that is not
admitted is reported unavailable with its own reason — no analysis is
substituted and no unavailable lane becomes a positive finding.
"""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from akousma.deployments import DeploymentRegistry
from oida.operation_control import checkpoint

TASKS = {
    "transcribe": ("qwen3-asr-06b", "Qwen3 ASR · 0.6B (MLX 4-bit)"),
    "tag_events": ("efficientat-mn04", "EfficientAT · mn04_as"),
    "track_beats": ("beat-this-small", "Beat This! · small0"),
    "speech_quality": ("dnsmos-p835", "DNSMOS · P.835 speech quality"),
}


def worker_path(task):
    if task == "transcribe":
        return Path(__file__).with_name("speech_worker.py")
    if task == "speech_quality":
        return Path(__file__).with_name("quality_worker.py")
    return Path(__file__).with_name("worker.py")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


class Specialists:
    def __init__(self, config_path=None):
        self.registry = DeploymentRegistry("oida")
        self.entries = {}  # (task, deployment id) -> entry
        self.defaults = {}  # task -> deployment id (first admitted wins)
        self.errors = {}
        self.slot = threading.BoundedSemaphore(1)
        self.waiters = threading.BoundedSemaphore(4)
        if config_path is None:
            config_path = os.environ.get("OIDA_SPECIALISTS_CONFIG")
        configs = []
        for path in [config_path, os.environ.get("OIDA_SPEECH_CONFIG")]:
            if not path:
                continue
            try:
                entries = json.loads(Path(path).read_text())["deployments"]
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) for entry in entries
                ):
                    raise ValueError("Invalid deployment collection")
                configs.extend(entries)
            except (OSError, ValueError, KeyError, TypeError):
                self.errors["configuration"] = (
                    "Optional specialist configuration unavailable"
                )
        for entry in configs:
            task = entry.get("task", "unknown")
            manifest_id = (entry.get("manifest") or {}).get("id") or entry.get(
                "deployment_id"
            )
            key = (task, manifest_id)
            try:
                if manifest_id is None:
                    raise ValueError("Deployment identity missing")
                if task not in TASKS:
                    raise ValueError("Unknown specialist task")
                if key in self.entries:
                    raise ValueError("Duplicate specialist deployment")
                # A deployment names its own adapter identity; the task's
                # canonical adapter id remains the default so the original
                # single-deployment manifests verify unchanged.
                entry.setdefault("adapter", TASKS[task][0])
                self.registry.register_adapter(
                    manifest_id, [task], verify=lambda e=entry: self.verify(e)
                )
                self.registry.admit(entry["manifest"])
                self.entries[key] = entry
                self.defaults.setdefault(task, manifest_id)
            except (ValueError, KeyError, TypeError) as exc:
                self.errors[task] = f"Specialist not admitted: {type(exc).__name__}"

    def verify(self, entry):
        try:
            expected_adapter = entry.get("adapter") or TASKS[entry["task"]][0]
            if entry["manifest"]["adapter"] != expected_adapter:
                return ["Adapter identity mismatch"]
            files = entry["files"]
            required = [
                str(worker_path(entry["task"])),
                entry["checkpoint"],
                entry["receipt"],
                entry["license"],
                entry["environment_lock"],
                str(Path(entry["python"]).resolve()),
            ]
            if any(path not in files for path in required):
                return ["Missing required artifact hash"]
            if any(digest(path) != expected for path, expected in files.items()):
                return ["Artifact changed since validation"]
            if not Path(entry["python"]).is_file():
                return ["Specialist Python environment missing"]
            manifest = entry["manifest"]
            if manifest["components"][0]["sha256"] != files[entry["checkpoint"]]:
                return ["Checkpoint manifest mismatch"]
            if manifest["runtime_revision"] != files[str(worker_path(entry["task"]))]:
                return ["Worker revision mismatch"]
            if (
                manifest["license_review"] != files[entry["license"]]
                or manifest["validation_receipt"] != files[entry["receipt"]]
            ):
                return ["Review receipt mismatch"]
            report = json.loads(Path(entry["receipt"]).read_text())
            if (
                report["worker_sha256"] != manifest["runtime_revision"]
                or report["checkpoint_sha256"] != files[entry["checkpoint"]]
            ):
                return ["Measurements belong to different artifacts"]
            if report["max_input_seconds"] < manifest["max_input_seconds"]:
                return ["Requested duration not measured"]
            if report.get("status") != "passed" or report.get("task") != entry["task"]:
                return ["Validation receipt mismatch"]
            if (
                entry["manifest"]["measured_peak_memory_mib"]
                < report["peak_memory_mib"]
            ):
                return ["Memory profile mismatch"]
            return []
        except (OSError, ValueError, KeyError, TypeError):
            return ["Incomplete local artifacts"]

    def options(self):
        options = []
        for task, (identifier, name) in TASKS.items():
            deployments = []
            for deployment_id, entry in self.entries.items():
                if deployment_id[0] != task:
                    continue
                problems = self.verify(entry)
                deployments.append(
                    dict(
                        id=deployment_id[1],
                        available=not problems,
                        default=deployment_id[1] == self.defaults.get(task),
                        detail="Local specialist · verified artifacts"
                        if not problems
                        else "; ".join(problems),
                    )
                )
            if not deployments:
                problems = [
                    self.errors.get(
                        task,
                        self.errors.get(
                            "configuration", "Not provisioned and validated"
                        ),
                    )
                ]
                deployments.append(
                    dict(
                        id=identifier,
                        available=False,
                        default=True,
                        detail="; ".join(problems),
                    )
                )
            options.append(
                dict(
                    id=task,
                    name=name,
                    model_id=identifier,
                    available=any(d["available"] for d in deployments),
                    detail="Select a deployment per listening; old task-only requests keep the configured default",
                    deployments=deployments,
                )
            )
        return options

    def _entry_for(self, task, deployment=None):
        """The admitted entry for an explicit or default deployment selection."""
        wanted = deployment or self.defaults.get(task)
        if wanted is not None:
            return self.entries.get((task, wanted))
        return None

    def execute(
        self,
        path,
        tasks,
        *,
        asset_id,
        start_seconds=0,
        covenant=None,
        source_sha256=None,
        speech_options=None,
        deployments=None,
    ):
        if len(tasks) != len(set(tasks)) or any(t not in TASKS for t in tasks):
            raise ValueError("Invalid specialist task selection")
        deployments = deployments or {}
        unknown = [t for t in deployments if t not in TASKS]
        if unknown:
            raise ValueError(f"Invalid specialist deployment selection: {unknown[0]}")
        orphans = [t for t in deployments if t not in tasks]
        if orphans:
            raise ValueError(
                f"Deployment selection names a task that was not selected: {orphans[0]}"
            )
        expected_hash = source_sha256 or digest(path)
        if digest(path) != expected_hash:
            raise ValueError("Source changed before specialist analysis")
        results = []
        for task in tasks:
            checkpoint()
            selected = self._entry_for(task, deployments.get(task))
            item = dict(
                task=task,
                model=TASKS[task][1],
                deployment_id=selected["manifest"]["id"]
                if selected
                else deployments.get(task),
            )
            # Whole event ontology and beat traces cannot bypass content restrictions.
            if covenant is not None and (
                covenant.covenant.rules_for("ignore")
                or covenant.covenant.rules_for("do_not_reveal")
            ):
                results.append(
                    dict(
                        **item,
                        status="withheld",
                        reason="Specialist lane withheld by active content/output covenant",
                    )
                )
                continue
            if selected is None:
                if deployments.get(task):
                    reason = (
                        f"Selected specialist deployment {deployments[task]!r} is not "
                        "admitted; no analysis was substituted"
                    )
                else:
                    reason = "Specialist deployment is not admitted"
                results.append(
                    dict(
                        **item,
                        status="unavailable",
                        reason=reason,
                    )
                )
                continue
            entry = selected
            if task == "transcribe":
                entry = {**entry, "request_options": speech_options or {}}
            try:
                value = self.run_worker(path, task, entry)
                if value["source_sha256"] != expected_hash:
                    raise ValueError("Specialist analyzed a different source")
                manifest = self.registry.require(
                    entry["manifest"]["id"], task, value["duration_seconds"]
                )
                if task == "transcribe":
                    from oida.specialists.speech_validation import validate_transcript

                    validate_transcript(value["result"], value["duration_seconds"])
                frame_id = "frame_" + uuid4().hex
                end = start_seconds + value["duration_seconds"]
                evidence = dict(
                    contract="earworm/analysis-evidence/v1",
                    frame_id=frame_id,
                    deployment_id=manifest["id"],
                    model_revision=manifest["components"][0]["revision"],
                    capability=task,
                    view=dict(
                        asset_id=asset_id,
                        content_sha256=value["view_sha256"],
                        start_seconds=start_seconds,
                        end_seconds=end,
                        sample_rate_hz=value["sample_rate_hz"],
                        channels=1,
                        transformations=value["transformations"],
                    ),
                    evidence_kind="undetermined"
                    if value["result"]["status"] == "undetermined"
                    else "model_hypothesis",
                    confidence_kind="not_provided"
                    if task == "transcribe"
                    else "uncalibrated_score",
                    result=value["result"],
                    limitations=value["limitations"],
                )
                self.registry.validate_result(manifest["id"], evidence)
                frame = dict(
                    frame_id=frame_id,
                    asset_ref=asset_id,
                    time_range=dict(start=start_seconds, end=end, unit="seconds"),
                    features={task: value["result"]},
                )
                results.append(
                    dict(
                        **item,
                        status="complete",
                        evidence=evidence,
                        frame=frame,
                        source_sha256=value["source_sha256"],
                        deployment_manifest=manifest,
                        processor_view=value.get("processor_view"),
                        latency_seconds=value["wall_seconds"],
                        peak_memory_mib=value["peak_memory_mib"],
                    )
                )
            except (
                ValueError,
                RuntimeError,
                OSError,
                TimeoutError,
                KeyError,
                TypeError,
            ) as exc:
                results.append(dict(**item, status="failed", reason=str(exc)[:300]))
        checkpoint()
        if digest(path) != expected_hash:
            raise ValueError("Source changed during compound listening")
        return results

    def run_worker(self, path, task, entry):
        import soundfile as sf

        duration = sf.info(path).duration
        self.registry.require(entry["manifest"]["id"], task, duration)
        problems = self.verify(entry)
        if problems:
            raise ValueError("; ".join(problems))
        if not self.waiters.acquire(blocking=False):
            raise RuntimeError("Specialist queue full; retry later")
        acquired = False
        try:
            deadline = time.monotonic() + 60
            while not acquired:
                checkpoint()
                if time.monotonic() > deadline:
                    raise TimeoutError("Specialist queue deadline exceeded")
                acquired = self.slot.acquire(timeout=0.05)
            source_hash = digest(path)
            with tempfile.TemporaryDirectory(prefix="oida-specialist-") as tmp:
                tmp = Path(tmp)
                request = tmp / "request.json"
                output = tmp / "output.json"
                request.write_text(
                    json.dumps(
                        dict(
                            path=str(path),
                            task=task,
                            repository=entry["repository"],
                            checkpoint=entry["checkpoint"],
                            options=entry.get("request_options", {}),
                        )
                    )
                )
                env = {
                    key: os.environ[key]
                    for key in ("PATH", "HOME", "TMPDIR")
                    if key in os.environ
                }
                env.update(
                    HF_HUB_OFFLINE="1",
                    OMP_NUM_THREADS="2",
                    OPENBLAS_NUM_THREADS="2",
                    PYTHONDONTWRITEBYTECODE="1",
                )
                from contextlib import ExitStack
                from akousma.resource_admission import heavy_lease

                with ExitStack() as stack:
                    if task == "transcribe":
                        stack.enter_context(
                            heavy_lease(
                                "oida", "transcribe", timeout=120, checkpoint=checkpoint
                            )
                        )
                    log = stack.enter_context((tmp / "stderr").open("w+"))
                    process = subprocess.Popen(
                        [
                            entry["python"],
                            str(worker_path(task)),
                            str(request),
                            str(output),
                        ],
                        cwd=tmp,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=log,
                        start_new_session=True,
                    )
                    try:
                        worker_started = time.monotonic()
                        deadline = worker_started + (
                            300 if task == "transcribe" else 60
                        )
                        while process.poll() is None:
                            checkpoint()
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    "Specialist inference deadline exceeded"
                                )
                            time.sleep(0.05)
                        checkpoint()
                        if process.returncode:
                            raise RuntimeError(
                                f"{task} worker failed (exit {process.returncode}); other evidence retained"
                            )
                        if output.stat().st_size > 256 * 1024:
                            raise RuntimeError("Specialist output exceeds limit")
                        value = json.loads(output.read_text())
                        if (
                            digest(path) != source_hash
                            or value["source_sha256"] != source_hash
                        ):
                            raise RuntimeError(
                                "Source changed during specialist analysis"
                            )
                        value["wall_seconds"] = time.monotonic() - worker_started
                        return value
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
        finally:
            if acquired:
                self.slot.release()
            self.waiters.release()
