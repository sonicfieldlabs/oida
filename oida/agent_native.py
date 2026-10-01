"""Owner-native DSP route over admitted file bytes and canonical evidence contracts."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Literal
import hashlib
import threading
import time
import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from akouo_contract.native_evidence import native_evidence_errors
from oida.apertures import file_aperture
from oida.dsp_multires import pyramid
from oida.agent_reports import report, account, unknown, identity
from oida.operation_control import checkpoint
from oida.station_aperture import PreviewRequest, preview as preview_aperture


admission = threading.Lock()


class NativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    path: str = Field(min_length=1, max_length=4096)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    permission_ref: str = Field(min_length=1, max_length=512)
    mode: Literal["centaur", "human_reference", "beyond"] = "centaur"
    bands_hz: list[list[float]] | None = None
    memory: Literal["none", "record", "record_audio"] = "none"
    derivatives_permitted: bool = False
    expires_at: float | None = None
    retained_views: list[Literal["complex_stft", "nsgt", "scattering"]] | None = Field(default=None, max_length=3)
    workers: list[Literal["nsgt", "kymatio"]] = Field(
        default_factory=list, max_length=2
    )

    @model_validator(mode="after")
    def selected_bands(self):
        from oida.station_aperture import Aperture
        Aperture(mode=self.mode, bands_hz=self.bands_hz, views=self.retained_views or [])
        if len(set(self.workers)) != len(self.workers):
            raise ValueError("Workers must be unique")
        return self


def native_measure(req):
    path = Path(req.path).expanduser()
    if not path.is_file():
        raise ValueError("Native source must be a regular audio file")
    with path.open("rb") as source:
        raw = source.read(96 * 1024**2 + 1)
    if len(raw) > 96 * 1024**2 or hashlib.sha256(raw).hexdigest() != req.source_sha256:
        raise ValueError("Source byte budget or identity mismatch")
    with sf.SoundFile(BytesIO(raw)) as source:
        n, rate, channels = len(source), source.samplerate, source.channels
        if (
            not 2 <= n <= rate * 60
            or rate > 192000
            or channels > 2
            or n * channels > 24_000_000
        ):
            raise ValueError("Decoded source exceeds spectral budget")
        samples = source.read(dtype="float32", always_2d=True)
    aperture = file_aperture(
        path, mode=req.mode, bands_hz=req.bands_hz, source_bytes=raw
    )
    if aperture["source_sha256"] != req.source_sha256:
        raise ValueError("Source changed during admission")
    if aperture["decision"]["outcome"] != "permitted":
        return {"outcome": "refused", "aperture": aperture}
    features, views, payloads, observations = [], [], {}, []
    evidence_id, clock_id, aperture_id = (
        identity("evidence"),
        identity("clock"),
        identity("aperture"),
    )
    total = 0
    aperture["band_measurements"] = []

    def cancelled():
        checkpoint()
        return False

    for index, level in enumerate(pyramid(samples, rate, cancelled=cancelled)):
        window = level["window_samples"]
        view_id = f"view:{index}"
        view = dict(
            view_id=view_id,
            kind="complex_stft",
            settings=dict(
                window="scipy.signal.windows.hann(sym=False)",
                hop=max(1, window // 2),
                fft_length=window,
                scaling="magnitude",
                boundary="zeros",
                f_min=0,
                f_max=rate / 2,
            ),
            state="omitted",
            reason=level.get("reason", "Audio derivative retention not requested"),
            losses=[],
            **{"for": "native"},
        )
        if level["state"] == "available":
            view["settings"]["hop"] = level["hop"]
            frequency = np.arange(level["complex"].shape[1]) * rate / window
            for band_index, (lower, upper) in enumerate(aperture["request"]["bands_hz"]):
                mask = (frequency >= lower) & (frequency < upper)
                if upper == rate / 2:
                    mask |= frequency == upper
                bins = int(mask.sum())
                measured = dict(window_samples=window, band_hz=[lower, upper], bin_count=bins,
                                bin_spacing_hz=rate / window, observed_seconds=n / rate,
                                status="measured" if bins else "undetermined")
                aperture["band_measurements"].append(measured)
                if not bins:
                    measured["reason"] = "No transform bin falls in this requested band at this window length"
                    continue
                band_value = float(np.sum(np.abs(level["complex"][:, mask, :]) ** 2))
                measured["spectral_energy_sum"] = band_value
                band_obs = identity("observation")
                observations.append(dict(id=band_obs, evidence_id=evidence_id, aperture_id=aperture_id,
                    clock_id=clock_id, source_interval_s=[0, n / rate], spectral_interval_hz=[lower, upper],
                    method=f"ShortTimeFFT Hann {window} samples, hop {level['hop']}; bins restricted to requested band",
                    method_version="oida/pyramid/v1",
                    access=[dict(observer_ref="oida:dsp", access="native_computable", basis="Calculated from verified sampled bytes")],
                    metrics=[dict(name="requested_band_energy_sum", value=band_value, unit="normalized_amplitude_squared")]))
                features.append(dict(namespace="akouo.agent-native", name=f"window_{window}_requested_band_{band_index}_energy",
                    category="measured", value=dict(status="known", value=band_value, unit="normalized_amplitude_squared"),
                    claim=dict(statement=f"Window {window} samples: squared STFT magnitudes in {bins} bins within [{lower:g}, {upper:g}] Hz sum to {band_value:.8g}; bin sampling and leakage limit this estimate.",
                               confidence="undetermined", source="dsp", evidence_refs=[band_obs], actionability="informational")))
            metric = float(sum(values.sum() for values in level["band_power"].values()))
            obs_id = identity("observation")
            features.append(
                dict(
                    namespace="akouo.agent-native",
                    name=f"window_{window}_spectral_energy",
                    category="measured",
                    value=dict(
                        status="known",
                        value=metric,
                        unit="normalized_amplitude_squared",
                    ),
                    claim=dict(
                        statement=f"Window {window} samples: sum of squared STFT magnitudes is {metric:.8g}.",
                        confidence="undetermined",
                        source="dsp",
                        evidence_refs=[obs_id],
                        actionability="informational",
                    ),
                )
            )
            metrics = [
                dict(
                    name="spectral_energy_sum",
                    value=metric,
                    unit="normalized_amplitude_squared",
                )
            ]
            metrics.extend(
                dict(
                    name=name + "_power_sum",
                    value=float(values.sum()),
                    unit="normalized_amplitude_squared",
                )
                for name, values in level["band_power"].items()
            )
            metrics.extend(
                [
                    dict(
                        name="ridge_frequency_mean",
                        value=float(level["ridge_hz"].mean()),
                        unit="Hz",
                    ),
                    dict(
                        name="positive_energy_flux_sum",
                        value=float(level["onset_energy"].sum()),
                        unit="normalized_amplitude_squared",
                    ),
                ]
            )
            observations.append(
                dict(
                    id=obs_id,
                    evidence_id=evidence_id,
                    aperture_id=aperture_id,
                    clock_id=clock_id,
                    source_interval_s=[0, n / rate],
                    spectral_interval_hz=[0, rate / 2],
                    method=f"ShortTimeFFT Hann {window} samples, hop {level['hop']}, magnitude scaling",
                    method_version="oida/pyramid/v1",
                    metrics=metrics,
                    access=[
                        dict(
                            observer_ref="oida:dsp",
                            access="native_computable",
                            basis="Calculated from verified sampled bytes",
                        )
                    ],
                )
            )
            if req.memory == "record_audio" and req.derivatives_permitted and (req.retained_views is None or "complex_stft" in req.retained_views):
                # NPY overhead is bounded and total selected bytes are admitted before encoding.
                array = level["complex"]
                if total + array.nbytes + 256 <= 64 * 1024**2:
                    stream = BytesIO()
                    np.save(stream, array, allow_pickle=False)
                    payloads[view_id] = stream.getvalue()
                    total += len(payloads[view_id])
                    view.update(
                        state="retained",
                        dtype=array.dtype.name,
                        shape=list(array.shape),
                        byte_count=len(payloads[view_id]),
                        expanded_bytes=array.nbytes,
                        sha256=hashlib.sha256(payloads[view_id]).hexdigest(),
                        axis_order=["channel", "frequency", "time"],
                        axis_units=["channel", "Hz", "s"],
                        time_origin_s=window / (2 * rate) if window == n else 0,
                        phase_convention="ShortTimeFFT phase_shift=0; negative FFT exponent",
                        reference_level="digital full scale",
                        transforms=[],
                    )
                    view["object_ref"] = (
                        "akousmata://objects/" + view["sha256"] + ".npy"
                    )
                    view.pop("reason")
                else:
                    view["reason"] = "Selected derivative total exceeds 64 MiB"
                del array
        views.append(view)
        level.clear()
    for task in dict.fromkeys(req.workers):
        from oida.spectral_specialists import run
        from akousmata_app.derivatives import inspect_numeric

        view = dict(
            view_id="view:" + task,
            kind="nsgt" if task == "nsgt" else "scattering",
            state="omitted",
            reason="Worker unavailable",
            losses=[],
            **{"for": "native"},
            settings=dict(
                window="unavailable",
                hop=1,
                fft_length=n,
                scaling="unavailable",
                boundary="unavailable",
                f_min=0,
                f_max=rate / 2,
            ),
        )
        try:
            data, metadata = run(task, samples, rate)
            view.update(metadata)
            view["reason"] = "Audio derivative retention not requested"
            if (
                req.memory == "record_audio"
                and req.derivatives_permitted
                and (req.retained_views is None or view["kind"] in req.retained_views)
                and total + len(data) <= 64 * 1024**2
            ):
                meta = inspect_numeric(data)
                view.update({k: v for k, v in meta.items() if k != "content_type"})
                view.update(
                    state="retained",
                    object_ref="akousmata://objects/" + meta["sha256"] + ".npy",
                    axis_order=["channel", "coefficient", "time"],
                    axis_units=["channel", "index", "index"],
                    time_origin_s=0,
                    phase_convention="Native NSGT complex phase"
                    if task == "nsgt"
                    else "Phase removed by modulus",
                    reference_level="digital full scale",
                    transforms=[task + " isolated numeric worker"],
                )
                view.pop("reason")
                payloads[view["view_id"]] = data
                total += len(data)
            elif req.retained_views is not None and view["kind"] not in req.retained_views:
                view["reason"] = "View was not selected for retention"
            elif req.memory == "record_audio":
                view["reason"] = "Selected derivative total exceeds 64 MiB"
            del data
        except (ValueError, OSError) as exc:
            view["reason"] = str(exc)
        views.append(view)
    if not features:
        return dict(
            outcome="unavailable",
            aperture=aperture,
            views=[
                dict(
                    view_id=v["view_id"],
                    state="omitted",
                    reason="No pyramid level admitted",
                )
                for v in views
            ],
        )
    access = dict(
        contract="earworm/listening-access/v1",
        declaration_id=identity("access"),
        subject_ref=req.source_sha256,
        capture=unknown("File bytes do not establish physical capture support"),
        sampled_representation=dict(
            status="known",
            representation_ref=aperture["request"]["representation_ref"],
            sample_rate_hz=rate,
            channels=channels,
            retained_band_hz=dict(lower=0, upper=rate / 2),
            evidence_refs=[req.source_sha256],
        ),
        model_input=dict(status="not_applicable", reason="DSP only; no model input"),
        human_access=[unknown("No playback or person-specific audibility evidence")],
    )
    output = report(req.source_sha256, access, features, inputs=[req.source_sha256])
    record = account(output, access)
    record["schema_version"] = "1.8.0"
    record["listening"]["akouo.agent-native"] = record["listening"].pop(
        "oida.agent-report"
    )
    record["auditum"]["listenings"][0]["report_namespace"] = "akouo.agent-native"
    bundle = dict(
        contract="earworm/spectral-bundle/v1",
        bundle_id=identity("bundle"),
        subject_ref=req.source_sha256,
        excerpt_sha256=hashlib.sha256(samples.tobytes()).hexdigest(),
        record_ref=record["akousma_id"],
        sampled_representation=aperture["request"]["representation_ref"],
        source_interval_samples=[0, n],
        effective_rate_hz=rate,
        channel_layout=[f"channel_{c}" for c in range(channels)],
        analysis_version="oida/pyramid/v1",
        views=views,
    )
    evidence = dict(
        contract="akouo/agent-native-evidence/v1",
        status="unvalidated",
        record_note="Owner DSP measurement; structural and reference validation is separate from truth.",
        clocks=[
            dict(
                id=clock_id,
                domain="source",
                unit="s",
                origin="first decoded source sample",
                alignment_note="One source sample clock; no inter-device alignment claimed",
            )
        ],
        evidence_objects=[
            dict(
                id=evidence_id,
                register="digital_waveform",
                artifact_ref=req.source_sha256,
                clock_id=clock_id,
                parent_ids=[],
                units="normalized_amplitude",
                sample_rate_hz=rate,
                band_basis="Sampled Nyquist ceiling; physical capture unknown",
                transform_note="soundfile float32 decoding",
            )
        ],
        apertures=[
            dict(
                id=aperture_id,
                evidence_id=evidence_id,
                observer_ref="oida:dsp",
                status="available",
                available_band_hz=[0, rate / 2],
                phase_preserved=True,
                limitations=["No physical capture calibration"],
            )
        ],
        observations=observations,
        relations=[],
        human_projections=[],
        limitations=[
            "No model inference, physical bandwidth qualification or human playback.",
            "Window bin spacing is not universal uncertainty.",
        ],
    )
    references = {req.source_sha256, "oida:dsp", output["listening_pass_id"]}
    for view in views:
        if view["state"] != "retained":
            continue
        identifier = identity("view-evidence")
        references.add(view["object_ref"])
        evidence["evidence_objects"].append(
            dict(
                id=identifier,
                register="sonic_analysis",
                artifact_ref=view["object_ref"],
                clock_id=clock_id,
                parent_ids=[evidence_id],
                units="native coefficient",
                band_basis="Transform settings and declared losses",
                transform_note=view["settings"]["window"],
            )
        )
        evidence["relations"].append(
            dict(
                id=identity("relation"),
                from_ref=identifier,
                to_ref=evidence_id,
                kind="derived_from",
                basis=view["settings"]["window"],
                evidence_refs=[identifier],
            )
        )
    for observation, feature in zip(observations, output["features"]):
        analysis_id, analysis_aperture = identity("analysis"), identity("aperture")
        artifact = "measurement:" + observation["id"]
        references.add(artifact)
        evidence["evidence_objects"].append(
            dict(
                id=analysis_id,
                register="sonic_analysis",
                artifact_ref=artifact,
                clock_id=clock_id,
                parent_ids=[evidence_id],
                units="per-metric units",
                band_basis="Computed on the declared source samples",
                transform_note=observation["method"],
            )
        )
        evidence["apertures"].append(
            dict(
                id=analysis_aperture,
                evidence_id=analysis_id,
                observer_ref="oida:dsp",
                status="available",
                available_band_hz=[0, rate / 2],
                phase_preserved=False,
                limitations=[
                    "Summary metrics omit complex phase; retained complex views are separate"
                ],
            )
        )
        evidence["relations"].append(
            dict(
                id=identity("relation"),
                from_ref=analysis_id,
                to_ref=evidence_id,
                kind="derived_from",
                basis=observation["method"],
                evidence_refs=[analysis_id],
            )
        )
        observation.update(evidence_id=analysis_id, aperture_id=analysis_aperture)
        observation.update(
            external_claim_ref=feature["claim"]["claim_id"],
            external_pass_ref=output["listening_pass_id"],
        )
        references.add(feature["claim"]["claim_id"])

    def validate_native(value):
        return native_evidence_errors(
            value,
            resolve_reference=lambda ref: ref in references,
            admit_access=lambda obs, entry: (
                entry["observer_ref"] == "oida:dsp" and obs in evidence["observations"]
            ),
        )

    record["extensions"]["akouo.agent-native"] = evidence
    record["extensions"]["oida.aperture"] = aperture
    if req.memory != "none":
        record["extensions"]["oida.spectral"] = bundle
    from akousma import validation_errors

    errors = validation_errors(record, validate_native=validate_native)
    if errors:
        raise ValueError("; ".join(errors))
    return dict(
        outcome="measured",
        record=record,
        report=output,
        aperture=aperture,
        payloads=payloads,
        bundle=bundle,
        validate_native=validate_native,
        source_bytes=raw,
    )


def native_router(
    operations,
    journal,
    preflight,
    retention_preflight,
    incognito=lambda: False,
    policy_snapshot=lambda: {},
):
    router = APIRouter()
    from oida.spectral_specialists import recover_temporary
    recover_temporary()
    @router.get("/sources/agent-native/capabilities")
    def capabilities():
        from oida.spectral_specialists import capabilities as workers

        return dict(
            **__import__("oida.station_aperture", fromlist=["capabilities"]).capabilities(),
            source_kinds=["file"],
            workers=workers(),
            reasoning_endpoint="/situated/decide",
            non_audio_inputs=dict(
                status="unavailable", reason="No admitted non-audio adapter"
            ),
        )

    @router.post("/sources/agent-native/preview")
    def preview(req: PreviewRequest):
        return preview_aperture(req)

    @router.post("/sources/agent-native")
    def listen(req: NativeRequest):
        def execute():
            if not admission.acquire(blocking=False):
                raise HTTPException(409, "Native DSP resource slot is busy")
            try:
                if incognito() and req.memory != "none":
                    raise HTTPException(
                        423, "Incognito refuses persistent native evidence"
                    )
                duration = sf.info(req.path).duration
                preflight("file", duration, None, req.memory != "none")
                if req.memory == "record_audio":
                    retention_preflight("file", duration, None, True)
                    if (
                        not req.derivatives_permitted
                        or req.expires_at is None
                        or req.expires_at <= time.time()
                    ):
                        raise ValueError(
                            "Explicit derivative permission and audio retention deadline required"
                        )
                result = native_measure(req)
                if result["outcome"] != "measured":
                    return result
                result["record"]["extensions"]["oida.native-policy"] = dict(
                    permission_ref=req.permission_ref,
                    memory=req.memory,
                    derivatives_permitted=req.derivatives_permitted,
                    expires_at=req.expires_at if req.memory == "record_audio" else None,
                    covenant=policy_snapshot(),
                )
                preflight(
                    "file",
                    result["aperture"]["samples"] / result["aperture"]["sample_rate"],
                    None,
                    req.memory != "none",
                )
                if req.memory == "record_audio":
                    retention_preflight("file", None, None, True)
                checkpoint(seal=True)
                if req.memory != "none":
                    from akousma import AkousmataStore

                    with AkousmataStore() as store:
                        if req.memory == "record_audio":
                            from akousmata_app.derivatives import publish_bundle

                            result["record"] = publish_bundle(
                                store,
                                result["record"],
                                result["payloads"],
                                memory=req.memory,
                                derivatives_permitted=True,
                                expires_at=req.expires_at,
                                validate_native=result["validate_native"],
                                resolve_representation=lambda ref: result["bundle"] if ref == result["bundle"]["sampled_representation"] else None,
                                source_bytes=result["source_bytes"],
                            )
                        else:
                            store.put(
                                result["record"],
                                supported_versions=["1.8.0"],
                                validate_native=result["validate_native"],
                                resolve_object=lambda ref: None,
                                resolve_representation=lambda ref: result["bundle"] if ref == result["bundle"]["sampled_representation"] else None,
                            )
                    journal.record_reference(
                        result["record"]["akousma_id"], record=result["record"]
                    )
                    result["akousma_id"] = result["record"]["akousma_id"]
                    result["next_action"] = dict(
                        endpoint="/situated/decide",
                        record_id=result["akousma_id"],
                        status="available_on_request",
                        reason="Configured provider and disclosure policy govern interpretation; no action dispatched",
                    )
                return {
                    k: v
                    for k, v in result.items()
                    if k not in {"payloads", "validate_native", "source_bytes"}
                }
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
            finally:
                admission.release()

        return operations.run(req.operation_id, execute)

    return router, listen
