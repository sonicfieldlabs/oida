# ruff: noqa: F811
from copy import deepcopy
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from akousma import AkousmataStore
from oida.server import create_app
from oida.record_exchange import REQUIRED, digest
from oida.owner_journal import OwnerJournal
from test_source_capture import setup  # noqa: F401
from test_ensemble_runtime import records


def handoff(setup, tmp_path, monkeypatch):
    sender = setup()
    source = records(tmp_path)[0]
    sender_id = sender.get("/owner/exchange/capabilities").json()["recipient_id"]
    # Another local owner instance advertises its own persistent identity.
    monkeypatch.setenv("OIDA_DATA_DIR", str(tmp_path / "receiver-runtime"))
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "receiver-store"))
    receiver = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    receiver_id = receiver.get("/owner/exchange/capabilities").json()["recipient_id"]
    # Offer is formed while the sending canonical store is active.
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "store"))
    response = sender.post(
        "/owner/exchange/offers",
        json=dict(
            record_id=source["akousma_id"],
            recipient_id=receiver_id,
            supported_contracts=REQUIRED,
            idempotency_key="handoff",
            permission_ref="fixture-owner",
        ),
    )
    assert response.status_code == 200, response.text
    packet = response.json()
    assert packet["sender_id"] == sender_id
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path / "receiver-store"))
    return receiver, source, packet


def test_negotiated_exchange_preserves_original_and_replays_after_restart(
    setup, tmp_path, monkeypatch
):
    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    response = receiver.post("/owner/exchange/receive", json=packet)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert not receipt["new_pass"] and not receipt["replayed"]
    assert receipt["sender_id"] != receipt["recipient_id"]
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert store.get(source["akousma_id"]) == source
        assert len(store.query(limit=10)) == 1
    finally:
        store.close()
    restarted = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    retry = restarted.post("/owner/exchange/receive", json=packet)
    assert retry.status_code == 200 and retry.json()["replayed"]
    assert (
        restarted.get("/operations/" + receipt["operation_id"]).json()["akousma_id"]
        == source["akousma_id"]
    )
    changed = {**packet, "permission_ref": "different"}
    assert restarted.post("/owner/exchange/receive", json=changed).status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(supported_contracts=["unsupported"]),
        lambda p: p.update(required_contracts=[*REQUIRED, "unsupported"]),
        lambda p: p.update(recipient_id="another-owner"),
        lambda p: p.update(source_sha256="0" * 64),
        lambda p: p.update(permission_ref=" "),
    ],
)
def test_exchange_refuses_unsupported_or_misattributed_inputs(
    setup, tmp_path, monkeypatch, change
):
    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    change(packet)
    response = receiver.post("/owner/exchange/receive", json=packet)
    assert response.status_code == 400, response.text
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert store.get(source["akousma_id"]) is None
    finally:
        store.close()


def test_conflicting_canonical_identity_is_never_overwritten(
    setup, tmp_path, monkeypatch
):
    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    existing = deepcopy(source)
    existing["summary"] = "Different canonical content"
    store = AkousmataStore(tmp_path / "receiver-store")
    store.put(existing)
    store.close()
    response = receiver.post("/owner/exchange/receive", json=packet)
    assert response.status_code == 409
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert store.get(source["akousma_id"]) == existing
    finally:
        store.close()


def test_exchange_recovers_record_commit_without_duplicate_pass(
    setup, tmp_path, monkeypatch
):
    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    with patch.object(
        OwnerJournal,
        "record_reference",
        side_effect=RuntimeError("fixture interrupted journal"),
    ):
        with pytest.raises(RuntimeError):
            receiver.post("/owner/exchange/receive", json=packet)
    restarted = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    response = restarted.post("/owner/exchange/receive", json=packet)
    assert response.status_code == 200 and response.json()["replayed"]
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert (
            len(store.query(limit=10)) == 1
            and store.get(source["akousma_id"]) == source
        )
    finally:
        store.close()


def test_cancel_before_exchange_seal_does_not_store_record(
    setup, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from oida.operation_control import checkpoint

    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    entered = Event()
    release = Event()
    op = "exchange_" + digest([packet["sender_id"], packet["idempotency_key"]])

    def delayed(**kwargs):
        entered.set()
        assert release.wait(5)
        return checkpoint(**kwargs)

    with (
        patch("oida.record_exchange.checkpoint", delayed),
        ThreadPoolExecutor() as pool,
    ):
        future = pool.submit(receiver.post, "/owner/exchange/receive", json=packet)
        try:
            assert entered.wait(3)
            assert receiver.post("/operations/" + op + "/cancel").json()[
                "cancel_requested"
            ]
        finally:
            release.set()
        assert future.result().status_code == 409
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert store.get(source["akousma_id"]) is None
    finally:
        store.close()
    assert receiver.post("/owner/exchange/receive", json=packet).status_code == 409


def test_concurrent_retries_cannot_duplicate_the_received_record(
    setup, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    with ThreadPoolExecutor() as pool:
        replies = list(
            pool.map(
                lambda _: receiver.post("/owner/exchange/receive", json=packet),
                range(4),
            )
        )
    assert all(r.status_code in (200, 409) for r in replies)
    assert any(r.status_code == 200 for r in replies)
    assert receiver.post("/owner/exchange/receive", json=packet).json()["replayed"]
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert len(store.query(limit=10)) == 1
    finally:
        store.close()


def test_unresolved_received_context_is_refused_before_storage(
    setup, tmp_path, monkeypatch
):
    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    packet["record"]["extensions"]["earworm_listening_context"]["contexts"][0][
        "listening_ref"
    ] = "missing"
    packet["source_sha256"] = digest(packet["record"])
    response = receiver.post("/owner/exchange/receive", json=packet)
    assert response.status_code == 400, response.text
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert store.get(source["akousma_id"]) is None
    finally:
        store.close()


@pytest.mark.parametrize("restriction", ["restricted", "withheld"])
def test_record_permission_cannot_be_overridden_by_transfer_reference(
    setup, tmp_path, monkeypatch, restriction
):
    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    if restriction == "restricted":
        packet["record"]["provenance"]["consent_status"] = "restricted"
    else:
        packet["record"]["covenant"] = {"rules_applied": ["do_not_reveal:transcript"]}
    packet["source_sha256"] = digest(packet["record"])
    response = receiver.post("/owner/exchange/receive", json=packet)
    assert response.status_code == 400
    assert (
        "nonrestricted" if restriction == "restricted" else "covenant"
    ) in response.text
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert store.get(source["akousma_id"]) is None
    finally:
        store.close()


def test_received_record_supports_explicit_a2_pass_without_exchange_reexecution(
    setup, tmp_path, monkeypatch
):
    receiver, source, packet = handoff(setup, tmp_path, monkeypatch)
    assert receiver.post("/owner/exchange/receive", json=packet).status_code == 200
    req = dict(
        operation_id="explicit-second",
        question="What does this retained account establish?",
        permission_ref="fixture-owner",
        require_model=False,
        provider_id="local_structured",
        remember=True,
    )
    response = receiver.post(
        "/owner/records/" + source["akousma_id"] + "/second-report", json=req
    )
    assert response.status_code == 200, response.text
    assert response.json()["report"]["report_of_refs"] == [source["akousma_id"]]
    assert receiver.post("/owner/exchange/receive", json=packet).json()["replayed"]
    assert (
        receiver.post(
            "/owner/records/" + source["akousma_id"] + "/second-report", json=req
        ).status_code
        == 409
    )
    store = AkousmataStore(tmp_path / "receiver-store")
    try:
        assert len(store.query(limit=10)) == 2
    finally:
        store.close()
