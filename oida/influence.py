"""Resolve retained decision-change attribution before writing ensemble edges."""

from copy import deepcopy
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from akouo_contract.agent_report import agent_report_errors
from akousma.listening_contracts import adapt_listening_passes


class TraceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_id: str = Field(min_length=1, max_length=256)
    namespace: str = Field(min_length=1, max_length=256)


class DecisionInfluence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract: Literal["oida/decision-influence/v1"]
    trace_id: str = Field(min_length=1, max_length=256)
    source_record_ref: str
    from_pass_ref: str
    to_pass_ref: str
    input_report_ref: str
    before_decision_ref: str
    after_decision_ref: str
    attributed_by: str
    permission_ref: str


def apply_influence(adapted, records, references, permission_refs):
    """Preserve attributable recorded changes; this is not a causal experiment."""
    result = deepcopy(adapted)
    raw = result["adapted"]["source"]
    passes = {p["id"]: p for p in raw["passes"]}
    edges = []
    evidence = []
    seen = set()
    trace_ids = set()
    for ref in references:
        target = records[ref.record_id]
        envelope = target["listening"][ref.namespace]
        if envelope.get("contract") != "oida/decision-influence/v1":
            raise ValueError("Unsupported retained influence trace")
        trace = DecisionInfluence.model_validate(envelope["payload"]).model_dump()
        if trace["trace_id"] in trace_ids:
            raise ValueError("Duplicate influence trace identity")
        trace_ids.add(trace["trace_id"])
        source = records[trace["source_record_ref"]]
        from_pass, to_pass = trace["from_pass_ref"], trace["to_pass_ref"]
        if source is target or from_pass == to_pass or (from_pass, to_pass) in seen:
            raise ValueError("Influence requires distinct inputs and a unique edge")
        seen.add((from_pass, to_pass))

        def listening(record, pid):
            return next(
                (
                    p
                    for p in record["auditum"]["listenings"]
                    if (p.get("listening_pass_ref") or p["listening_id"]) == pid
                ),
                None,
            )

        origin, receiver = listening(source, from_pass), listening(target, to_pass)
        if (
            origin is None
            or receiver is None
            or from_pass not in passes
            or to_pass not in passes
        ):
            raise ValueError("Influence pass does not belong to its retained source")
        if (
            trace["attributed_by"] != receiver["listener_id"]
            or trace["permission_ref"] != permission_refs[ref.record_id]
        ):
            raise ValueError("Influence authority or permission mismatch")
        entry = target["listening"][receiver["report_namespace"]]["payload"]
        if not isinstance(entry, dict):
            raise ValueError("Influence input report must be structured")
        report = entry.get("report", entry)
        if (
            agent_report_errors(report)
            or report["listening_pass_id"] != to_pass
            or report["listener_id"] != receiver["listener_id"]
        ):
            raise ValueError("Influence requires an attributable A7 input report")
        if (
            report["report_id"] != trace["input_report_ref"]
            or source["akousma_id"] not in report["input_refs"]
            or source["akousma_id"] not in report["report_of_refs"]
        ):
            raise ValueError(
                "Retained target report did not declare the influencing input"
            )
        decisions = {d["decision_id"]: d for d in target["auditum"]["route_decisions"]}
        before, after = (
            decisions[trace["before_decision_ref"]],
            decisions[trace["after_decision_ref"]],
        )
        for decision in (before, after):
            if (
                decision.get("listening_id") != receiver["listening_id"]
                or decision["authority"]["actor"] != receiver["listener_id"]
                or decision["decision_id"]
                not in receiver.get("route_decision_refs", [])
            ):
                raise ValueError(
                    "Decision attribution does not resolve to the target pass"
                )
        if (
            before["gate"] != after["gate"]
            or before["subject"] != after["subject"]
            or before["outcome"] == after["outcome"]
        ):
            raise ValueError(
                "Influence requires a changed decision on the same gate and subject"
            )
        if datetime.fromisoformat(
            before["decided_at"].replace("Z", "+00:00")
        ) >= datetime.fromisoformat(after["decided_at"].replace("Z", "+00:00")):
            raise ValueError("Influence decisions require ordered timestamps")
        if datetime.fromisoformat(
            origin["created_at"].replace("Z", "+00:00")
        ) > datetime.fromisoformat(after["decided_at"].replace("Z", "+00:00")):
            raise ValueError("Influencing pass postdates the attributed decision")
        effect = f"Recorded {after['gate']} decision changed from {before['outcome']} to {after['outcome']}"
        edge = dict(from_pass_id=from_pass, to_pass_id=to_pass, effect=effect)
        edges.append(edge)
        passes[to_pass]["influenced_by"].append(dict(pass_id=from_pass, effect=effect))
        evidence.append(
            dict(
                source_record_ref=source["akousma_id"],
                target_record_ref=target["akousma_id"],
                trace_namespace=ref.namespace,
                trace=trace,
                before=deepcopy(before),
                after=deepcopy(after),
                input_report_ref=report["report_id"],
                effect=effect,
                basis="Retained participant attribution and decision change; not independent causal measurement",
            )
        )
    if not edges:
        raise ValueError("At least one retained influence trace is required")
    raw["ensemble"].update(kind="ear_swarm", influence_edges=edges)
    bindings = [
        dict(
            pass_id=p["id"],
            listening_id=result["adapted"]["pass_to_listening"][p["id"]],
            report_namespace=listening["report_namespace"],
            contract=listening["contract"],
        )
        for p, listening in zip(
            raw["passes"], result["adapted"]["listenings"], strict=True
        )
    ]
    result["adapted"] = adapt_listening_passes(
        passes=raw["passes"],
        participants=raw["participants"],
        bindings=bindings,
        ensemble=raw["ensemble"],
    )
    return result, evidence
