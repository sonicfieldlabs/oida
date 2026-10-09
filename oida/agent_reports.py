"""A7 reports and readable text over attributable local results."""

from copy import deepcopy
from uuid import uuid4
from akouo_contract.agent_report import agent_report_errors
from akousma.record_evolution import next_record_errors
from oida.claim_lifecycle import utc_now


def identity(prefix):
    return prefix + ":" + str(uuid4())


def unknown(reason):
    return dict(status="unknown", reason=reason)


def report(subject, access, features, *, inputs, recipients=None, report_of=None):
    result = dict(
        contract="akouo/agent-report/v0.1",
        report_id=identity("report"),
        listener_id="oida:structured-reporter",
        listening_pass_id=identity("pass"),
        subject_ref=subject,
        input_refs=inputs,
        apparatus_ref=access["declaration_id"],
        report_format="structured",
        recipients=recipients or [dict(id="oida:owner", type="human")],
        report_of_refs=report_of or [],
        features=deepcopy(features),
        limitations=[
            "Digital evidence, provider attribution, human access and physical calibration remain separate."
        ],
    )
    for f in result["features"]:
        f["feature_id"] = identity("feature")
        f["claim"].update(
            claim_id=identity("claim"), listening_pass_id=result["listening_pass_id"]
        )
    errors = agent_report_errors(result)
    if errors:
        raise ValueError("; ".join(errors))
    return result


def render(report):
    return "\n".join(
        [f"Report {report['report_id']}"]
        + [
            f"{f['name']}: {f['claim']['statement']} [{f['category']}; confidence {f['claim']['confidence']}]"
            for f in report["features"]
        ]
        + report["limitations"]
    )


def account(report, access, *, audio=None):
    """Compose the existing 1.7 listening/context schemas; validate before storage."""
    now = utc_now()
    listening = identity("listening")
    rendering = identity("rendering")
    result = dict(
        akousma_id=identity("ak"),
        schema_version="1.7.0",
        created_at=now,
        subject=report["subject_ref"],
        provenance=dict(
            source_type="unknown",
            origin="unknown",
            originating_app="oida",
            created_at=now,
        ),
        listening={
            "oida.agent-report": dict(contract=report["contract"], payload=report)
        },
        lineage=dict(parent_akousma_ids=[]),
        tags=[],
        annotations={},
        summary="Local digital measurement report",
        auditum=dict(
            contract="earworm/auditum/v3",
            listenings=[
                dict(
                    listening_id=listening,
                    listener_id=report["listener_id"],
                    listener_type="agent",
                    created_at=now,
                    report_namespace="oida.agent-report",
                    contract=report["contract"],
                    listening_pass_ref=report["listening_pass_id"],
                )
            ],
            route_decisions=[],
            actions=[],
            disagreements=[],
            honest_absences=[],
        ),
        extensions=dict(
            earworm_listening_access=access,
            earworm_listening_context=dict(
                contract="earworm/listening-context/v1",
                contexts=[
                    dict(
                        listening_ref=listening,
                        subject_ref=report["subject_ref"],
                        recipients=report["recipients"],
                        access_declaration_ref=access["declaration_id"],
                        report=dict(
                            ref=report["report_id"],
                            contract=report["contract"],
                            format="structured",
                            readability=dict(status="known", value="machine_readable"),
                            human_rendering=dict(
                                status="available", rendering_refs=[rendering]
                            ),
                        ),
                        renderings=[
                            dict(
                                rendering_id=rendering,
                                source_ref=report["report_id"],
                                output_ref="text:" + report["report_id"],
                                transformation_ref="render:" + report["report_id"],
                                author_ref="oida:structured-reporter",
                                kind="interpretation",
                                media_type="text/plain",
                                access=dict(status="known", value="private"),
                            )
                        ],
                    )
                ],
                claims=[
                    dict(
                        claim_ref=f["claim"]["claim_id"],
                        listening_ref=listening,
                        validity=unknown("No validity interval declared"),
                        retention=unknown("Owner retention policy"),
                    )
                    for f in report["features"]
                ],
            ),
        ),
    )
    result["auditum"]["route_decisions"] = [
        dict(
            decision_id=identity("decision"),
            gate="inference",
            outcome="proceed",
            subject=report["subject_ref"],
            reason="Bounded local report construction; no action authority",
            decided_at=now,
            listening_id=listening,
            producer_contract=report["contract"],
            authority=dict(
                mode="observe_only",
                actor=report["listener_id"],
                requires_confirmation=True,
                reversible=True,
            ),
        )
    ]
    result["auditum"]["honest_absences"] = [
        dict(
            id=identity("absence"),
            kind="not_retained",
            subject="raw audio",
            attributed_to="owner retention boundary",
            listening_id=listening,
            count=1,
            note="No raw audio asset is retained in this account",
        )
    ]
    result["listening"]["oida.report-text"] = dict(
        contract="oida/report-text/v1",
        payload=dict(text=render(report), report_ref=report["report_id"]),
    )
    if audio:
        result["audio"] = audio
    errors = next_record_errors(result)
    if errors:
        raise ValueError("; ".join(errors))
    return result


def retained_report_router(operations, journal, preflight):
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, ConfigDict, Field
    from oida.operation_control import checkpoint

    class Request(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
        recipients: list[dict] | None = None
        remember: bool = False

    router = APIRouter()

    def execute(identifier, req):
        from akousma import AkousmataStore
        from oida.owner_journal import canonical

        preflight("file", None, None, req.remember)
        store = AkousmataStore()
        try:
            source = store.get(identifier)
            if source is None:
                raise HTTPException(404, "Unknown canonical record")
            before = canonical(source)
            access = deepcopy(
                source.get("extensions", {}).get("earworm_listening_access")
            )
            if access is None:
                access = dict(
                    contract="earworm/listening-access/v1",
                    declaration_id=identity("access"),
                    subject_ref=identifier,
                    capture=unknown("Retained account only"),
                    sampled_representation=unknown("No source bytes supplied"),
                    model_input=unknown("No new model invocation"),
                    human_access=[unknown("No perceptual evidence")],
                )
            features = []
            inputs = [identifier]
            selected = None
            for entry in source.get("listening", {}).values():
                payload = entry.get("payload", {})
                if payload.get("contract") == "akouo/agent-report/v0.1":
                    selected = payload
                    break
                if payload.get("contract") == "akouo/masa-observation-report/v0.1":
                    selected = payload["report"]
                    break
            if selected:
                # This is a second account of retained claims, not fresh measurement.
                for f in selected["features"]:
                    features.append(
                        dict(
                            namespace="oida.retained_report",
                            name=f["name"],
                            category="undetermined",
                            value=deepcopy(f["value"]),
                            claim=dict(
                                statement="Retained "
                                + f["category"]
                                + " claim: "
                                + f["claim"]["statement"],
                                confidence="undetermined",
                                source="memory",
                                evidence_refs=[identifier],
                                actionability="none",
                            ),
                        )
                    )
            else:
                provenance = []

                def collect(value):
                    if isinstance(value, dict):
                        if value.get("contract") == "oida/pass-provenance/v1":
                            provenance.append(value)
                        else:
                            for child in value.values():
                                collect(child)
                    elif isinstance(value, list):
                        for child in value:
                            collect(child)

                collect(source.get("listening", {}))
                if not provenance:
                    raise HTTPException(
                        409,
                        "Record has no supported structured report or actual model-pass attribution",
                    )
                features = [
                    dict(
                        namespace="oida.retained_model",
                        name="model_pass_attribution",
                        category="undetermined",
                        value=unknown("No new model result"),
                        claim=dict(
                            statement=f"Retained account contains {len(provenance)} attributed model-pass receipts. "
                            + str(source.get("summary") or "No retained summary."),
                            confidence="undetermined",
                            source="memory",
                            evidence_refs=[identifier],
                            actionability="none",
                        ),
                    )
                ]
            access["subject_ref"] = identifier
            access["declaration_id"] = identity("access")
            # A retained-account report does not claim a fresh effective audio input.
            access["capture"] = unknown(
                "Source declarations are preserved in the retained account"
            )
            access["sampled_representation"] = unknown("No source bytes supplied")
            access["model_input"] = dict(
                status="not_applicable",
                reason="Retained account inspection; no inference",
            )
            output = report(
                identifier,
                access,
                features,
                inputs=inputs,
                recipients=req.recipients,
                report_of=[identifier],
            )
            result = account(output, access)
            result["summary"] = "Structured report of retained account"
            result["listening"]["oida.retained-source"] = dict(
                contract="oida/retained-report-source/v1",
                payload=dict(record=source, record_ref=identifier),
            )
            errors = next_record_errors(result)
            if errors:
                raise ValueError("; ".join(errors))
            preflight("file", None, None, req.remember)
            if canonical(store.get(identifier)) != before:
                raise HTTPException(
                    409, "Source record changed during report construction"
                )
            checkpoint(seal=True)
            saved = None
            if req.remember:
                store.put(result)
                saved = result["akousma_id"]
                journal.record_reference(saved, record=result)
            return dict(
                contract="oida/structured-report-result/v1",
                report=output,
                text=render(output),
                record=result,
                akousma_id=saved,
            )
        finally:
            store.close()

    @router.post("/owner/records/{identifier}/agent-report")
    def produce(identifier: str, req: Request):
        def invoke():
            try:
                return execute(identifier, req)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc)) from exc

        return operations.run(req.operation_id, invoke)

    return router
