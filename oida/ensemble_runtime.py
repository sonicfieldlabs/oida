"""Independent retained ensembles and local A2 second-report execution."""

from copy import deepcopy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from akousma import AkousmataStore
from akousma.record_evolution import next_record_errors
from akousma.listening_contracts import adapt_listening_passes, listening_access_errors
from oida.agent_reports import report, account, unknown, identity, render
from oida.operation_control import checkpoint
from oida.owner_journal import canonical
from oida.influence import TraceReference, apply_influence


class EnsembleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    record_ids: list[str] = Field(min_length=2, max_length=16)
    permission_refs: dict[str, str]
    remember: bool = False


class InfluencedRequest(EnsembleRequest):
    trace_refs: list[TraceReference] = Field(min_length=1, max_length=32)


class SecondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    question: str = Field(min_length=1, max_length=8000)
    permission_ref: str = Field(min_length=1, max_length=256)
    provider_id: str | None = None
    require_model: bool = True
    remember: bool = False


def access_for(subject):
    return dict(
        contract="earworm/listening-access/v1",
        declaration_id=identity("access"),
        subject_ref=subject,
        capture=unknown("Retained records only"),
        sampled_representation=unknown("No audio source opened"),
        model_input=dict(
            status="not_applicable",
            reason="Textual retained-record reasoning; no audio model input",
        ),
        human_access=[unknown("Text rendering is not human audio access")],
    )


def load(store, identifier):
    value = store.get(identifier)
    if value is None:
        raise HTTPException(404, "Unknown retained record")
    if value.get("provenance", {}).get("consent_status") == "restricted":
        raise ValueError(
            "Restricted source requires a separately resolved permission policy"
        )
    errors = next_record_errors(value)
    if errors:
        raise ValueError("Unsupported source record: " + "; ".join(errors))
    if len(canonical(value).encode()) > 2 * 1024 * 1024:
        raise ValueError("Source record exceeds 2 MiB")
    return value


def runtime_router(operations, journal, preflight, reasoning):
    router = APIRouter()

    def commit(store, req, result, sources):
        preflight("file", None, None, req.remember)
        for identifier, source in sources.items():
            if canonical(store.get(identifier)) != canonical(source):
                raise HTTPException(409, "Retained input changed during execution")
        errors = next_record_errors(result)
        if errors:
            raise ValueError("; ".join(errors))
        checkpoint(seal=True)
        saved = None
        if req.remember:
            store.put(result)
            saved = result["akousma_id"]
            journal.record_reference(saved, record=result)
        return dict(record=result, akousma_id=saved)

    def aggregate(req):
        from akouo_contract.orchestration import adapt_records, plan, execute

        if set(req.permission_refs) != set(req.record_ids) or any(
            not ref.strip() for ref in req.permission_refs.values()
        ):
            raise ValueError(
                "Each retained source requires an explicit permission reference"
            )
        preflight("file", None, None, req.remember)
        store = AkousmataStore()
        try:
            sources = [load(store, i) for i in req.record_ids]
            schedule = plan(
                [
                    dict(id=i, depends_on=[], permission="granted")
                    for i in req.record_ids
                ],
                dissolution_rule="Stop after the selected retained inputs",
            )
            execution = execute(
                schedule,
                lambda i, deps: next(r for r in sources if r["akousma_id"] == i),
            )
            checkpoint()
            if any(r["status"] != "complete" for r in execution["receipts"]):
                raise ValueError("Retained-input orchestration did not complete")
            adapted = adapt_records(
                sources,
                validate_record=next_record_errors,
                adapt_passes=adapt_listening_passes,
                ensemble_id=identity("ensemble"),
            )
            influence_evidence = []
            if isinstance(req, InfluencedRequest):
                adapted, influence_evidence = apply_influence(
                    adapted,
                    {r["akousma_id"]: r for r in sources},
                    req.trace_refs,
                    req.permission_refs,
                )
            subject = identity("retained-ensemble")
            access = access_for(subject)
            output = report(
                subject, access, [], inputs=req.record_ids, report_of=req.record_ids
            )
            output["limitations"].append(
                "Independent retained passes; no new model pass, consensus or influence inferred."
            )
            if influence_evidence:
                output["limitations"][-1] = (
                    "Retained decision-change attribution; no new pass or independent causal measurement."
                )
            result = account(output, access)
            result["summary"] = (
                "Retained influenced ensemble"
                if influence_evidence
                else "Independent retained listening ensemble"
            )
            if influence_evidence:
                result["listening"]["oida.influence-evidence"] = dict(
                    contract="oida/influence-evidence/v1",
                    payload=dict(traces=influence_evidence),
                )
            result["lineage"]["parent_akousma_ids"] = list(req.record_ids)
            result["auditum"]["listenings"].extend(adapted["adapted"]["listenings"])
            result["auditum"]["ensemble"] = adapted["adapted"]["ensemble"]
            contexts = result["extensions"]["earworm_listening_context"]
            contexts["access_declarations"] = []
            for source in sources:
                retained_access = access_for(source["akousma_id"])
                contexts["access_declarations"].append(retained_access)
                for original in source["auditum"]["listenings"]:
                    pid = original.get("listening_pass_ref") or original["listening_id"]
                    contexts["contexts"].append(
                        dict(
                            listening_ref=adapted["adapted"]["pass_to_listening"][pid],
                            subject_ref=source["akousma_id"],
                            recipients=deepcopy(output["recipients"]),
                            access_declaration_ref=retained_access["declaration_id"],
                            report=dict(
                                ref=source["akousma_id"],
                                contract="akouo/retained-listening/v0.1",
                                format="structured",
                                readability=dict(
                                    status="known", value="machine_readable"
                                ),
                                human_rendering=dict(
                                    status="none",
                                    reason="Original rendering declarations remain in the source snapshot.",
                                ),
                            ),
                            renderings=[],
                        )
                    )
            for source in sources:
                namespace = "retained:" + source["akousma_id"]
                result["listening"][namespace] = dict(
                    contract="akouo/retained-listening/v0.1",
                    payload=dict(record=source),
                )
            # Preserve disagreement identities/positions in the retained adapter;
            # the original scopes are not relabelled as newly resolved agreement.
            result["listening"]["akouo.retained-ensemble"] = dict(
                contract=adapted["contract"], payload=adapted
            )
            result["listening"]["oida.ensemble-execution"] = dict(
                contract="oida/ensemble-execution/v1",
                payload=dict(
                    plan=schedule,
                    receipts=execution["receipts"],
                    permission_refs=req.permission_refs,
                    terminated=execution["terminated"],
                ),
            )
            return dict(
                contract="oida/ensemble-result/v1",
                ensemble=adapted["adapted"]["ensemble"],
                **commit(store, req, result, {r["akousma_id"]: r for r in sources}),
            )
        finally:
            store.close()

    def second(identifier, req):
        from akouo_contract.companions import plan_second_report, COMPANION_CONTRACT
        from akouo_contract.agent_routes import agent_route_report_errors

        preflight("file", None, None, req.remember)
        store = AkousmataStore()
        try:
            if not req.permission_ref.strip():
                raise ValueError("An explicit permission reference is required")
            source = load(store, identifier)
            access = access_for(identifier)
            output = report(
                identifier, access, [], inputs=[identifier], report_of=[identifier]
            )
            request = dict(
                contract="akouo/agent-route/v0.1",
                request_id=identity("route"),
                profile="second_report",
                supported_contracts=[
                    COMPANION_CONTRACT,
                    "akouo/agent-route/v0.1",
                    "akouo/agent-report/v0.1",
                    "earworm/listening-access/v1",
                ],
                relation=dict(of="record", ref=identifier),
                resolved_refs=[
                    identifier,
                    access["declaration_id"],
                    output["listener_id"],
                    *[r["id"] for r in output["recipients"]],
                ],
                requested_categories=["interpreted", "undetermined"],
                **{
                    k: output[k]
                    for k in (
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
            planned = plan_second_report(
                request,
                access,
                records={identifier: source},
                validate_record=next_record_errors,
                validate_access=listening_access_errors,
            )
            if planned["route"]["decision"]["outcome"] != "proceed":
                raise ValueError("Second-report route abstained")
            # A bounded retained-account projection feeds existing text reasoning;
            # do not send an audio path or claim original sensor/model access.
            claims = []
            for entry in source.get("listening", {}).values():
                payload = entry.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                selected = (
                    payload.get("report")
                    if isinstance(payload.get("report"), dict)
                    else payload
                )
                if selected.get("contract") == "akouo/agent-report/v0.1":
                    from akouo_contract.agent_report import agent_report_errors

                    if agent_report_errors(selected):
                        raise ValueError("Retained agent-report payload is invalid")
                    claims.extend(
                        dict(
                            statement="Retained "
                            + f["category"]
                            + " claim: "
                            + f["claim"]["statement"],
                            source="memory",
                            confidence="undetermined",
                        )
                        for f in selected["features"]
                    )
            event = dict(
                id=identifier,
                raw_audio_policy="temp",
                privacy_mode="session",
                covenant=deepcopy(source.get("covenant", {})),
                aggregate=dict(
                    short_summary=source.get("summary") or "Retained listening account"
                ),
                routes=[dict(structured=dict(claim_summary={"undetermined": claims}))],
            )
            evaluated = reasoning.evaluate_retained(
                event=event,
                question=req.question,
                provider_id=req.provider_id,
                require_model=req.require_model,
            )
            checkpoint()
            for block in evaluated["response"]["answer_blocks"]:
                output["features"].append(
                    dict(
                        feature_id=identity("feature"),
                        namespace="oida.second_report",
                        name="retained_account_interpretation",
                        category="interpreted",
                        value=unknown("Textual second-report interpretation"),
                        claim=dict(
                            claim_id=identity("claim"),
                            statement=block["text"],
                            confidence="undetermined",
                            source="memory",
                            evidence_refs=[identifier],
                            listening_pass_id=output["listening_pass_id"],
                            actionability="none",
                        ),
                    )
                )
            output["features"].append(
                dict(
                    feature_id=identity("feature"),
                    namespace="oida.second_report",
                    name="access_limit",
                    category="undetermined",
                    value=unknown("No original audio examined"),
                    claim=dict(
                        claim_id=identity("claim"),
                        statement="This pass interprets retained evidence; it does not verify original claims or establish hearing.",
                        confidence="undetermined",
                        source="memory",
                        evidence_refs=[identifier],
                        listening_pass_id=output["listening_pass_id"],
                        actionability="none",
                    ),
                )
            )
            errors = agent_route_report_errors(output, planned["route"])
            if errors:
                raise ValueError("; ".join(errors))
            result = account(output, access)
            result["summary"] = "Second report on retained evidence"
            result["lineage"]["parent_akousma_ids"] = [identifier]
            if "covenant" in source:
                result["covenant"] = deepcopy(source["covenant"])
            result["listening"]["akouo.second-report"] = dict(
                contract=planned["contract"], payload=planned
            )
            result["listening"]["oida.second-report-execution"] = dict(
                contract="oida/second-report-execution/v1",
                payload=dict(
                    **evaluated,
                    report_pass_ref=output["listening_pass_id"],
                    permission_ref=req.permission_ref,
                ),
            )
            return dict(
                contract="oida/second-report-result/v1",
                report=output,
                text=render(output),
                execution=evaluated["execution"],
                **commit(store, req, result, {identifier: source}),
            )
        finally:
            store.close()

    def invoke(operation, callback):
        def run():
            try:
                return callback()
            except (ValueError, TypeError, KeyError) as exc:
                raise HTTPException(400, str(exc)) from exc
            except ImportError as exc:
                raise HTTPException(
                    503, "Compatible orchestration contracts unavailable"
                ) from exc

        return operations.run(operation, run)

    @router.post("/owner/orchestrate/plan")
    def orchestration_plan(request: dict):
        from harness.akouo.routing import plan_command

        try:
            return plan_command(request)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/owner/ensembles")
    def ensemble(req: EnsembleRequest):
        return invoke(req.operation_id, lambda: aggregate(req))

    @router.post("/owner/ensembles/influenced")
    def influenced(req: InfluencedRequest):
        return invoke(req.operation_id, lambda: aggregate(req))

    @router.post("/owner/records/{identifier}/second-report")
    def second_report(identifier: str, req: SecondRequest):
        return invoke(req.operation_id, lambda: second(identifier, req))

    return router
