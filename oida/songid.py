"""Song identification for oída — opt-in, provider-abstracted, offline-safe.

When oída's listening classifies a source as music, an optional "song id" toggle can try to
name the track. Default is OFF. Uses ShazamIO (an *unofficial* API — no SLA; sends an audio
fingerprint, not the raw recording) behind a provider interface, so open-data providers
(AcoustID/MusicBrainz) can be added later without touching callers. On any failure or when
disabled, it degrades gracefully to "no match" and oída's descriptive path is unaffected.

Results are shaped for an akousma's ``extensions.songid`` block.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol


def songid_enabled(explicit: bool | None = None) -> bool:
    """Toggle state: explicit arg wins, else ``OIDA_SONGID`` env (default OFF)."""
    if explicit is not None:
        return explicit
    return os.getenv("OIDA_SONGID", "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class SongIdResult:
    provider: str
    matched: bool = False
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    isrc: str | None = None
    track_id: str | None = None
    confidence: float | None = None
    note: str | None = None
    checked_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_extension(self) -> dict[str, Any]:
        data = {
            "provider": self.provider,
            "matched": self.matched,
            "checked_at": self.checked_at,
        }
        for k in ("title", "artist", "album", "isrc", "track_id", "confidence", "note"):
            v = getattr(self, k)
            if v is not None:
                data[k] = v
        return data


class SongIdProvider(Protocol):
    name: str

    def identify(self, audio_path: Path) -> SongIdResult:
        ...


class DescriptionFallbackProvider:
    """Never matches — signals callers to keep oída's descriptive path."""

    name = "description"

    def identify(self, audio_path: Path) -> SongIdResult:
        return SongIdResult(provider=self.name, matched=False, note="song id disabled; describing instead")


class ShazamIOProvider:
    """ShazamIO-backed identification. Lazy-imports shazamio; any error → matched=False."""

    name = "shazamio"

    def identify(self, audio_path: Path) -> SongIdResult:
        try:
            import asyncio

            from shazamio import Shazam  # optional dep: pip install 'oida[songid]'

            async def _run() -> dict[str, Any]:
                shazam = Shazam()
                recognize = getattr(shazam, "recognize", None) or getattr(shazam, "recognize_song")
                return await recognize(str(audio_path))

            data = asyncio.run(_run()) or {}
        except ModuleNotFoundError:
            return SongIdResult(provider=self.name, matched=False, note="shazamio not installed")
        except Exception as exc:  # network/format/API — stay graceful and offline-safe
            return SongIdResult(provider=self.name, matched=False, note=f"offline or unrecognized: {exc}".strip())

        track = (data.get("track") or {}) if isinstance(data, dict) else {}
        if not track:
            return SongIdResult(provider=self.name, matched=False, note="no match")
        isrc = track.get("isrc")
        if not isrc:
            for section in track.get("sections", []) or []:
                for meta in section.get("metadata", []) or []:
                    if str(meta.get("title", "")).upper() == "ISRC":
                        isrc = meta.get("text")
        return SongIdResult(
            provider=self.name,
            matched=True,
            title=track.get("title"),
            artist=track.get("subtitle"),
            isrc=isrc,
            track_id=str(track.get("key")) if track.get("key") is not None else None,
        )


def _file_hash(audio_path: Path) -> str:
    return sha256(Path(audio_path).read_bytes()).hexdigest()


def identify_song(
    audio_path: str | Path,
    *,
    enabled: bool | None = None,
    provider: SongIdProvider | None = None,
    cache: dict[str, SongIdResult] | None = None,
) -> dict[str, Any]:
    """Identify the track at ``audio_path`` and return an ``extensions.songid`` dict.

    Off by default. Caches by audio content hash so the same fragment is never re-queried.
    Never raises for identification failures — returns ``matched=False`` instead.
    """
    if not songid_enabled(enabled):
        return SongIdResult(provider="description", matched=False, note="song id off").to_extension()

    provider = provider or ShazamIOProvider()
    path = Path(audio_path).expanduser()
    try:
        key = _file_hash(path)
    except OSError as exc:
        return SongIdResult(provider=provider.name, matched=False, note=f"unreadable audio: {exc}").to_extension()

    if cache is not None and key in cache:
        return cache[key].to_extension()

    result = provider.identify(path)
    if cache is not None:
        cache[key] = result
    return result.to_extension()


def enrich_akousma(
    record: dict[str, Any],
    audio_path: str | Path,
    *,
    enabled: bool | None = None,
    provider: SongIdProvider | None = None,
    cache: dict[str, SongIdResult] | None = None,
) -> dict[str, Any]:
    """Attach a song-id result to ``record['extensions']['songid']`` and return the record."""
    record.setdefault("extensions", {})["songid"] = identify_song(
        audio_path, enabled=enabled, provider=provider, cache=cache
    )
    return record
