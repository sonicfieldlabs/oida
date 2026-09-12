from unittest.mock import patch
import pytest
from oida.delivery_client import DeliveryClient
from test_source_capture import setup  # noqa: F401
from test_runtime_attribution import fixture_request


def test_delivery_adapter_scopes_retention_and_projection():
    client = DeliveryClient("http://127.0.0.1:8766")
    with patch.object(
        client,
        "call",
        return_value={
            "contract": "oida/gateway/v0.6",
            "status": "complete",
            "listening_event": {"id": "fixture-event"},
            "perception_report": {"secret": "PRIVATE"},
        },
    ) as call:
        result = client.listen("/fixture.wav", "fixture-operation")
    request = call.call_args.args[1]
    assert (
        request["ephemeral_delivery"]
        and request["privacy_mode"] == "incognito"
        and request["remember"] is False
    )
    assert "PRIVATE" not in str(result) and result["owner_event_id"] == "fixture-event"
    with pytest.raises(ValueError):
        DeliveryClient("https://remote.example")


def test_real_gateway_ephemeral_delivery_does_not_retain_history(setup, tmp_path):  # noqa: F811
    client = setup()
    configured = client.post(
        "/background/config",
        json={"updates": {"recent_history": {"include_incognito": True}}},
    )
    assert configured.status_code == 200, configured.text
    req = fixture_request(tmp_path)
    body = dict(
        path=req["path"],
        operation_id="delivery-fixture",
        privacy_mode="incognito",
        ephemeral_delivery=True,
        raw_audio_policy="not_stored",
        remember=False,
    )
    response = client.post("/gateway/listen", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["akousma_id"] is None
    event = response.json()["listening_event"]["id"]
    assert event not in str(client.get("/background/status").json())
    assert client.get("/operations/delivery-fixture").json()["event_id"] == event
    refused = client.post(
        "/gateway/listen",
        json={**body, "operation_id": "bad-delivery", "remember": True},
    )
    assert refused.status_code == 400, refused.text
