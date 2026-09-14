"""Bounded digital band energy with source-preserving MASA/Earworm evidence."""

from io import BytesIO
from pathlib import Path
import hashlib
from uuid import uuid4
import numpy as np
import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, HTTPException
from oida.operation_control import checkpoint
from oida.observation_source import validator, ObservationUnavailable
from oida.agent_reports import report, account, unknown, identity, render
from oida.claim_lifecycle import utc_now


class SpectrumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lower_hz: float = Field(gt=0)
    upper_hz: float = Field(gt=0)
    channel: int = Field(default=0, ge=0)
    start_s: float = Field(default=0, ge=0)
    end_s: float = Field(gt=0, le=60)
    permission: str = Field(pattern="^granted$")
    permission_ref: str = Field(min_length=1, max_length=256)
    evidence_class: str = Field(
        default="capture_unverified",
        pattern="^(synthetic_declared|capture_unverified)$",
    )
    remember: bool = False
    recipients: list[dict] | None = None


def measure(req):
    path = Path(req.path).expanduser().resolve()
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError("Source exceeds 64 MiB")
    if hashlib.sha256(raw).hexdigest() != req.source_sha256:
        raise ValueError("Source hash mismatch")
    with sf.SoundFile(BytesIO(raw)) as src:
        rate = src.samplerate
        channels = src.channels
        if (
            src.format != "WAV"
            or rate > 192000
            or channels > 8
            or req.channel >= channels
        ):
            raise ValueError("Unsupported WAV rate/channel")
        first = round(req.start_s * rate)
        last = round(req.end_s * rate)
        if first >= last or last > len(src):
            raise ValueError("Window must fit source")
        if (
            abs(first / rate - req.start_s) > 1e-9
            or abs(last / rate - req.end_s) > 1e-9
        ):
            raise ValueError("Window must align with samples")
        src.seek(first)
        samples = src.read(last - first, always_2d=True, dtype="float64")[
            :, req.channel
        ]
    if not np.isfinite(samples).all():
        raise ValueError("Nonfinite samples")
    duration = len(samples) / rate
    resolution = 1 / duration
    if not 0 < req.lower_hz < req.upper_hz < rate / 2:
        raise ValueError("Band must lie inside sampled Nyquist")
    if req.lower_hz * duration < 5 or (req.upper_hz - req.lower_hz) < 2 * resolution:
        raise ValueError(
            "Insufficient resolution: require five cycles at lower edge and two frequency bins across band"
        )
    window = np.hanning(len(samples))
    normalization = np.sum(window**2)
    if normalization <= 0:
        raise ValueError("Window too short")
    power = abs(np.fft.rfft(samples * window)) ** 2 / (len(samples) * normalization)
    power[1 : -1 if len(samples) % 2 == 0 else None] *= 2
    frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
    select = (frequencies >= req.lower_hz) & (frequencies < req.upper_hz)
    energy = float(power[select].sum())
    checkpoint()
    return dict(
        energy=energy,
        rate=rate,
        channels=channels,
        duration=duration,
        resolution=resolution,
        first=first,
        last=last,
    )


def material(req, measured):
    def new():
        return "urn:uuid:" + str(uuid4())

    def known(v):
        return dict(state="known", value=v)

    now = utc_now()
    mid, actor, representation, measurement = new(), new(), new(), new()
    m = dict(
        id=measurement,
        type="masa:Measurement",
        about=representation,
        metric="digital_band_mean_square",
        value=measured["energy"],
        unit="sample^2",
        method=dict(
            name="Hann one-sided periodogram band integration",
            version=known("1"),
            parameters=dict(
                channel=req.channel,
                lower_hz=req.lower_hz,
                upper_hz=req.upper_hz,
                frequency_bin_hz=measured["resolution"],
                window="symmetric Hann",
                detrend="none",
                normalization="sum(abs(rfft(x*w))^2) / (N*sum(w^2)); double interior one-sided bins",
                band_edges="lower inclusive; upper exclusive",
            ),
        ),
        window=dict(kind="temporal", unit="s", start=req.start_s, end=req.end_s),
        actor=actor,
        createdAt=now,
        uncertainty=[
            "Finite-window leakage and bin quantization; no physical calibration or semantic measurement."
        ],
        extensions={},
    )
    r = dict(
        masaVersion="0.2.0",
        id=mid,
        type="masa:MatterRecord",
        revision=1,
        profiles=["core", "audio", "analysis"],
        createdAt=now,
        createdBy=actor,
        title="Local digital band measurement",
        actors=[
            dict(
                id=actor,
                type="masa:Actor",
                actorKind="software",
                roles=["record-creator"],
                name=known("Oida digital spectrum adapter"),
                disclosure="private",
                extensions={},
            )
        ],
        representations=[
            dict(
                id=representation,
                type="masa:Representation",
                role="source-representation",
                mediaType="audio/wav",
                format=known("WAV"),
                availability="unknown",
                locator=dict(
                    state="unknown", reason="Owner-local source path is excluded"
                ),
                integrity=known({"sha256": req.source_sha256}),
                policyRefs=[],
                disclosure="private",
                extensions={},
                audio=dict(
                    sampleRateHz=known(measured["rate"]),
                    channels=known(measured["channels"]),
                ),
            )
        ],
        measurements=[m],
        integrity=dict(state="unknown", reason="No external asset bundle"),
        history=dict(mode="embedded", events=[]),
        disclosure="private",
        registers=["digital-technical"],
        scales=["object-event"],
        extensions={},
    )
    for key in (
        "sources",
        "encounters",
        "apertures",
        "listeningPasses",
        "claims",
        "regions",
        "observations",
        "mappings",
        "contexts",
        "agentRuns",
        "capabilities",
        "policies",
        "relations",
    ):
        r[key] = []
    r["$schema"] = "https://masa.sonicfield.org/schemas/0.2.0/matter-record.schema.json"
    policy = new()
    r["policies"] = [
        dict(
            id=policy,
            type="masa:Policy",
            policyKind="composite",
            issuer=actor,
            status="active",
            disclosure="private",
            rules=[
                dict(
                    id=new(),
                    effect="permission",
                    actions=["analyze"],
                    targets=[representation],
                    subjects=[actor],
                    authorityBasis=known(req.permission_ref),
                    constraints={},
                    duties=[],
                )
            ],
            review=dict(
                contact=dict(state="unknown", reason="Local owner"),
                route=known("Owner inspection"),
            ),
            extensions={},
        )
    ]
    rep = r["representations"][0]
    rep.update(
        availability="withheld",
        policyRefs=[policy],
        locator=dict(state="withheld", reason="Local owner path", policyRefs=[policy]),
    )
    for key in (
        "durationSeconds",
        "bitDepth",
        "encoding",
        "spatialFormat",
        "levelContext",
    ):
        rep["audio"][key] = dict(
            state="unknown", reason="Not established by this band measurement"
        )
    return r, representation, measurement


def sector_router(operations, journal, preflight, module):
    router = APIRouter()

    def execute(req):
        preflight("file", req.end_s - req.start_s, None, req.remember)
        measured = measure(req)
        source, representation, measurement = material(req, measured)
        validate = validator(module)
        errors = validate(source)
        if errors:
            raise ValueError("; ".join(errors))
        subject = "sha256:" + req.source_sha256
        access = dict(
            contract="earworm/listening-access/v1",
            declaration_id=identity("access"),
            subject_ref=subject,
            capture=unknown(
                "Physical apparatus unverified; evidence class " + req.evidence_class
            ),
            sampled_representation=dict(
                status="known",
                representation_ref=representation,
                sample_rate_hz=measured["rate"],
                channels=measured["channels"],
                retained_band_hz=dict(lower=0, upper=measured["rate"] / 2),
                evidence_refs=[subject],
            ),
            model_input=dict(
                status="known",
                model_ref="oida:deterministic-spectrum-v1",
                representation_ref=representation,
                sample_rate_hz=measured["rate"],
                channels=measured["channels"],
                effective_band_hz=dict(lower=0, upper=measured["rate"] / 2),
                window_s=dict(start=req.start_s, end=req.end_s),
                preprocessing_refs=[],
                evidence_refs=[measurement],
                blind_spots=["No physical calibration or semantic interpretation"],
            ),
            human_access=[unknown("No playback or perceptual evidence")],
        )
        feature = dict(
            namespace="oida.digital_spectrum",
            name="band_energy",
            category="measured",
            value=dict(status="known", value=measured["energy"], unit="sample^2"),
            claim=dict(
                statement=f"Selected channel digital mean-square energy in [{req.lower_hz}, {req.upper_hz}) Hz is {measured['energy']:.8g} sample^2.",
                confidence="undetermined",
                source="dsp",
                evidence_refs=[measurement],
                actionability="informational",
            ),
        )
        output = report(
            subject,
            access,
            [feature],
            inputs=[source["id"], measurement],
            recipients=req.recipients,
        )
        result = account(output, access)
        from akousma.agent_sectors import create_measurement_set
        from akousma.record_evolution import next_record_errors

        descriptor = dict(
            descriptor_id=identity("descriptor"),
            source_record_ref=source["id"],
            measurement_ref=measurement,
            feature="band_energy",
            reference_basis="digital_energy",
            band_hz=dict(status="known", lower=req.lower_hz, upper=req.upper_hz),
            declared_by="oida:structured-reporter",
            mapping_reason="Computed windowed digital energy; no physical pressure claim",
        )
        result["extensions"]["earworm_measurements"] = create_measurement_set(
            [source],
            [descriptor],
            validate,
            ["earworm/measurement-set/v1", "masa/0.2.0"],
        )
        context = result["extensions"]["earworm_listening_context"]["contexts"][0]
        result["extensions"]["earworm_agent_sector"] = dict(
            contract="earworm/agent-sector/v1",
            entries=[
                dict(
                    sector_id=identity("sector"),
                    listening_ref=context["listening_ref"],
                    subject_ref=subject,
                    access_declaration_ref=access["declaration_id"],
                    source_kind="acoustic_signal",
                    basis="retained_measurement",
                    measurement_refs=[descriptor["descriptor_id"]],
                    claim_refs=[output["features"][0]["claim"]["claim_id"]],
                    renderings=[r["rendering_id"] for r in context["renderings"]],
                )
            ],
        )
        result["listening"]["oida.digital-source"] = dict(
            contract="oida/digital-source/v1",
            payload=dict(
                evidence_class=req.evidence_class,
                permission_ref=req.permission_ref,
                source_sha256=req.source_sha256,
                resolution_hz=measured["resolution"],
                physical_support="unverified",
            ),
        )
        errors = next_record_errors(result)
        if errors:
            raise ValueError("; ".join(errors))
        preflight("file", req.end_s - req.start_s, None, req.remember)
        checkpoint(seal=True)
        identifier = None
        if req.remember:
            from akousma import AkousmataStore

            store = AkousmataStore()
            try:
                store.put(result)
            finally:
                store.close()
            identifier = result["akousma_id"]
            journal.record_reference(identifier, record=result)
        return dict(
            contract="oida/digital-sector-result/v1",
            record=result,
            report=output,
            text=render(output),
            akousma_id=identifier,
            source_sha256=req.source_sha256,
        )

    @router.post("/sources/spectrum")
    def spectrum(req: SpectrumRequest):
        def invoke():
            try:
                return execute(req)
            except ObservationUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc

        return operations.run(req.operation_id, invoke)

    return router
