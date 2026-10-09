"""Receive MASA observations through the existing AKOUO and Earworm contracts."""

from __future__ import annotations

import hashlib
import json
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
    operation_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    remember: bool = False
    claim_validity: dict[str, Any] | None = None
    claim_retention: dict[str, Any] | None = None


class ObservationUnavailable(RuntimeError):
    pass


def validator(module: str | None):
    if not module or not Path(module).is_file():
        raise ObservationUnavailable("configure OIDA_MASA_VALIDATOR_MODULE with the installed MASA validator entry")
    from akousma.masa_runtime import masa_validator
    return masa_validator(module)


def receive_observation(
    req: ObservationRequest, module: str | None, *, before_commit=None
) -> dict:
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
    from akouo_contract.agent_routes import plan_agent_route, agent_route_report_errors
    from akousma.listening_contracts import listening_access_errors

    report = mapping["report"]
    source_observation = next(
        o for o in req.source_record["observations"] if o["id"] == req.observation_ref
    )
    from oida.observation_freshness import evaluate_observation

    received_freshness = evaluate_observation(req.source_record, source_observation, now=timestamp)
    route_request = dict(
        contract="akouo/agent-route/v0.1",
        request_id=decision_id,
        profile="agent",
        supported_contracts=[
            "akouo/agent-route/v0.1",
            "akouo/agent-report/v0.1",
            "earworm/listening-access/v1",
        ],
        relation=dict(of="observation", ref=req.observation_ref),
        requested_categories=["undetermined"],
        resolved_refs=[
            req.source_record["id"],
            req.observation_ref,
            source_observation["sourceRef"],
            access_id,
            listener,
        ],
        permission_overrides=dict(
            heard_allowed=False,
            measured_allowed=False,
            inferred_allowed=False,
            interpreted_allowed=False,
            speculative_allowed=False,
            must_include_undetermined=True,
        ),
        **{
            key: report[key]
            for key in (
                "report_id",
                "listener_id",
                "listening_pass_id",
                "input_refs",
                "report_of_refs",
                "apparatus_ref",
                "recipients",
            )
        },
    )
    route = plan_agent_route(
        route_request, options["access"], validate_access=listening_access_errors
    )
    if route["decision"]["outcome"] != "proceed":
        raise ValueError(route["decision"]["reason"])
    # An omitted/deleted value has no receiving claim; never invent one to fill a report.
    if report["features"]:
        errors = agent_route_report_errors(report, route)
        if errors:
            raise ValueError("; ".join(errors))
    decision = dict(route["decision"])
    options["created_at"] = decision["decided_at"]
    decision_ref = decision.pop("id")
    options["route_decision"] = dict(
        decision,
        decision_id=decision_ref,
        listening_id=listening_id,
        producer_contract=route["contract"],
        producer_decision_ref=decision_ref,
    )
    record = create_observation_account(
        mapping,
        options,
        lambda value: masa_observation_report_errors(value, validate_masa=validate),
    )
    record["listening"]["oida.observation-route"] = dict(
        contract=route["contract"], payload=route
    )
    from oida.claim_lifecycle import apply_declaration

    claim_evaluation = apply_declaration(
        record, req.claim_validity, req.claim_retention
    )
    record["listening"]["oida.source"] = dict(
        contract="oida/observation-admission/v1",
        payload=dict(
            effective_freshness=received_freshness,
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
    from oida.operation_control import checkpoint

    if before_commit is not None:
        before_commit()
    checkpoint(seal=True)
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
        claim_evaluation=claim_evaluation,
        effective_freshness=received_freshness,
        akousma_id=record["akousma_id"] if req.remember else None,
        execution="not_requested",
    )
