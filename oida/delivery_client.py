"""Explicit local relay admission to the existing gateway and cancellation APIs.

The relay supplies its own bounded spool file. This adapter never enables hosted
access to the local server, requests permanent memory, or returns raw reports.
"""

import json
import ipaddress
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DeliveryClient:
    def __init__(self, origin):
        p = urlsplit(origin)
        try:
            local = (
                p.hostname == "localhost"
                or ipaddress.ip_address(p.hostname or "").is_loopback
            )
            port = p.port
        except ValueError:
            local, port = False, None
        if (
            p.scheme != "http"
            or not local
            or not port
            or p.path not in ("", "/")
            or p.query
            or p.fragment
            or p.username
            or p.password
        ):
            raise ValueError("Delivery requires an explicit loopback Oida origin")
        self.origin = origin.rstrip("/")

    def call(self, path, body, timeout=120):
        request = Request(
            self.origin + path,
            data=json.dumps(body, allow_nan=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=timeout
        ) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Oversized Oida result")
        return json.loads(raw)

    def listen(self, path, operation_id):
        result = self.call(
            "/gateway/listen",
            dict(
                path=str(path),
                operation_id=operation_id,
                remember=False,
                privacy_mode="incognito",
                ephemeral_delivery=True,
                raw_audio_policy="not_stored",
                route_preset="basic",
                source_type="file",
                source_label="Visitor-authorized session; no publication or training",
            ),
        )
        if result.get("contract") != "oida/gateway/v0.6" or result.get("akousma_id"):
            raise ValueError("Unsupported response or unexpected permanent retention")
        # Receipt linkage only; reports/transcripts/source paths never leave the owner.
        return dict(
            owner_operation_id=operation_id,
            owner_event_id=result.get("listening_event", {}).get("id"),
            outcome=result.get("status", "unknown"),
            retained=False,
            publication="not_requested",
            result_access="Session processing acknowledged; raw reports remain with the owner",
        )

    def cancel(self, operation_id):
        return self.call("/operations/" + operation_id + "/cancel", {}, timeout=5)
