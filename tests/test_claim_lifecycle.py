import copy
from unittest.mock import patch
from test_observation_source import (
    setup as setup,
    payload as payload,
    validator_module as validator_module,
)
from akousma import AkousmataStore


def test_declared_currency_write_owner_replay_and_retention_review(
    setup, payload, validator_module, tmp_path
):
    client = setup()
    declaration = {
        "status": "expires",
        "issued_at": "2020-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    retention = {
        "status": "review_after",
        "review_after": "2098-01-01T00:00:00Z",
        "policy_ref": "owner-review-policy",
    }
    response = client.post(
        "/sources/observations",
        json={
            **payload,
            "remember": True,
            "claim_validity": declaration,
            "claim_retention": retention,
        },
    )
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    identifier = record["akousma_id"]
    assert all(
        c["validity"] == "current"
        for c in response.json()["claim_evaluation"]["claims"]
    )
    before = copy.deepcopy(record)
    with patch("oida.claim_lifecycle.utc_now", return_value="2100-01-01T00:00:00Z"):
        replay = client.get("/owner/records/" + identifier).json()
        assert replay["record"] == before
        assert all(
            c["validity"] == "expired" and c["retention"] == "review_due"
            for c in replay["claim_evaluation"]["claims"]
        )
    store = AkousmataStore()
    assert store.get(identifier) == before
    store.close()
    refused = client.post(
        "/sources/observations",
        json={
            **payload,
            "remember": True,
            "claim_validity": {**declaration, "expires_at": "2021-01-01T00:00:00Z"},
        },
    )
    assert refused.status_code == 400
    invalid = client.post(
        "/sources/observations",
        json={**payload, "claim_validity": {"status": "expires"}},
    )
    assert invalid.status_code == 400


def test_expired_claim_leaves_public_projection_without_mutating_record(
    setup, payload, validator_module, tmp_path, monkeypatch
):
    from akousmata_app import publication, exports

    client = setup()
    r = client.post(
        "/sources/observations",
        json={
            **payload,
            "remember": True,
            "claim_validity": {
                "status": "expires",
                "issued_at": "2020-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        },
    )
    assert r.status_code == 200, r.text
    record = r.json()["record"]
    store = AkousmataStore()
    record["provenance"]["consent_status"] = "owned"
    store.put(record)
    publication.grant(store, record["akousma_id"], ["summary"])
    assert publication.public_record(store, record["akousma_id"]) is not None
    monkeypatch.setattr(exports, "claim_clock", lambda: "2100-01-01T00:00:00Z")
    assert publication.public_record(store, record["akousma_id"]) is None
    assert not exports.exportable(record)[0]
    assert store.get(record["akousma_id"]) == record
    store.close()
