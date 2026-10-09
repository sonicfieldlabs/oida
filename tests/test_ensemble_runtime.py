# ruff: noqa: F811
from unittest.mock import patch
from akousma import AkousmataStore
from oida.agent_reports import report, account
from oida.ensemble_runtime import access_for
from test_observation_source import setup  # noqa: F401


def records(tmp_path):
    store = AkousmataStore(tmp_path / "store")
    items = []
    for n, kind in enumerate(("agent", "human")):
        subject = "fixture:subject"
        access = access_for(subject)
        features = [
            dict(
                namespace="fixture",
                name="position",
                category="interpreted",
                value=dict(status="unknown", reason="Synthetic fixture"),
                claim=dict(
                    statement="Synthetic retained position " + str(n),
                    source="memory",
                    confidence="undetermined",
                    evidence_refs=[subject],
                    actionability="none",
                ),
            )
        ]
        r = report(subject, access, features, inputs=[subject])
        r["listener_id"] = kind + ":fixture"
        value = account(r, access)
        value["auditum"]["listenings"][0]["listener_type"] = kind
        value["summary"] = "Synthetic retained account " + str(n)
        store.put(value)
        items.append(value)
    store.close()
    return items


def test_independent_ensemble_preserves_sources_and_no_influence(setup, tmp_path):
    client = setup()
    sources = records(tmp_path)
    ids = [s["akousma_id"] for s in sources]
    request = dict(
        operation_id="ensemble",
        record_ids=ids,
        permission_refs={i: "fixture-owner" for i in ids},
        remember=True,
    )
    response = client.post("/owner/ensembles", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert all("bytes were read" not in item.get("note", "") for item in body["record"]["auditum"]["honest_absences"])
    assert body["ensemble"]["influence_edges"] == []
    retained = body["record"]["listening"]["akouo.retained-ensemble"]["payload"][
        "source_snapshots"
    ]
    assert retained == {s["akousma_id"]: s for s in sources}
    assert client.get("/operations/ensemble").json()["akousma_id"] == body["akousma_id"]
    assert setup().post("/owner/ensembles", json=request).status_code == 409
    assert (
        client.post(
            "/owner/ensembles",
            json={**request, "operation_id": "missing", "permission_refs": {}},
        ).status_code
        == 400
    )


def test_second_report_runs_local_reasoning_and_preserves_a2_route(setup, tmp_path):
    client = setup()
    source = records(tmp_path)[0]
    identifier = source["akousma_id"]
    req = dict(
        operation_id="second",
        question="What can this retained account establish?",
        permission_ref="fixture-owner",
        require_model=False,
        provider_id="local_structured",
        remember=True,
    )
    response = client.post("/owner/records/" + identifier + "/second-report", json=req)
    assert response.status_code == 200, response.text
    value = response.json()
    planned = value["record"]["listening"]["akouo.second-report"]["payload"]
    assert planned["route"]["request"]["profile"] == "second_report"
    assert planned["source_snapshots"][identifier] == source
    assert value["execution"]["provider_id"] == "local_structured"
    packet = value["record"]["listening"]["oida.second-report-execution"]["payload"][
        "evidence_packet"
    ]
    assert any(
        i["kind"] == "claim"
        and i["source"] == "memory"
        and i["category"] == "undetermined"
        for i in packet["items"]
    )
    store = AkousmataStore(tmp_path / "store")
    try:
        assert store.get(identifier) == source
    finally:
        store.close()
    assert client.get("/operations/second").json()["akousma_id"] == value["akousma_id"]
    assert (
        setup()
        .post("/owner/records/" + identifier + "/second-report", json=req)
        .status_code
        == 409
    )
    refused = client.post(
        "/owner/records/" + identifier + "/second-report",
        json={**req, "operation_id": "needs-model", "require_model": True},
    )
    assert refused.status_code == 400, refused.text


def test_cancelled_second_report_does_not_retain_late_result(setup, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from oida.reasoning.orchestrator import ReasoningOrchestrator

    client = setup()
    source = records(tmp_path)[0]
    entered = threading.Event()
    release = threading.Event()
    original = ReasoningOrchestrator.evaluate_retained

    def delayed(self, **kwargs):
        result = original(self, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    req = dict(
        operation_id="cancel",
        question="Inspect limits",
        permission_ref="fixture-owner",
        require_model=False,
        provider_id="local_structured",
        remember=True,
    )
    with (
        patch.object(ReasoningOrchestrator, "evaluate_retained", delayed),
        ThreadPoolExecutor() as pool,
    ):
        future = pool.submit(
            client.post,
            "/owner/records/" + source["akousma_id"] + "/second-report",
            json=req,
        )
        try:
            assert entered.wait(3)
            assert client.post("/operations/cancel/cancel").json()["cancel_requested"]
        finally:
            release.set()
        assert future.result().status_code == 409
    assert client.get("/operations/cancel").json()["status"] == "cancelled"
    store = AkousmataStore(tmp_path / "store")
    try:
        assert len(store.query(limit=10)) == 2
    finally:
        store.close()


def test_independent_aggregation_rejects_duplicate_pass_and_restricted_source(
    setup, tmp_path
):
    from copy import deepcopy

    client = setup()
    sources = records(tmp_path)
    store = AkousmataStore(tmp_path / "store")
    duplicate = deepcopy(sources[0])
    duplicate["akousma_id"] = "duplicate:fixture"
    store.put(duplicate)
    req = dict(
        operation_id="duplicates",
        record_ids=[sources[0]["akousma_id"], duplicate["akousma_id"]],
        permission_refs={
            sources[0]["akousma_id"]: "owner",
            duplicate["akousma_id"]: "owner",
        },
    )
    response = client.post("/owner/ensembles", json=req)
    assert response.status_code == 400 and "counted twice" in response.text
    restricted = deepcopy(sources[1])
    restricted["provenance"]["consent_status"] = "restricted"
    store.put(restricted)
    store.close()
    response = client.post(
        "/owner/records/" + restricted["akousma_id"] + "/second-report",
        json=dict(
            operation_id="restricted",
            question="Inspect",
            permission_ref="owner",
            require_model=False,
        ),
    )
    assert response.status_code == 400 and "Restricted" in response.text


def test_aggregation_preserves_scoped_disagreement_and_actual_participants(
    setup, tmp_path
):
    from copy import deepcopy

    client = setup()
    sources = records(tmp_path)
    first = sources[0]
    first["akousma_id"] = "disagreement-source:fixture"
    original = first["auditum"]["listenings"][0]
    additional = deepcopy(original)
    additional.update(
        listening_id="extra:listening",
        listener_id="fixture:dsp",
        listener_type="agent",
        listening_pass_ref="extra:pass",
    )
    first["auditum"]["listenings"].append(additional)
    context = deepcopy(first["extensions"]["earworm_listening_context"]["contexts"][0])
    context["listening_ref"] = additional["listening_id"]
    context["renderings"] = []
    context["report"]["human_rendering"] = dict(
        status="none", reason="No added rendering"
    )
    first["extensions"]["earworm_listening_context"]["contexts"].append(context)
    disagreement = dict(
        id="disagreement:fixture",
        subject="fixture:subject",
        listening_ids=[original["listening_id"], additional["listening_id"]],
        positions=[
            dict(
                listening_id=original["listening_id"],
                statement="First position",
                claim_category="interpreted",
            ),
            dict(
                listening_id=additional["listening_id"],
                statement="Second position",
                claim_category="measured",
            ),
        ],
        status="preserved",
    )
    first["auditum"]["disagreements"] = [disagreement]
    store = AkousmataStore(tmp_path / "store")
    store.put(first)
    store.close()
    ids = [s["akousma_id"] for s in sources]
    response = client.post(
        "/owner/ensembles",
        json=dict(
            operation_id="disagreements",
            record_ids=ids,
            permission_refs={i: "owner" for i in ids},
        ),
    )
    assert response.status_code == 200, response.text
    retained = response.json()["record"]["listening"]["akouo.retained-ensemble"][
        "payload"
    ]
    assert retained["disagreements"] == [
        dict(source_record_ref=first["akousma_id"], disagreement=disagreement)
    ]
    assert {p["type"] for p in retained["adapted"]["source"]["participants"]} == {
        "agent",
        "human",
    }
    assert len(retained["adapted"]["source"]["participants"]) == 3
    assert retained["source_snapshots"][first["akousma_id"]] == first


def test_source_mutation_before_commit_refuses_second_report(setup, tmp_path):
    from oida.reasoning.orchestrator import ReasoningOrchestrator

    client = setup()
    source = records(tmp_path)[0]
    original = ReasoningOrchestrator.evaluate_retained

    def mutate(self, **kwargs):
        result = original(self, **kwargs)
        store = AkousmataStore(tmp_path / "store")
        source["summary"] = "Concurrent owner edit"
        store.put(source)
        store.close()
        return result

    with patch.object(ReasoningOrchestrator, "evaluate_retained", mutate):
        response = client.post(
            "/owner/records/" + source["akousma_id"] + "/second-report",
            json=dict(
                operation_id="drift",
                question="Inspect",
                permission_ref="owner",
                require_model=False,
                remember=True,
            ),
        )
    assert response.status_code == 409 and "changed during execution" in response.text
    store = AkousmataStore(tmp_path / "store")
    try:
        assert len(store.query(limit=10)) == 2
    finally:
        store.close()
