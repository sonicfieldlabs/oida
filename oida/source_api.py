"""Owner APIs joining acquisition and structured sources to existing listening paths."""

from __future__ import annotations

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
from oida.station_aperture import Aperture, admit as admit_aperture


from oida.reasoning.audio_selection import AudioModel


class AcquisitionRequest(BaseModel):
    audio_model: AudioModel | None = None
    allow_external_audio: bool = False
    passes: (
        list[Literal["transcribe", "events", "caption", "speech", "music"]] | None
    ) = Field(default=None, max_length=5)
    context_refs: list[str] | None = Field(default=None, max_length=7)
    focus_question: str | None = Field(default=None, max_length=4096)
    aperture: Aperture | None = None
    specialist_tasks: list[Literal["tag_events", "track_beats", "transcribe"]] = Field(
        default_factory=list, max_length=3
    )
    speech_vad: bool = True
    speech_alignment: bool = False
    speech_language: Literal["English", "Spanish", "Portuguese"] | None = None
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    acquisition_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    seconds: float = Field(gt=0, le=300)
    remember: bool = False
    retain_library_audio: bool = False
    route_preset: str = "basic"
    response_mode: Literal["full", "summary"] = "full"
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    listening_mode: str | None = Field(default=None, min_length=1, max_length=100)


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
                    raw_audio_policy="temp",
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
            if deadline is not None and time.time() >= deadline:
                scheduler.terminal_queued(
                    req.acquisition_id, "expired", "deadline elapsed before acquisition"
                )
                raise ValueError("source job expired")
            item, cancel = receipts.begin(
                req.acquisition_id, source_id, scheduled=deadline is not None
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
            receipts.save(item)
            with TemporaryDirectory(prefix="oida-source-", dir=capture_temp) as temp:
                path = Path(temp) / "capture.wav"
                capture_started = time.perf_counter()
                capture_audio(source, req.seconds, path, cancel)
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
                    item.update(
                        status="complete",
                        event_id=event["id"],
                        akousma_id=result.get("akousma_id"),
                        source_admission=admission,
                        raw_audio_policy="temp",
                        raw_audio_deleted=True,
                    )
        except (CaptureInterrupted, OperationCancelled):
            result = None
            expired = deadline is not None and time.time() >= deadline
            item.update(
                status="expired" if expired else "cancelled",
                reason="deadline elapsed before publication"
                if expired
                else "cancelled before publication; late output discarded",
            )
        except HTTPException as exc:
            result = None
            item.update(
                status="refused",
                reason="listening route refused",
                http_status=exc.status_code,
            )
        except (ValueError, RuntimeError, OSError, TimeoutError):
            item.update(
                status="failed",
                reason="acquisition or listening failed; source configuration and native format must match",
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
