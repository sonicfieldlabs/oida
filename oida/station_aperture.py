"""Portable Station requests resolved by the owner against immutable audio bytes."""

import hashlib
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT = "listeningstack/aperture-request/v1"


class Retention(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    derivatives_permitted: bool
    expires_at: float = Field(gt=0)
    permission_ref: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def permitted(self):
        if self.derivatives_permitted is not True:
            raise ValueError("Explicit derivative permission required")
        return self


class Aperture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    contract: Literal["listeningstack/aperture-request/v1"] = CONTRACT
    mode: Literal["centaur", "human_reference", "beyond"]
    bands_hz: list[list[float]] | None = Field(default=None, max_length=16)
    analysis_profile: Literal["pyramid-v1"] = "pyramid-v1"
    views: list[Literal["complex_stft", "nsgt", "scattering"]] = Field(
        default_factory=list, max_length=3
    )
    retention: Retention | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.bands_hz is not None:
            if not self.bands_hz or any(
                len(b) != 2 or not 0 <= b[0] < b[1] <= 96000 for b in self.bands_hz
            ):
                raise ValueError(
                    "Choose increasing frequency bands between 0 and 96000 Hz"
                )
            if any(a[1] > b[0] for a, b in zip(self.bands_hz, self.bands_hz[1:])):
                raise ValueError("Bands must be ordered and disjoint")
        if len(set(self.views)) != len(self.views):
            raise ValueError("Views must be unique")
        return self


def capabilities():
    from oida.spectral_specialists import capabilities as workers

    available = workers()
    return dict(
        aperture_contracts=[CONTRACT],
        analysis_profiles=["pyramid-v1"],
        aperture_preview_endpoint="/sources/agent-native/preview",
        retained_views={
            "complex_stft": dict(
                status="available",
                reason="Explicit audio retention, derivative permission and deadline required",
            ),
            "nsgt": available["nsgt"],
            "scattering": available["kymatio"],
        },
        qualification_scope="Sampled native DSP only; model, capture and human access require separate owner evidence",
        window_contract="oida/aperture-window/v1",
    )


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    aperture: Aperture
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    seconds: float = Field(default=10, gt=0, le=60)
    start_seconds: float = Field(default=0, ge=0, le=86400)
    model_id: str | None = Field(default=None, max_length=256)


def preview(req):
    """Read file declarations only. No capture, transforms, model load or writes."""
    from oida.apertures import file_aperture

    limits = [
        dict(
            kind=k,
            status="undetermined",
            declared=None,
            reason="No source-bound declaration available",
        )
        for k in ("capture", "sampled_representation", "model_input", "human_access")
    ]
    result = dict(
        limits=limits,
        admission=dict(
            status="supported",
            reason="Request format supported; source and covenant rechecked at execution",
        ),
    )
    if req.path is None:
        return result
    try:
        with Path(req.path).open("rb") as stream:
            raw = stream.read(96 * 1024**2 + 1)
        aperture = file_aperture(
            req.path,
            mode=req.aperture.mode,
            bands_hz=req.aperture.bands_hz,
            source_bytes=raw,
        )
        duration = aperture["samples"] / aperture["sample_rate"]
        if req.start_seconds >= duration:
            raise ValueError("Requested window starts after the source ends")
        seconds = min(req.seconds, duration - req.start_seconds)
        aperture = file_aperture(
            req.path,
            mode=req.aperture.mode,
            bands_hz=req.aperture.bands_hz,
            window_s=[req.start_seconds, req.start_seconds + seconds],
            source_bytes=raw,
        )
        supported = aperture["decision"]["outcome"] == "permitted"
        limits[1].update(
            status="supported" if supported else "unsupported",
            declared=dict(
                sample_rate_hz=aperture["sample_rate"],
                channels=aperture["channels"],
                bands_hz=[[0, aperture["sample_rate"] / 2]],
                source_sha256=aperture["source_sha256"],
            ),
            reason="File bytes establish the digital representation ceiling; this is not physical capture support",
        )
        limits[2]["reason"] = (
            "Selected model preprocessing is rechecked at execution; file sample rate does not declare model input"
        )
        result.update(
            aperture=aperture,
            time_scales=dict(
                sample_period_s=1 / aperture["sample_rate"],
                admitted_window_s=seconds,
                start_seconds=req.start_seconds,
                grain_status="planned; transform levels are measured only during execution",
            ),
        )
        if not supported:
            result["admission"] = dict(
                status="unsupported",
                reason="Requested bands exceed this sampled representation",
            )
    except (ValueError, OSError, RuntimeError):
        result["admission"] = dict(
            status="undetermined",
            reason="Source preview unavailable; check the audio file and requested window",
        )
    return result


def admit(request, *, remember, retain_audio, source_type="file"):
    """Check requests before acquisition; modes never create retention permission."""
    if request is None:
        return
    available = capabilities()["retained_views"] if request.views else {}
    for view in request.views:
        if available[view]["status"] != "available":
            raise HTTPException(409, f"Requested retained view is unavailable: {view}")
    if request.views and (
        not remember
        or not retain_audio
        or source_type == "system_output"
        or request.retention is None
        or request.retention.expires_at <= time.time()
    ):
        raise HTTPException(
            409,
            "Views require permitted audio memory, explicit derivative permission and a current retention deadline",
        )


@contextmanager
def prepare(req, *, root, preflight):
    """One byte snapshot feeds both native DSP and the originally selected route."""
    from oida.agent_native import NativeRequest, admission, native_measure
    from oida.operation_control import checkpoint, current_operation_id

    aperture = req.aperture
    if aperture is None:
        yield req, None
        return
    admit(
        aperture,
        remember=req.remember,
        retain_audio=req.retain_library_audio,
        source_type=req.source_type,
    )
    if (
        req.native_options is not None
        or req.spectral_request is not None
        or req.listening_access is not None
    ):
        raise HTTPException(400, "Choose one aperture request mechanism")
    if not admission.acquire(blocking=False):
        raise HTTPException(409, "Native DSP resource slot is busy")
    try:
        path = Path(req.path).expanduser()
        if not path.is_file() or path.stat().st_size > 96 * 1024**2:
            raise ValueError("Aperture source must be a regular file within 96 MiB")
        with path.open("rb") as source:
            raw = source.read(96 * 1024**2 + 1)
        if len(raw) > 96 * 1024**2:
            raise ValueError("Aperture source exceeds byte budget")
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aperture-", dir=root) as folder:
            frozen = Path(folder) / ("source" + path.suffix)
            frozen.write_bytes(raw)
            preflight(frozen)
            checkpoint()
            retain = bool(aperture.views)
            result = native_measure(
                NativeRequest(
                    operation_id=current_operation_id() or "station-aperture",
                    path=str(frozen),
                    source_sha256=hashlib.sha256(raw).hexdigest(),
                    permission_ref=aperture.retention.permission_ref
                    if retain
                    else "explicit-owner-listening-request",
                    mode=aperture.mode,
                    bands_hz=aperture.bands_hz,
                    memory="record_audio" if retain else "record",
                    derivatives_permitted=retain,
                    expires_at=aperture.retention.expires_at if retain else None,
                    workers=[
                        "kymatio" if v == "scattering" else v
                        for v in aperture.views
                        if v != "complex_stft"
                    ],
                    retained_views=aperture.views,
                )
            )
            if result["outcome"] != "measured":
                raise HTTPException(
                    409,
                    dict(
                        reason="Requested aperture cannot be measured",
                        aperture=result.get("aperture"),
                    ),
                )
            result["requested"] = aperture.model_dump(exclude_none=True)
            preflight(frozen)
            result["recheck"] = lambda: preflight(frozen)
            # A temporary path must never become a durable external-reference URI.
            yield (
                req.model_copy(
                    update={
                        "path": str(frozen),
                        "raw_audio_policy": "not_stored"
                        if req.raw_audio_policy == "not_stored"
                        else "temp",
                    }
                ),
                result,
            )
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        admission.release()


def receipt(result):
    return dict(
        requested=result["requested"],
        outcome=result["outcome"],
        aperture=result["aperture"],
        views=[
            {**v, "state": "pending_publication"} if v["state"] == "retained" else v
            for v in result["bundle"]["views"]
        ],
        retention_status="pending_publication"
        if result["requested"]["views"]
        else "not_requested",
        report=result["report"],
        scope="Native DSP on the same sampled excerpt; model and human access are separately attributed",
    )


def persist(record, result):
    """Publish the ordinary listening and native evidence in one shared record."""
    from akousma import AkousmataStore
    from akousmata_app.derivatives import publish_bundle

    native = deepcopy(result["record"])
    record["schema_version"] = "1.8.0"
    record["auditum"]["contract"] = "earworm/auditum/v3"
    record.setdefault("listening", {}).update(native["listening"])
    record["auditum"]["listenings"].extend(native["auditum"]["listenings"])
    extensions = record.setdefault("extensions", {})
    for key in ("akouo.agent-native", "oida.aperture", "oida.spectral"):
        extensions[key] = native["extensions"][key]
    # Keep the separate DSP access declaration alongside the route's own declaration.
    extensions["oida.native-access"] = native["extensions"]["earworm_listening_access"]
    extensions["oida.native-context"] = native["extensions"][
        "earworm_listening_context"
    ]
    bundle = extensions["oida.spectral"]
    bundle["record_ref"] = record["akousma_id"]
    policy = result["requested"].get("retention")
    extensions["oida.native-policy"] = dict(
        requested=result["requested"],
        memory="record_audio" if policy and result["requested"]["views"] else "record",
        covenant=record.get("auditum", {}).get("covenant"),
    )

    def resolve(ref):
        return bundle if ref == bundle["sampled_representation"] else None

    with AkousmataStore() as store:
        if policy and result["requested"]["views"]:
            published = publish_bundle(
                store,
                record,
                result["payloads"],
                memory="record_audio",
                derivatives_permitted=True,
                expires_at=policy["expires_at"],
                source_bytes=result["source_bytes"],
                validate_native=result["validate_native"],
                resolve_representation=resolve,
            )
            record.clear()
            record.update(published)
        else:
            store.put(
                record,
                supported_versions=["1.8.0"],
                validate_native=result["validate_native"],
                resolve_object=lambda ref: None,
                resolve_representation=resolve,
            )
    return record["akousma_id"]
