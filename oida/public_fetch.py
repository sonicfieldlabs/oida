"""Bounded public retrieval with DNS pinning; discovery never reaches local services."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

USER_AGENT = "ListeningStackDiscovery/0.1 (local owner-requested audio exploration)"


class SourceHTTPError(ValueError):
    def __init__(self, status, url):
        self.status = status
        self.host = urlsplit(url).hostname
        super().__init__(f"Source returned HTTP {status}")


def public_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
            or len(value) > 4096
            or any(ord(c) < 33 for c in value)
            or "\\" in value
        ):
            raise ValueError()
        if host.lower() == "localhost" or host.lower().endswith(
            (".localhost", ".local", ".internal", ".ts.net")
        ):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
        )
    except (ValueError, AttributeError) as exc:
        raise ValueError("Use a public HTTP(S) link without credentials") from exc


@dataclass
class Retrieved:
    url: str
    content_type: str
    data: bytes


class PublicFetcher:
    def get(self, url, *, limit=2 * 1024 * 1024, cancel=None, stream_seconds=None):
        started = time.monotonic()
        for _ in range(4):
            if cancel and cancel.is_set():
                raise InterruptedError("Discovery stopped")
            url = public_url(url)
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
            if not addresses or any(
                not ipaddress.ip_address(ip).is_global for ip in addresses
            ):
                raise ValueError("This link resolves outside the public internet")
            # Keep Host/SNI and certificate validation tied to the original hostname,
            # while connecting only to an address checked above (no second DNS lookup).
            connection = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )(host, port, timeout=12)
            connection._create_connection = (
                lambda address, timeout=12, source_address=None, _target=(addresses[0], port): (
                    socket.create_connection(_target, timeout, source_address)
                )
            )
            try:
                connection.request(
                    "GET",
                    urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept-Encoding": "identity",
                        "Accept": "*/*",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError(
                            "Source returned a redirect without a location"
                        )
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise SourceHTTPError(response.status, url)
                size = response.getheader("Content-Length")
                if size and int(size) > limit and not stream_seconds:
                    raise ValueError(
                        f"Source exceeds the {limit // (1024 * 1024)} MiB retrieval limit"
                    )
                chunks, count = [], 0
                while True:
                    if cancel and cancel.is_set():
                        raise InterruptedError("Discovery stopped")
                    if time.monotonic() - started > 45:
                        raise TimeoutError("Source retrieval timed out")
                    if (
                        stream_seconds
                        and chunks
                        and time.monotonic() - started >= stream_seconds
                    ):
                        break
                    chunk = response.read(
                        min(8192 if stream_seconds else 65536, limit - count + 1)
                    )
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit and stream_seconds:
                        chunks.append(chunk[: len(chunk) - (count - limit)])
                        break
                    if count > limit:
                        raise ValueError("Source exceeds the retrieval limit")
                    chunks.append(chunk)
                return Retrieved(
                    url,
                    response.getheader(
                        "Content-Type", "application/octet-stream"
                    ).split(";")[0],
                    b"".join(chunks),
                )
            finally:
                connection.close()
        raise ValueError("Too many redirects")

    def json(self, url, *, cancel=None):
        return json.loads(self.get(url, cancel=cancel).data)
