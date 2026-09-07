"""Receive MASA observations through the existing AKOUO and Earworm contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from datetime import datetime, timezone


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    source_record: dict[str, Any]
    observation_ref: str = Field(min_length=1, max_length=512)
    producer_id: str = Field(min_length=1, max_length=256)
    consent: Literal["granted", "denied", "unknown"]
    consent_ref: str = Field(min_length=1, max_length=512)
    remember: bool = False


class ObservationUnavailable(RuntimeError):
    pass


def validator(module: str | None):
    if not module or not Path(module).is_file():
        raise ObservationUnavailable(
            "configure OIDA_MASA_VALIDATOR_MODULE with the installed MASA validator entry"
        )
    uri = Path(module).resolve().as_uri()
    cache: dict[str, list[str]] = {}

    def validate(record):
        raw = json.dumps(record, allow_nan=False)
        if len(raw.encode()) > 2 * 1024 * 1024:
            return ["MASA snapshot exceeds 2 MiB"]
        key = hashlib.sha256(raw.encode()).hexdigest()
        if key not in cache:
            # Import only the owner-selected module, never a URL supplied in a record.
            code = """import {readFileSync} from 'node:fs';
const {validateMatterRecord} = await import(process.argv[1]);
const result = validateMatterRecord(JSON.parse(readFileSync(0, 'utf8')));
process.stdout.write(JSON.stringify({valid: result.valid === true}));"""
            try:
                result = subprocess.run(
                    ["node", "--input-type=module", "-e", code, uri],
                    input=raw,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=True,
                )
                valid = json.loads(result.stdout).get("valid") is True
            except (
                OSError,
                subprocess.SubprocessError,
                ValueError,
                AttributeError,
            ) as exc:
                raise ObservationUnavailable(
                    "MASA validator unavailable or failed"
                ) from exc
            cache[key] = (
                [] if valid else ["MASA structural or semantic validation failed"]
            )
        return cache[key]

    return validate


def receive_observation(req: ObservationRequest, module: str | None) -> dict:
    if req.consent != "granted":
        raise ValueError("observation source requires declared granted consent")
    from akouo_contract.masa_observations import (
        map_masa_observation,
        masa_observation_report_errors,
    )
    from akousma.observation_accounts import create_observation_account
    from akousma.record_evolution import next_record_errors

    validate = validator(module)
    errors = validate(req.source_record)
    if errors:
        raise ValueError("; ".join(errors))
    identifier = lambda label: label + ":" + str(uuid4())  # noqa: E731
    access_id, listening_id, listener = (
        identifier("access"),
        identifier("listening"),
        "oida:observation-receiver",
    )
    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    mapping = map_masa_observation(
        req.source_record,
        req.observation_ref,
        mapping_id=identifier("mapping"),
        report_id=identifier("report"),
        listener_id=listener,
        listening_pass_id=identifier("pass"),
        apparatus_ref=access_id,
        recipients=[dict(id=listener, type="agent")],
        supported_contracts=[
            "akouo/masa-observation-report/v0.1",
            "masa/0.2.0",
            "akouo/agent-report/v0.1",
        ],
        validate_masa=validate,
    )
    unknown = dict(
        status="unknown", reason="No receiving evidence establishes this scope."
    )
    absent = dict(
        status="not_applicable",
        reason="Structured observation attribution; no audio capture or model input.",
    )
    decision_id = identifier("route")
    options = dict(
        supported_contracts=[
            "earworm/akousma/v1.7",
            "earworm/auditum/v3",
            "earworm/observation-account/v1",
            "earworm/matter-context/v1",
            "akouo/masa-observation-report/v0.1",
            "earworm/listening-access/v1",
            "earworm/listening-context/v1",
        ],
        akousma_id=identifier("ak"),
        created_at=timestamp,
        originating_app="oida",
        listening_id=listening_id,
        matter_context_id=identifier("matter"),
        route_decision=dict(
            gate="inference",
            outcome="proceed",
            subject=req.observation_ref,
            reason="Retain source attribution only; no new measurement or action.",
            decided_at=timestamp,
            authority=dict(
                mode="observe_only",
                actor=listener,
                requires_confirmation=True,
                reversible=True,
            ),
            decision_id=decision_id,
            listening_id=listening_id,
            producer_contract="akouo/agent-route/v0.1",
            producer_decision_ref=decision_id,
        ),
        access=dict(
            contract="earworm/listening-access/v1",
            declaration_id=access_id,
            subject_ref=req.observation_ref,
            capture=absent,
            sampled_representation=absent,
            model_input=absent,
            human_access=[unknown],
        ),
        source_modality=unknown,
        representation=dict(
            status="known",
            kind="structured_observation",
            observation_ref=req.observation_ref,
        ),
        temporal_scope=unknown,
    )
    record = create_observation_account(
        mapping,
        options,
        lambda value: masa_observation_report_errors(value, validate_masa=validate),
    )
    record["listening"]["oida.source"] = dict(
        contract="oida/observation-admission/v1",
        payload=dict(
            producer_id=req.producer_id,
            consent=req.consent,
            consent_ref=req.consent_ref,
            retention="saved" if req.remember else "not_stored",
            source_record_ref=req.source_record["id"],
            source_observation_ref=req.observation_ref,
            source_sha256=hashlib.sha256(
                json.dumps(
                    req.source_record,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest(),
            basis="caller-declared consent; source snapshot preserves apparatus, time, policies and freshness",
        ),
    )
    errors = next_record_errors(record)
    if errors:
        raise ValueError("; ".join(errors))
    if req.remember:
        from akousma import AkousmataStore

        store = AkousmataStore()
        try:
            store.put(record)
        finally:
            store.close()
    return dict(
        contract="oida/observation-reception/v1",
        status="received",
        record=record,
        akousma_id=record["akousma_id"] if req.remember else None,
        execution="not_requested",
    )
