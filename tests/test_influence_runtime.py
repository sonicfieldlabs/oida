# ruff: noqa: F811
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import patch
import pytest
from akousma import AkousmataStore
from oida.influence import apply_influence
from test_source_capture import setup  # noqa: F401
from test_ensemble_runtime import records


def inputs(tmp_path, mutation=None):
    source, target = records(tmp_path)
    target["akousma_id"] = "influence-target:fixture"
    listening = target["auditum"]["listenings"][0]
    report = target["listening"]["oida.agent-report"]["payload"]
    report["input_refs"].append(source["akousma_id"])
    report["report_of_refs"] = [source["akousma_id"]]
    before = deepcopy(target["auditum"]["route_decisions"][0])
    after = deepcopy(before)
    instant = datetime.fromisoformat(
        source["auditum"]["listenings"][0]["created_at"].replace("Z", "+00:00")
    )

    def at(seconds):
        return (
            (instant + timedelta(seconds=seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    before.update(decision_id="before", outcome="abstain", decided_at=at(1))
    after.update(decision_id="after", outcome="proceed", decided_at=at(2))
    listening["route_decision_refs"] = ["before", "after"]
    target["auditum"]["route_decisions"] = [before, after]
    trace = dict(
        contract="oida/decision-influence/v1",
        trace_id="trace",
        source_record_ref=source["akousma_id"],
        from_pass_ref=source["auditum"]["listenings"][0]["listening_pass_ref"],
        to_pass_ref=listening["listening_pass_ref"],
        input_report_ref=report["report_id"],
        before_decision_ref="before",
        after_decision_ref="after",
        attributed_by=listening["listener_id"],
        permission_ref="fixture-owner",
    )
    target["listening"]["fixture.influence"] = dict(
        contract=trace["contract"], payload=trace
    )
    if mutation:
        mutation(target)
    store = AkousmataStore(tmp_path / "store")
    store.put(target)
    store.close()
    req = dict(
        operation_id="influenced",
        record_ids=[source["akousma_id"], target["akousma_id"]],
        permission_refs={
            source["akousma_id"]: "fixture-owner",
            target["akousma_id"]: "fixture-owner",
        },
        trace_refs=[
            dict(record_id=target["akousma_id"], namespace="fixture.influence")
        ],
        remember=True,
    )
    return source, target, req


def test_influenced_ensemble_retains_decision_trace_and_restarts(setup, tmp_path):
    client = setup()
    source, target, req = inputs(tmp_path)
    response = client.post("/owner/ensembles/influenced", json=req)
    assert response.status_code == 200, response.text
    result = response.json()
    ensemble = result["record"]["auditum"]["ensemble"]
    assert ensemble["kind"] == "ear_swarm" and len(ensemble["influence_edges"]) == 1
    edge = ensemble["influence_edges"][0]
    assert "abstain to proceed" in edge["effect"]
    receiver = next(
        listening
        for listening in result["record"]["auditum"]["listenings"]
        if listening["listening_id"] == edge["to_listening_id"]
    )
    assert receiver["influenced_by"] == [
        dict(listening_id=edge["from_listening_id"], effect=edge["effect"])
    ]
    retained = result["record"]["listening"]["akouo.retained-ensemble"]["payload"][
        "source_snapshots"
    ]
    assert retained == {s["akousma_id"]: s for s in [source, target]}
    evidence = result["record"]["listening"]["oida.influence-evidence"]["payload"][
        "traces"
    ][0]
    assert evidence["before"] == target["auditum"]["route_decisions"][0]
    assert evidence["after"] == target["auditum"]["route_decisions"][1]
    assert (
        client.get("/operations/influenced").json()["akousma_id"]
        == result["akousma_id"]
    )
    assert setup().post("/owner/ensembles/influenced", json=req).status_code == 409


@pytest.mark.parametrize(
    "mutation",
    [
        lambda t: [
            d.update(decided_at=f"2000-01-01T00:00:0{i}Z")
            for i, d in enumerate(t["auditum"]["route_decisions"])
        ],
        lambda t: t["auditum"]["route_decisions"][1].update(outcome="abstain"),
        lambda t: t["listening"]["fixture.influence"]["payload"].update(
            attributed_by="invented"
        ),
        lambda t: t["listening"]["fixture.influence"]["payload"].update(
            permission_ref="different"
        ),
        lambda t: t["listening"]["fixture.influence"]["payload"].update(
            from_pass_ref="missing"
        ),
        lambda t: t["listening"]["oida.agent-report"]["payload"].update(
            report_of_refs=[]
        ),
        lambda t: t["auditum"]["route_decisions"][1].update(
            decided_at="2026-09-07T09:00:00Z"
        ),
    ],
)
def test_unsupported_influence_refused_without_new_record(setup, tmp_path, mutation):
    client = setup()
    _, _, req = inputs(tmp_path, mutation)
    response = client.post("/owner/ensembles/influenced", json=req)
    assert response.status_code == 400, response.text
    store = AkousmataStore(tmp_path / "store")
    try:
        assert len(store.query(limit=10)) == 3
    finally:
        store.close()


def test_cancelled_influence_cannot_publish_late_record(setup, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    client = setup()
    _, _, req = inputs(tmp_path)
    entered = Event()
    release = Event()

    def slow(*args):
        result = apply_influence(*args)
        entered.set()
        assert release.wait(5)
        return result

    with (
        patch("oida.ensemble_runtime.apply_influence", slow),
        ThreadPoolExecutor() as pool,
    ):
        future = pool.submit(client.post, "/owner/ensembles/influenced", json=req)
        try:
            assert entered.wait(3)
            assert client.post("/operations/influenced/cancel").json()[
                "cancel_requested"
            ]
        finally:
            release.set()
        assert future.result().status_code == 409
    assert client.get("/operations/influenced").json()["status"] == "cancelled"


def test_owner_orchestration_plans_without_perception_fallback(setup, tmp_path):
    from harness.akouo.command import build_command_output

    client = setup()
    req = dict(
        contract="akouo/orchestrate/v0.1",
        request_id="plan",
        ears=[
            dict(
                id="ear", participant_ref="agent", modes=["signal-inspection-listening"]
            )
        ],
        passes=[dict(id="pass", ear_ref="ear", depends_on=[], permission="unknown")],
        direction={"kind": "none"},
        resolved_refs=["agent"],
        dissolution_rule="Finish selected work or stop",
    )
    response = client.post("/owner/orchestrate/plan", json=req)
    assert response.status_code == 200, response.text
    assert response.json()["execution"] == "not_requested"
    assert response.json()["schedule"]["nodes"][0]["permission"] == "unknown"
    assert (
        client.post(
            "/owner/orchestrate/plan",
            json={**req, "direction": {"kind": "person", "ref": "missing"}},
        ).status_code
        == 400
    )
    with pytest.raises(ValueError):
        build_command_output({}, command="/orchestrate")
