"""One owner-requested loopback poll feeding the existing O6 operation path."""

import os
from urllib.parse import urlsplit
import ipaddress
import httpx
from oida.observation_source import ObservationRequest


def request_from_feed(body):
    if (
        not isinstance(body, dict)
        or set(body) not in (
            {"mode", "observation_ref", "consent_ref", "remember", "operation_id"},
            {"mode", "signal_id", "consent_ref", "remember", "operation_id"},
        )
        or body["mode"] not in {"fixture", "live"}
    ):
        raise ValueError(
            "Choose mode, signal_id (or observation_ref), consent_ref, remember and operation_id"
        )
    base = os.environ.get("OIDA_COSMOAUDITION_URL", "")
    parts = urlsplit(base)
    try:
        local = (
            parts.hostname == "localhost"
            or ipaddress.ip_address(parts.hostname or "").is_loopback
        )
        port = parts.port
    except ValueError:
        local = False
        port = None
    if (
        parts.scheme != "http"
        or not local
        or port is None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ValueError("Configure an explicit loopback OIDA_COSMOAUDITION_URL")
    with httpx.Client(follow_redirects=False, timeout=15, trust_env=False) as client:
        with client.stream(
            "GET",
            base.rstrip("/") + "/api/observation-feed",
            params={"mode": body["mode"]},
        ) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("Observation feed exceeds 2 MiB")
    import json

    feed = json.loads(data)
    if (
        feed.get("contract") != "cosmo/observation-feed/v1"
        or feed.get("relation") != {"of": "signal"}
        or feed.get("source_register") != "non-acoustic"
        or feed.get("producer", {}).get("acquisitionMode") != body["mode"]
    ):
        raise ValueError("Unsupported observation feed")
    observation_ref = body.get("observation_ref")
    if "signal_id" in body:
        signal_id = body["signal_id"]
        if not isinstance(signal_id, str) or not 1 <= len(signal_id) <= 256:
            raise ValueError("Choose a bounded named signal")
        matches = [o for o in feed["source_record"].get("observations", []) if o.get("field") == signal_id]
        if len(matches) != 1:
            raise ValueError("Named signal must resolve to one observation in this poll")
        observation_ref = matches[0]["id"]
    return ObservationRequest(
        source_record=feed["source_record"],
        observation_ref=observation_ref,
        producer_id=feed["producer"]["producerId"],
        consent="granted",
        consent_ref=body["consent_ref"],
        remember=body["remember"],
        operation_id=body["operation_id"],
    )
