"""Owner-side finite playlist acquisition; FFmpeg never receives a network URL.

HLS is a deliberately small RFC 8216 subset: completed media playlists with
unencrypted MPEG-TS or packed AAC segments. Master/live/fMP4/byte-range/key
playlists are refused, rather than delegated to an unrestricted demuxer.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from oida.public_fetch import PublicFetcher, public_url

MAX_BYTES = 32 * 1024**2
MAX_PLAYLIST = 64 * 1024
FORMATS = {
    "audio/mpeg": "mp3",
    "audio/aac": "aac",
    "audio/aacp": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/ogg": "ogg",
    "application/ogg": "ogg",
    "video/mp2t": "mpegts",
}


@dataclass(frozen=True)
class AudioBytes:
    data: bytes
    format: str
    receipt: dict


def playlist(data: bytes, base: str):
    if len(data) > MAX_PLAYLIST:
        raise ValueError("Playlist exceeds 64 KiB")
    lines = [s.strip() for s in data.decode("utf-8-sig").splitlines() if s.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError("Only M3U playlists are supported")
    hls = any(s.startswith("#EXT-X-") for s in lines)
    targets, pending, duration = [], None, 0.0
    for line in lines[1:]:
        if line.startswith("#EXTINF:"):
            if pending is not None:
                raise ValueError("Playlist segment lacks a URI")
            pending = float(line.split(":", 1)[1].split(",", 1)[0])
            if not math.isfinite(pending) or (hls and not 0 < pending <= 30):
                raise ValueError("Playlist segment duration exceeds bounds")
            if not hls:
                pending = 0.0
        elif line.startswith("#"):
            if hls and not (
                line == "#EXT-X-ENDLIST"
                or line.startswith(
                    (
                        "#EXT-X-VERSION:",
                        "#EXT-X-TARGETDURATION:",
                        "#EXT-X-MEDIA-SEQUENCE:",
                        "#EXT-X-PLAYLIST-TYPE:VOD",
                    )
                )
            ):
                raise ValueError("Unsupported HLS playlists tag")
        else:
            if hls and pending is None:
                raise ValueError("HLS segment lacks a duration")
            target = public_url(urljoin(base, line))
            # A playlist cannot silently introduce another origin or downgrade TLS.
            if urlsplit(target).netloc != urlsplit(base).netloc or (
                urlsplit(base).scheme == "https" and urlsplit(target).scheme != "https"
            ):
                raise ValueError("Playlist segment origin differs from its source")
            targets.append(target)
            duration += pending or 0
            pending = None
    if pending is not None or not targets or len(targets) > 16 or duration > 60:
        raise ValueError("Playlist exceeds segment or duration bounds")
    if hls and "#EXT-X-ENDLIST" not in lines:
        raise ValueError("Live HLS playlists are not qualified")
    if not hls and len(targets) != 1:
        raise ValueError("An ordinary playlist must select exactly one stream")
    return hls, targets


def retrieve_audio(url, *, seconds, cancel, fetcher=None):
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Invalid stream window")
    fetcher = fetcher or PublicFetcher()
    deadline = time.monotonic() + 45

    def get(target, limit, stream=None):
        if cancel.is_set():
            raise InterruptedError("Capture cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("Playlist acquisition timed out")
        result = fetcher.get(
            target, limit=limit, cancel=cancel, stream_seconds=stream, deadline=deadline
        )
        if len(result.data) > limit or not result.data:
            raise ValueError("Stream bytes exceed bounds or are empty")
        return result

    root = get(url, MAX_BYTES, min(seconds + 2, 40))
    if root.data.lstrip().startswith(b"#EXTM3U"):
        hls, targets = playlist(root.data, root.url)
        parts, kind, total = [], None, len(root.data)
        hashes = []
        for target in targets:
            result = get(
                target, MAX_BYTES - total, None if hls else min(seconds + 2, 40)
            )
            name = FORMATS.get(result.content_type.lower())
            if (
                not name
                or (hls and name not in {"aac", "mpegts"})
                or (kind is not None and name != kind)
                or result.data.lstrip().startswith((b"#EXTM3U", b"[playlist]", b"<"))
            ):
                raise ValueError("Unsupported or nested playlists media")
            # Redirects may not escape the playlist's declared origin either.
            if urlsplit(result.url).netloc != urlsplit(root.url).netloc:
                raise ValueError("Segment redirect changed source origin")
            total += len(result.data)
            kind = name
            parts.append(result.data)
            hashes.append(hashlib.sha256(result.data).hexdigest())
        data = b"".join(parts)
        format_name = kind
        route = "finite_hls" if hls else "single_m3u"
    else:
        format_name = FORMATS.get(root.content_type.lower())
        if not format_name or root.data.lstrip().startswith((b"[playlist]", b"<")):
            raise ValueError("Unsupported direct media or playlists")
        data, hashes, route = root.data, [], "direct"
    if cancel.is_set():
        raise InterruptedError("Capture cancelled")
    return AudioBytes(
        data,
        format_name,
        {
            "contract": "oida/stream-acquisition/v1",
            "route": route,
            "source_bytes_sha256": hashlib.sha256(data).hexdigest(),
            "segment_sha256": hashes,
            "bytes": len(data),
            "rights": "owner consent and retention rechecked by capture admission",
            "live_qualification": "not_claimed",
        },
    )


def capabilities():
    return {
        "contract": "oida/stream-formats/v1",
        "routes": ["direct", "single_m3u", "finite_hls"],
        "hls_media": ["mpegts", "aac"],
        "max_bytes": MAX_BYTES,
        "max_segments": 16,
        "max_acquisition_seconds": 45,
        "live_qualification": "not_claimed",
        "refused": [
            "live_hls",
            "master",
            "encryption",
            "fmp4",
            "byte_range",
            "nested",
            "pls",
        ],
    }
