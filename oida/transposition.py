"""Bounded local DSP derivatives with typed recipes; no capture/model access claim."""

from __future__ import annotations
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4
import hashlib
import os

import numpy as np
import scipy
from scipy.signal import butter, sosfiltfilt, hilbert, resample_poly
import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, HTTPException
from oida.operation_control import checkpoint
from oida.observation_source import validator, ObservationUnavailable
from oida.claim_lifecycle import utc_now


class TranspositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    permission: Literal["granted", "denied", "unknown"]
    permission_ref: str = Field(min_length=1, max_length=256)
    kind: Literal[
        "filtered_resample", "playback_rate", "frequency_translation", "pitch_shift"
    ]
    lower_hz: float = Field(gt=0)
    upper_hz: float = Field(gt=0)
    target_rate: int | None = Field(default=None, ge=8000, le=192000)
    rate_ratio: float | None = Field(default=None, ge=0.25, le=4)
    offset_hz: float | None = None
    cents: float | None = Field(default=None, ge=-2400, le=2400)
    remember: bool = False


def process(samples, rate, req):
    if not 0 < req.lower_hz < req.upper_hz < rate / 2:
        raise ValueError("input band must lie strictly within sampled Nyquist")
    fields = {
        "target_rate": req.target_rate,
        "rate_ratio": req.rate_ratio,
        "offset_hz": req.offset_hz,
        "cents": req.cents,
    }
    selected = {
        "filtered_resample": "target_rate",
        "playback_rate": "rate_ratio",
        "frequency_translation": "offset_hz",
        "pitch_shift": "cents",
    }[req.kind]
    if fields[selected] is None or any(
        value is not None for key, value in fields.items() if key != selected
    ):
        raise ValueError("supply only the parameter for the selected recipe")
    output_rate = rate
    ratio = 1.0
    offset = 0.0
    parameters = {}
    if req.kind == "filtered_resample":
        output_rate = req.target_rate
        if output_rate == rate or len(samples) * output_rate % rate:
            raise ValueError(
                "resampling needs a new rate and exact duration on the output sample grid"
            )
    elif req.kind == "playback_rate":
        ratio = req.rate_ratio
        output_rate = round(rate * ratio)
        parameters = {"rate_ratio": ratio}
        if (
            output_rate != rate * ratio
            or ratio == 1
            or not 8000 <= output_rate <= 192000
        ):
            raise ValueError(
                "playback ratio must produce a distinct supported integer sample rate"
            )
    elif req.kind == "frequency_translation":
        offset = req.offset_hz
        parameters = {"offset_hz": offset}
    else:
        ratio = 2 ** (req.cents / 1200)
        parameters = {"cents": req.cents}
        if ratio == 1:
            raise ValueError("pitch shift must change pitch")
    band = {
        "lower": req.lower_hz * ratio + offset,
        "upper": req.upper_hz * ratio + offset,
    }
    if not 0 < band["lower"] < band["upper"] < output_rate / 2:
        raise ValueError("output band crosses zero or sampled Nyquist")
    filtered = sosfiltfilt(
        butter(
            8, [req.lower_hz, req.upper_hz], btype="bandpass", fs=rate, output="sos"
        ),
        samples,
        axis=0,
    )
    checkpoint()
    if req.kind == "filtered_resample":
        from oida.dsp import _resample

        output = _resample(filtered, rate, output_rate)
    elif req.kind == "frequency_translation":
        phase = np.exp(2j * np.pi * offset * np.arange(len(filtered)) / rate)[:, None]
        output = np.real(hilbert(filtered, axis=0) * phase)
    elif req.kind == "pitch_shift":
        factor = Fraction(1 / ratio).limit_denominator(4096)
        # Preserve the declared physical Hz ratio to the rational DSP precision.
        if abs(float(factor) - 1 / ratio) > 1e-7:
            raise ValueError("pitch ratio exceeds supported rational precision")
        output = resample_poly(filtered, factor.numerator, factor.denominator, axis=0)
    else:
        output = filtered
    if not np.isfinite(output).all():
        raise ValueError("nonfinite DSP output")
    parameters.update(
        prefilter_order=8,
        prefilter_mode="sosfiltfilt zero-phase",
        resample_window="kaiser beta=5",
        output_encoding="WAV float32",
    )
    recipe = dict(
        contract="earworm/transposition-recipe/v1",
        kind=req.kind,
        algorithm={
            "name": "8th-order Butterworth bandpass + " + req.kind,
            "version": scipy.__version__,
        },
        input_window_s={"start": 0.0, "end": len(samples) / rate},
        input_band_hz={"lower": req.lower_hz, "upper": req.upper_hz},
        output_band_hz=band,
        duration_behavior="preserved"
        if req.kind in ("filtered_resample", "frequency_translation")
        else "changed",
    )
    return output, output_rate, recipe, parameters


def matter_receipt(req, recipe, parameters, source_info, output_info, started_at=None):
    def new():
        return "urn:uuid:" + str(uuid4())

    def known(value):
        return dict(state="known", value=value)

    def unknown(reason):
        return dict(state="unknown", reason=reason)

    record_id, actor, policy, rule, operation, input_id, output_id = [
        new() for _ in range(7)
    ]
    now = utc_now()
    reps = []
    for identifier, role, info in [
        (input_id, "source-representation", source_info),
        (output_id, "render", output_info),
    ]:
        rate, frames, channels, digest = info
        reps.append(
            dict(
                id=identifier,
                type="masa:Representation",
                role=role,
                mediaType="audio/wav",
                format=known("WAV audio; verified local bytes"),
                availability="withheld",
                locator=dict(
                    state="withheld",
                    reason="Local file locator is retained only in the owner operation response",
                    policyRefs=[policy],
                ),
                integrity=known({"sha256": digest}),
                policyRefs=[policy],
                disclosure="private",
                extensions={},
                audio=dict(
                    sampleRateHz=known(rate),
                    durationSeconds=known(frames / rate),
                    channels=known(channels),
                    bitDepth=unknown(
                        "Input encoding is not inferred from its extension"
                    ),
                    encoding=unknown("See verified WAV bytes"),
                    spatialFormat=unknown(
                        "Channel count does not establish spatial format"
                    ),
                    levelContext=unknown("No calibrated capture or playback level"),
                ),
            )
        )
    event = dict(
        id=operation,
        type="masa:OperationReceipt",
        recordId=record_id,
        sequence=0,
        operationType={
            "filtered_resample": "earworm:resample",
            "playback_rate": "earworm:playback_rate",
            "frequency_translation": "earworm:frequency_translate",
            "pitch_shift": "matter.pitchshift",
        }[req.kind],
        effectClass="derive",
        finalStatus="completed",
        startedAt=started_at or now,
        endedAt=now,
        actors=[actor],
        inputs=[input_id],
        outputs=[output_id],
        tool=known(
            dict(
                id=new(),
                name="Oida SciPy transposition adapter",
                version=known(scipy.__version__),
                kind="software",
            )
        ),
        parameters=parameters,
        policyEvaluation=dict(
            action=req.kind,
            targets=[output_id],
            policyRefs=[policy],
            result="permitted",
            evaluatedAt=now,
            evaluator=actor,
            authorityRefs=[rule],
            reasons=["Admitted owner request declared permission for this derivative"],
        ),
        reversibility="irreversible",
        determinism={"state": "deterministic"},
        warnings=[
            "Finite filter rolloff; no ideal brick-wall or perceptual equivalence claim"
        ],
        errors=[],
        claimRefs=[],
        extensions={"earworm:transposition": recipe},
    )
    record = dict(
        masaVersion="0.2.0",
        id=record_id,
        type="masa:MatterRecord",
        revision=1,
        profiles=["core", "audio"],
        createdAt=now,
        createdBy=actor,
        title="Local sampled-audio derivative",
        actors=[
            dict(
                id=actor,
                type="masa:Actor",
                actorKind="software",
                roles=["record-creator"],
                name=known("Oida local operator adapter"),
                disclosure="private",
                extensions={},
            )
        ],
        representations=reps,
        policies=[
            dict(
                id=policy,
                type="masa:Policy",
                policyKind="composite",
                issuer=actor,
                status="active",
                disclosure="private",
                rules=[
                    dict(
                        id=rule,
                        effect="permission",
                        actions=[req.kind],
                        targets=[input_id, output_id],
                        subjects=[actor],
                        authorityBasis=known(req.permission_ref),
                        constraints={"network": "prohibited"},
                        duties=["Preserve lineage"],
                    )
                ],
                review=dict(
                    contact=unknown("Local owner review"),
                    route=known("Inspect owner operation receipt"),
                ),
                extensions={},
            )
        ],
        relations=[
            dict(
                id=new(),
                type="masa:Relation",
                subject=output_id,
                predicate="masa:derived-from",
                object=input_id,
                assertedBy=actor,
                createdAt=now,
                basis=[{"ref": operation, "role": "operation"}],
                operationRef=operation,
                extensions={},
            )
        ],
        integrity=unknown("External local assets; this record is not an asset bundle"),
        history={"mode": "embedded", "events": [event]},
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
        "measurements",
        "observations",
        "mappings",
        "contexts",
        "agentRuns",
        "capabilities",
    ):
        record[key] = []
    record["$schema"] = (
        "https://masa.sonicfield.org/schemas/0.2.0/matter-record.schema.json"
    )
    return record


def graph_account(record, req, final, out_rate, frames, channels, digest, validate):
    import subprocess
    import json
    from akousma import new_akousma
    from akousma.transformation_graph import create_transformation_graph
    from akousma.graph_records import create_graph_record
    from akousma.record_evolution import next_record_errors

    module = Path(os.environ["OIDA_MASA_VALIDATOR_MODULE"]).resolve().as_uri()
    code = "const core=await import(import.meta.resolve('@sonicfield/masa',process.argv[1])); process.stdout.write(JSON.stringify(core.lineageRelationDirections));"
    try:
        result = subprocess.run(
            [
                "node",
                "--experimental-import-meta-resolve",
                "--input-type=module",
                "-e",
                code,
                module,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ObservationUnavailable(
            "Selected MASA lineage registry is unavailable"
        ) from exc
    directions = json.loads(result.stdout)
    if not isinstance(directions, dict):
        raise ObservationUnavailable("Selected MASA lineage registry is invalid")
    audio = {
        "asset_id": "sha256:" + digest,
        "uri": final.as_uri(),
        "sample_rate": out_rate,
        "duration_seconds": frames / out_rate,
        "channels": channels,
    }
    identifier = new_akousma(audio=audio, originating_app="oida")["akousma_id"]
    contracts = [
        "earworm/akousma/v1.7",
        "earworm/transformation-graph/v1",
        "masa/0.2.0",
        "earworm/transposition-recipe/v1",
    ]
    graph = create_transformation_graph(
        record,
        dict(
            graph_id=identifier,
            revision=1,
            authored_by="oida:transposition",
            nodes=[
                dict(node_id="node:" + str(i), representation_ref=r["id"])
                for i, r in enumerate(record["representations"])
            ],
            supported_contracts=contracts,
        ),
        validate_masa=validate,
        lineage_directions=directions,
    )
    account = create_graph_record(
        graph,
        dict(
            created_at=utc_now(), originating_app="oida", supported_contracts=contracts
        ),
        validate_masa=validate,
        lineage_directions=directions,
    )
    account.update(
        audio=audio,
        summary="Sampled audio " + req.kind + " derivative; no listening claimed",
    )
    errors = next_record_errors(account)
    if errors:
        raise ValueError("; ".join(errors))
    return account, directions


def transposition_router(root, operations, journal, preflight):
    router = APIRouter()

    def execute(req):
        if req.permission != "granted":
            raise HTTPException(423, "explicit derivative permission required")
        preflight("file", None, None, req.remember)
        path = Path(req.path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("local source unavailable or exceeds 32 MiB")
        with path.open("rb") as source:
            raw = source.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("source exceeds 32 MiB")
        if hashlib.sha256(raw).hexdigest() != req.source_sha256:
            raise ValueError("source bytes do not match declared hash")
        with sf.SoundFile(BytesIO(raw)) as source:
            if (
                source.format != "WAV"
                or source.channels > 2
                or source.samplerate > 192000
                or not 0.1 <= len(source) / source.samplerate <= 10
            ):
                raise ValueError(
                    "source must be WAV, at most two channels and 0.1–10 seconds"
                )
            samples = source.read(dtype="float64", always_2d=True)
            rate = source.samplerate
        preflight("file", len(samples) / rate, None, req.remember)
        started_at = utc_now()
        output, out_rate, recipe, parameters = process(samples, rate, req)
        destination = root / "transpositions"
        destination.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=destination) as tmp:
            staged = Path(tmp) / "output.wav"
            sf.write(staged, output, out_rate, subtype="FLOAT")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            record = matter_receipt(
                req,
                recipe,
                parameters,
                (rate, len(samples), samples.shape[1], req.source_sha256),
                (out_rate, len(output), output.shape[1], digest),
                started_at,
            )
            from akousma.transposition_recipes import transposition_recipe_errors

            validate = validator(os.environ.get("OIDA_MASA_VALIDATOR_MODULE"))
            errors = validate(record) + transposition_recipe_errors(record)
            if errors:
                raise ValueError("; ".join(errors))
            final = destination / (req.operation_id + ".wav")
            account, directions = (
                graph_account(
                    record,
                    req,
                    final,
                    out_rate,
                    len(output),
                    output.shape[1],
                    digest,
                    validate,
                )
                if req.remember
                else (None, None)
            )
            preflight("file", len(samples) / rate, None, req.remember)
            checkpoint(seal=True)
            if final.exists():
                raise ValueError(
                    "output already exists; inspect its original operation"
                )
            staged.rename(final)
            identifier = None
            if req.remember:
                from akousma import AkousmataStore

                store = AkousmataStore()
                try:
                    store.put(
                        account, validate_masa=validate, lineage_directions=directions
                    )
                finally:
                    store.close()
                identifier = account["akousma_id"]
                journal.record_reference(identifier, record=account)
            return dict(
                contract="oida/transposition-result/v1",
                path=str(final),
                source_sha256=req.source_sha256,
                output_sha256=digest,
                record=record,
                derivation_ref=record["history"]["events"][0]["id"],
                akousma_id=identifier,
                interpretation="sampled derivative; no capture bandwidth, native model understanding or human hearing is established",
            )

    @router.post("/sources/transpositions")
    def transpose(req: TranspositionRequest):
        try:
            return operations.run(req.operation_id, lambda: execute(req))
        except ObservationUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
