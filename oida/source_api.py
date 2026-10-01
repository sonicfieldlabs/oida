"""Owner APIs joining acquisition and structured sources to existing listening paths."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from oida.source_scheduler import SourceScheduler, QueueFull
from oida.source_metrics import process_peak_rss
from oida.operation_control import Control, controlled, checkpoint, OperationCancelled
from oida.contracts import now_iso
from oida.observation_source import (
    ObservationRequest,
    ObservationUnavailable,
    receive_observation,
)
from oida.source_capture import (
    AcquisitionReceipts,
    CaptureInterrupted,
    capture_audio,
    load_capture_sources,
)
from oida.capture_registry import CaptureRegistry, Registration
from oida.research_samples import ResearchSampleRefused, ResearchSamples
from oida.station_aperture import Aperture, admit as admit_aperture


from oida.reasoning.audio_selection import AudioModel

LOGGER = logging.getLogger(__name__)
RESEARCH_SWEEP_SECONDS = 30.0


class AcquisitionRequest(BaseModel):
    audio_model: AudioModel | None = None
    allow_external_audio: bool = False
    passes: (
        list[Literal["transcribe", "events", "caption", "speech", "music"]] | None
    ) = Field(default=None, max_length=5)
    context_refs: list[str] | None = Field(default=None, max_length=7)
    focus_question: str | None = Field(default=None, max_length=4096)
    aperture: Aperture | None = None
    specialist_tasks: list[
        Literal["tag_events", "track_beats", "transcribe", "speech_quality"]
    ] = Field(default_factory=list, max_length=3)
    specialist_deployments: dict[
        Literal["tag_events", "track_beats", "transcribe", "speech_quality"], str
    ] = Field(default_factory=dict, max_length=3)
    speech_vad: bool = True
    speech_alignment: bool = False
    speech_language: Literal["English", "Spanish", "Portuguese"] | None = None
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    acquisition_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    seconds: float = Field(gt=0, le=300)
    remember: bool = False
    retain_library_audio: bool = False
    # Keep the listened input as a local research sample; only for opted-in stations.
    retain_research_sample: bool = False
    route_preset: str = "basic"
    response_mode: Literal["full", "summary"] = "full"
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    listening_mode: str | None = Field(default=None, min_length=1, max_length=100)
    # A caller's deadline (epoch seconds). The capture window and its listening both stop
    # at it, and a listening stopped there publishes nothing.
    deadline_at: float | None = None


class ScheduledAcquisitionRequest(AcquisitionRequest):
    expires_in_seconds: float = Field(default=60.0, ge=0.05, le=3600)


def source_router(
    data_dir: Path,
    listen: Callable,
    preflight: Callable,
    *,
    shutdown_callbacks=None,
    journal=None,
    operations=None,
    validate_listening=None,
    retention_forbidden=None,
    scanned_roots=(),
) -> APIRouter:
    router = APIRouter()
    sources = load_capture_sources(os.environ.get("OIDA_CAPTURE_SOURCES"))
    registry = CaptureRegistry(data_dir / "capture-registry", sources)

    def resolve_source(identifier):
        return sources.get(identifier) or registry.source(identifier)

    @router.post("/sources/capture/register")
    def register_source(body: Registration):
        preflight("external_stream", body.max_seconds, None, False)
        try:
            return registry.register(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/sources/capture/registered")
    def registered_sources():
        return {
            "sources": registry.entries(),
            "hls": "unavailable",
            "retention": "temp_only",
        }

    @router.delete("/sources/capture/registered/{identifier}")
    def revoke_source(identifier: str):
        try:
            registry.revoke(identifier)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        with scheduler.condition:
            jobs = [
                job[1].acquisition_id
                for job in list(scheduler.pending)
                if job[0] == identifier
            ]
            if scheduler.running and scheduler.running[0] == identifier:
                jobs.append(scheduler.running[1].acquisition_id)
        with receipts.lock:
            jobs += [
                key
                for key in receipts.active
                if receipts.get(key).get("source_id") == identifier
            ]
        for job in set(jobs):
            scheduler.cancel(job)
        return {"revoked": identifier, "dependent_jobs_cancelled": len(set(jobs))}

    masa_module = os.environ.get("OIDA_MASA_VALIDATOR_MODULE")
    receipts = AcquisitionReceipts(data_dir / "source-acquisitions", journal=journal)
    samples = ResearchSamples(
        data_dir / "research-samples", receipts.journal, scanned_roots=scanned_roots
    )
    samples.sweep()
    # Expiry must not wait for a reader: sweep on a timer while any station can keep
    # samples or any sample is still on disk.
    if samples.root.exists() or any(
        s.retention == "research_sample" for s in sources.values()
    ):
        stop_sweeping = threading.Event()

        def sweep_on_schedule():
            while not stop_sweeping.wait(RESEARCH_SWEEP_SECONDS):
                try:
                    samples.sweep()
                except Exception:
                    LOGGER.exception("research-sample sweep failed")

        threading.Thread(
            target=sweep_on_schedule, name="research-sample-sweep", daemon=True
        ).start()
        if shutdown_callbacks is not None:
            shutdown_callbacks.append(stop_sweeping.set)
    if operations is None:
        from oida.operation_control import Operations

        operations = Operations(receipts.journal)
    operation_lock = threading.Lock()
    capture_temp = data_dir / "source-capture-temp"
    capture_temp.mkdir(parents=True, exist_ok=True)
    # This namespace is owned exclusively by this single-owner capture adapter.
    # The supervisor closes crashed-owner FFmpeg children; restart removes leftovers.
    for stale in capture_temp.iterdir():
        if stale.is_symlink() or stale.is_file():
            stale.unlink()
        elif stale.is_dir():
            shutil.rmtree(stale)

    @router.get("/sources/capture")
    def capture_sources():
        return dict(
            contract="oida/capture-sources/v2",
            capabilities=["bounded-public-radio-v1"],
            sources=[
                dict(
                    id=s.id,
                    adapter=s.adapter,
                    sample_rate=s.sample_rate,
                    channels=s.channels,
                    max_seconds=s.max_seconds,
                    consent=s.consent,
                    raw_audio_policy="research_sample"
                    if s.retention == "research_sample"
                    else "temp",
                    research=dict(ttl_seconds=s.research.ttl_seconds, audience="local")
                    if s.research
                    else None,
                    status="configured_unverified",
                    claim_limit="native sample rate is not capture bandwidth",
                    network_policy=s.network_policy,
                    retention=s.retention,
                    consent_ref=s.consent_ref,
                    rights_ref=s.rights_ref,
                    source_ref=s.source_ref,
                    registration_revision=s.registration_revision,
                )
                for s in sources.values()
            ],
        )

    @router.get("/sources/acquisitions/{identifier}")
    def acquisition_receipt(identifier: str):
        if (
            not identifier
            or len(identifier) > 80
            or any(
                c
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in identifier
            )
        ):
            raise HTTPException(400, "invalid acquisition id")
        item = receipts.get(identifier)
        if item is None:
            raise HTTPException(404, "unknown acquisition")
        return item

    @router.post("/sources/acquisitions/{identifier}/cancel")
    def cancel_acquisition(identifier: str):
        return dict(
            cancel_requested=scheduler.cancel(identifier),
            scope="capture or listening before the publication commit fence; late worker output is discarded",
        )

    def execute(source_id: str, req: AcquisitionRequest, deadline=None):
        source = resolve_source(source_id)
        if source is None:
            raise HTTPException(404, "unknown configured source")
        scheduled = deadline is not None
        caller_deadline = req.deadline_at
        if caller_deadline is not None:
            remaining = caller_deadline - time.time()
            if remaining > 4 * 3600:
                raise HTTPException(400, "deadline_at must be within four hours")
            if remaining < req.seconds + 1:
                # The capture window alone would outlast the deadline: nothing is started.
                raise HTTPException(
                    409,
                    "operation deadline leaves no time for the capture window; nothing was started",
                )
            deadline = caller_deadline if deadline is None else min(deadline, caller_deadline)
        # Gates precede subprocess/device/network acquisition, not just inference.
        try:
            if req.aperture and (
                req.seconds > 60 or source.channels > 2 or source.sample_rate > 192000
            ):
                raise ValueError(
                    "Requested capture exceeds native aperture duration/channel/rate budget"
                )
            admit_aperture(
                req.aperture,
                remember=req.remember,
                retain_audio=req.retain_library_audio,
                source_type=source.source_type,
            )
            if (
                source.runtime_registered or source.retention == "temp_only"
            ) and req.retain_library_audio:
                raise ValueError("Radio consent permits temporary capture only")
            if req.retain_research_sample:
                if source.retention != "research_sample" or source.research is None:
                    raise ValueError(
                        "This station is not opted in to research samples; its audio is temporary"
                    )
                if retention_forbidden is not None and retention_forbidden():
                    raise ValueError(
                        "The active covenant forbids retaining raw audio; no research sample"
                    )
            if source.consent != "granted" or req.seconds > source.max_seconds:
                raise ValueError("source consent or duration refused")
            if validate_listening is not None:
                validate_listening(
                    req.model_id,
                    req.listening_mode,
                    **(
                        {
                            "audio_model": req.audio_model,
                            "route_preset_id": req.route_preset,
                            "explicit_passes": req.passes,
                        }
                        if req.audio_model is not None
                        else {}
                    ),
                )
            preflight(source.source_type, req.seconds, req.route_preset, req.remember)
            if scheduled and time.time() >= deadline:
                scheduler.terminal_queued(
                    req.acquisition_id, "expired", "deadline elapsed before acquisition"
                )
                raise ValueError("source job expired")
            item, cancel = receipts.begin(
                req.acquisition_id, source_id, scheduled=scheduled
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result = None
        started = time.perf_counter()
        timing = dict(
            basis="observed owner wall time; workload/model evidence is recorded separately"
        )
        if item.get("queued_at"):
            timing["queue_wait_seconds"] = max(
                0.0, time.time() - datetime.fromisoformat(item["queued_at"]).timestamp()
            )
        try:
            item["source_descriptor"] = dict(
                producer_id=source.producer_id,
                source_id=source.id,
                apparatus=source.apparatus,
                consent=source.consent,
                consent_ref=source.consent_ref,
                rights_ref=source.rights_ref,
                source_ref=source.source_ref,
                registration_revision=source.registration_revision,
                raw_audio_policy="temp",
                source_time=item["started_at"],
                source_time_basis="local-acquisition-start",
                expected_format=dict(
                    sample_rate=source.sample_rate, channels=source.channels
                ),
            )
            if source.runtime_registered or source.network_policy == "public_radio":
                item["source_descriptor"]["format_limits"] = item[
                    "source_descriptor"
                ].pop("expected_format")
            if caller_deadline is not None:
                item["deadline_at"] = caller_deadline
            receipts.save(item)
            with TemporaryDirectory(prefix="oida-source-", dir=capture_temp) as temp:
                path = Path(temp) / "capture.wav"
                capture_started = time.perf_counter()
                timer = None
                if caller_deadline is not None:
                    # Stops an acquisition still running when the deadline passes.
                    timer = threading.Timer(
                        max(0.0, caller_deadline - time.time()), cancel.set
                    )
                    timer.daemon = True
                    timer.start()
                try:
                    capture_audio(source, req.seconds, path, cancel)
                finally:
                    if timer is not None:
                        timer.cancel()
                import soundfile as sf

                actual = sf.info(path)
                item["source_descriptor"]["actual_format"] = dict(
                    sample_rate=actual.samplerate, channels=actual.channels
                )
                timing["capture_seconds"] = time.perf_counter() - capture_started
                with receipts.lock:
                    if deadline is not None and time.time() >= deadline:
                        cancel.set()
                    if cancel.is_set():
                        raise CaptureInterrupted("capture cancelled")
                    item.update(status="listening")
                    receipts.save(item)

                def seal():
                    item.update(status="committing")
                    receipts.save(item)

                def recheck():
                    if (
                        source.runtime_registered
                        and registry.source(source_id) != source
                    ):
                        raise CaptureInterrupted("Capture registration revoked")
                    preflight(
                        source.source_type, req.seconds, req.route_preset, req.remember
                    )

                control = Control(
                    event=cancel,
                    lock=receipts.lock,
                    recheck=recheck,
                    on_seal=seal,
                    identifier=req.acquisition_id,
                    deadline_at=caller_deadline,
                )
                listening_started = time.perf_counter()
                with controlled(control):
                    result = listen(
                        dict(
                            **(
                                {"aperture": req.aperture.model_dump(exclude_none=True)}
                                if req.aperture
                                else {}
                            ),
                            path=str(path),
                            source_type=source.source_type,
                            source_label=source.id,
                            raw_audio_policy="temp",
                            remember=req.remember,
                            retain_library_audio=req.retain_library_audio,
                            route_preset=req.route_preset,
                            response_mode=req.response_mode,
                            model_id=req.model_id,
                            allow_external_audio=req.allow_external_audio,
                            passes=req.passes,
                            context_refs=req.context_refs,
                            focus_question=req.focus_question,
                            **(
                                {"audio_model": req.audio_model.model_dump()}
                                if req.audio_model is not None
                                else {}
                            ),
                            listening_mode=req.listening_mode,
                            specialist_tasks=req.specialist_tasks,
                            specialist_deployments=req.specialist_deployments,
                            speech_vad=req.speech_vad,
                            speech_alignment=req.speech_alignment,
                            speech_language=req.speech_language,
                            source_admission=dict(
                                adapter="radio-window"
                                if source.adapter == "radio"
                                else "high-rate-device",
                                producer_id=source.producer_id,
                                source_id=source.id,
                                source_time=item["started_at"],
                                source_time_basis="local-acquisition-start",
                                consent=source.consent,
                                consent_ref=source.consent_ref,
                                raw_audio_policy="temp",
                                max_window_s=req.seconds,
                                apparatus=source.apparatus,
                            ),
                        )
                    )
                    checkpoint(seal=True)
                timing["listening_seconds"] = time.perf_counter() - listening_started
                event = result.get("listening_event") or {}
                if not event:
                    item.update(
                        status="refused",
                        reason="listening route refused after acquisition",
                    )
                else:
                    admission = event["source"]["details"]["source_admission"]
                    sample = None
                    if req.retain_research_sample:
                        segment = event.get("segment") or {}
                        try:
                            sample = samples.retain(
                                path,
                                expected_sha256=(segment.get("data_ref") or {}).get(
                                    "sha256"
                                ),
                                source=source,
                                acquisition_id=req.acquisition_id,
                                event_id=event.get("id"),
                                record_id=result.get("akousma_id"),
                                captured_at=item["started_at"],
                                sample_rate=actual.samplerate,
                                channels=actual.channels,
                            )
                        except (ResearchSampleRefused, OSError) as exc:
                            sample = dict(status="not_retained", reason=str(exc)[:300])
                    retained = bool(sample and sample.get("status") == "retained")
                    item.update(
                        status="complete",
                        event_id=event["id"],
                        akousma_id=result.get("akousma_id"),
                        source_admission=admission,
                        raw_audio_policy="temp",
                        raw_audio_deleted=not retained,
                        **({"research_sample": sample} if sample else {}),
                    )
        except (CaptureInterrupted, OperationCancelled):
            result = None
            expired = deadline is not None and time.time() >= deadline
            item.update(
                status="expired" if expired else "cancelled",
                reason="deadline elapsed before publication"
                if expired
                else "cancelled before publication; late output discarded",
                **(
                    {"reason_code": "deadline"}
                    if expired and caller_deadline is not None
                    else {}
                ),
            )
        except HTTPException as exc:
            result = None
            item.update(
                status="refused",
                reason="listening route refused",
                http_status=exc.status_code,
            )
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            # The generic sentence sent operators to the source configuration
            # for failures that had nothing to do with it. Carry the exception
            # out: the type and message name what actually broke, and a capture
            # failure here holds no audio and no credential.
            LOGGER.exception("capture listen failed for %s", item.get("source_id"))
            item.update(
                status="failed",
                reason=(
                    f"acquisition or listening failed ({type(exc).__name__}: {exc})"
                ),
            )
        except Exception:
            item.update(
                status="failed", reason="unexpected acquisition or listening failure"
            )
            raise
        finally:
            timing["execution_seconds"] = time.perf_counter() - started
            item["timing"] = timing
            item["owner_peak_rss"] = process_peak_rss()
            item["finished_at"] = now_iso()
            receipts.finish(item)
        return dict(receipt=item, result=result)

    scheduler = SourceScheduler(
        receipts,
        execute,
        operation_lock,
        capacity=int(os.environ.get("OIDA_SOURCE_QUEUE_CAPACITY", "8")),
    )
    if shutdown_callbacks is not None:
        shutdown_callbacks.append(scheduler.close)

    @router.post("/sources/capture/{source_id}/listen")
    def capture_listen(source_id: str, req: AcquisitionRequest):
        if not scheduler.status()["accepting"]:
            raise HTTPException(503, "source scheduler is shutting down")
        if not operation_lock.acquire(blocking=False):
            raise HTTPException(
                409, "source execution busy; use the bounded jobs endpoint"
            )
        try:
            return execute(source_id, req)
        finally:
            operation_lock.release()

    @router.get("/sources/research-samples")
    def research_samples():
        return dict(
            contract="oida/research-samples/v1",
            audience="local",
            exportable=False,
            samples=samples.entries(),
        )

    @router.delete("/sources/research-samples/{identifier}")
    def delete_research_sample(identifier: str):
        try:
            receipt = samples.delete(identifier)
        except KeyError as exc:
            raise HTTPException(404, "No such research sample, or it expired") from exc
        return {k: v for k, v in receipt.items() if k != "file"}

    @router.get("/sources/research-samples/{identifier}/resolve")
    def research_sample_location(identifier: str):
        try:
            path, value = samples.audio(identifier)
        except KeyError as exc:
            raise HTTPException(404, "No such research sample, or it expired") from exc
        except ResearchSampleRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"contract": "oida/research-sample-location/v1", "path": str(path.resolve()),
                "sample": {k: v for k, v in value.items() if k != "file"}}

    @router.get("/sources/research-samples/{identifier}/audio")
    def research_sample_audio(identifier: str):
        from fastapi.responses import FileResponse

        try:
            path, value = samples.audio(identifier)
        except KeyError as exc:
            raise HTTPException(404, "No such research sample, or it expired") from exc
        except ResearchSampleRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        return FileResponse(
            path,
            media_type="audio/wav"
            if path.suffix == ".wav"
            else "application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "X-Oida-Research-Sample-Sha256": value["sha256"],
            },
        )

    @router.get("/sources/scheduler")
    def scheduler_status():
        return scheduler.status()

    @router.post("/sources/capture/{source_id}/jobs", status_code=202)
    def schedule(source_id: str, req: ScheduledAcquisitionRequest):
        source = resolve_source(source_id)
        if source is None:
            raise HTTPException(404, "unknown configured source")
        try:
            if source.consent != "granted" or req.seconds > source.max_seconds:
                raise ValueError("source consent or duration refused")
            if validate_listening is not None:
                validate_listening(
                    req.model_id,
                    req.listening_mode,
                    **(
                        {
                            "audio_model": req.audio_model,
                            "route_preset_id": req.route_preset,
                            "explicit_passes": req.passes,
                        }
                        if req.audio_model is not None
                        else {}
                    ),
                )
            preflight(source.source_type, req.seconds, req.route_preset, req.remember)
            return scheduler.submit(source_id, req)
        except QueueFull as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def execute_observation(req: ObservationRequest):
        try:
            preflight("external_stream", None, None, req.remember)
            result = receive_observation(
                req,
                masa_module,
                before_commit=lambda: preflight(
                    "external_stream", None, None, req.remember
                ),
            )
            if result.get("akousma_id"):
                receipts.journal.record_reference(
                    result["akousma_id"], record=result["record"]
                )
            return result
        except ObservationUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except (ImportError, FileNotFoundError) as exc:
            raise HTTPException(
                503, "required observation contracts are unavailable"
            ) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/sources/observations")
    def observation(req: ObservationRequest):
        return operations.run(req.operation_id, lambda: execute_observation(req))

    @router.post("/sources/cosmoaudition/poll")
    def cosmoaudition_poll(body: dict):
        from oida.cosmo_subscription import request_from_feed
        import httpx

        try:
            preflight("external_stream", None, None, body.get("remember", False))
            return operations.run(
                body.get("operation_id"),
                lambda: execute_observation(request_from_feed(body)),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Cosmoaudition owner feed unavailable") from exc

    from oida.owner_journal_api import owner_journal_router

    router.include_router(owner_journal_router(receipts.journal))
    return router
