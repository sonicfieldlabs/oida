"""Bounded public retrieval with DNS pinning; discovery never reaches local services."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

USER_AGENT = "ListeningStackDiscovery/0.1 (local owner-requested audio exploration)"
DNS_SLOTS = threading.BoundedSemaphore(4)


def resolve_public(host, port, *, deadline, cancel):
    """Bound DNS waiting without releasing a slot for a still-running resolver."""
    if not DNS_SLOTS.acquire(blocking=False):
        raise ValueError("Public DNS resolver capacity is busy")
    result, done = [], threading.Event()

    def resolve():
        try:
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.append(exc)
        finally:
            DNS_SLOTS.release()
            done.set()

    try:
        threading.Thread(target=resolve, name="oida-public-dns", daemon=True).start()
    except Exception:
        DNS_SLOTS.release()
        raise
    end = min(deadline, time.monotonic() + 5)
    while not done.wait(0.05):
        if cancel and cancel.is_set():
            raise InterruptedError("Discovery stopped")
        if time.monotonic() >= end:
            raise TimeoutError("Public DNS resolution timed out")
    if isinstance(result[0], Exception):
        raise result[0]
    if len(result[0]) > 64:
        raise ValueError("Public DNS answer budget exceeded")
    return result[0]


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
    def get(
        self,
        url,
        *,
        limit=2 * 1024 * 1024,
        cancel=None,
        stream_seconds=None,
        deadline=None,
    ):
        started = time.monotonic()
        deadline = min(deadline or started + 45, started + 45)
        for _ in range(4):
            if cancel and cancel.is_set():
                raise InterruptedError("Discovery stopped")
            if time.monotonic() >= deadline:
                raise TimeoutError("Source retrieval timed out")
            url = public_url(url)
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            answers = resolve_public(host, port, deadline=deadline, cancel=cancel)
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
            )(host, port, timeout=min(12, max(0.001, deadline - time.monotonic())))
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
                    target = urljoin(url, location)
                    if parsed.scheme == "https" and urlsplit(target).scheme != "https":
                        raise ValueError("Source redirect may not downgrade HTTPS")
                    url = target
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
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Source retrieval timed out")
                    if (
                        stream_seconds
                        and chunks
                        and time.monotonic() - started >= stream_seconds
                    ):
                        break
                    sock = getattr(connection, "sock", None) or getattr(
                        getattr(getattr(response, "fp", None), "raw", None),
                        "_sock",
                        None,
                    )
                    if sock is not None:
                        sock.settimeout(
                            min(12, max(0.001, deadline - time.monotonic()))
                        )
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
