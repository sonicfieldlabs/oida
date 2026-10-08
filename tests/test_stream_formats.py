import threading
from dataclasses import replace

import pytest

from oida.public_fetch import Retrieved
from oida.stream_formats import playlist, retrieve_audio

BASE = "https://audio.example/test.m3u8"
MEDIA = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\na.ts\n#EXTINF:1,\nb.ts\n#EXT-X-ENDLIST\n"


class Fetch:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.values)


def test_finite_hls_binds_segments_and_shared_budget():
    fetch = Fetch(
        [
            Retrieved(BASE, "application/vnd.apple.mpegurl", MEDIA),
            Retrieved("https://audio.example/a.ts", "video/mp2t", b"a"),
            Retrieved("https://audio.example/b.ts", "video/mp2t", b"b"),
        ]
    )
    result = retrieve_audio(BASE, seconds=2, cancel=threading.Event(), fetcher=fetch)
    assert result.data == b"ab" and result.format == "mpegts"
    assert result.receipt["route"] == "finite_hls"
    assert len(result.receipt["segment_sha256"]) == 2
    assert len({k["deadline"] for _, k in fetch.calls}) == 1
    assert fetch.calls[-1][1]["limit"] < fetch.calls[1][1]["limit"]


@pytest.mark.parametrize(
    "data",
    [
        MEDIA.replace(b"#EXT-X-ENDLIST\n", b""),
        MEDIA.replace(b"a.ts", b"http://127.0.0.1/private"),
        MEDIA.replace(b"a.ts", b"https://other.example/a.ts"),
        MEDIA.replace(b"#EXTINF:1,", b"#EXT-X-KEY:METHOD=AES-128\n#EXTINF:1,"),
        MEDIA.replace(b"#EXTINF:1,", b"#EXT-X-MAP:URI=init.mp4\n#EXTINF:1,"),
        MEDIA.replace(b"#EXTINF:1,", b"#EXTINF:nan,"),
        MEDIA.replace(b"#EXTINF:1,", b"#EXTINF:40,"),
        b"#EXTM3U\na\nb\n",
        b"#EXTM3U\n" + b"x" * 65536,
    ],
)
def test_adversarial_playlists_refused(data):
    with pytest.raises(ValueError):
        playlist(data, BASE)


def test_cancel_and_redirect_never_admit_audio():
    cancel = threading.Event()
    cancel.set()
    fetch = Fetch([])
    with pytest.raises(InterruptedError):
        retrieve_audio(BASE, seconds=1, cancel=cancel, fetcher=fetch)
    assert not fetch.calls
    redirect = Retrieved("https://other.example/a.ts", "video/mp2t", b"a")
    fetch = Fetch([Retrieved(BASE, "application/vnd.apple.mpegurl", MEDIA), redirect])
    with pytest.raises(ValueError, match="origin"):
        retrieve_audio(BASE, seconds=1, cancel=threading.Event(), fetcher=fetch)


def test_single_playlist_and_nested_refusal():
    root = Retrieved(BASE, "audio/x-mpegurl", b"#EXTM3U\na.aac\n")
    audio = Retrieved("https://audio.example/a.aac", "audio/aac", b"audio")
    assert (
        retrieve_audio(
            BASE, seconds=1, cancel=threading.Event(), fetcher=Fetch([root, audio])
        ).receipt["route"]
        == "single_m3u"
    )
    with pytest.raises(ValueError):
        retrieve_audio(
            BASE,
            seconds=1,
            cancel=threading.Event(),
            fetcher=Fetch([root, replace(audio, data=b"#EXTM3U\nnested")]),
        )


def test_generated_hls_uses_local_demux_and_preserves_native_rate(
    tmp_path, monkeypatch
):
    import shutil
    import subprocess

    import soundfile as sf

    from oida.source_capture import CaptureSource, capture_audio

    executable = shutil.which("ffmpeg")
    if not executable:
        pytest.skip("FFmpeg unavailable")
    segment = tmp_path / "segment.ts"
    subprocess.run(
        [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            "0.1",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            str(segment),
        ],
        check=True,
        timeout=10,
    )
    fetch = Fetch(
        [
            Retrieved(BASE, "application/vnd.apple.mpegurl", MEDIA),
            Retrieved("https://audio.example/a.ts", "video/mp2t", segment.read_bytes()),
            Retrieved("https://audio.example/b.ts", "video/mp2t", segment.read_bytes()),
        ]
    )
    monkeypatch.setattr("oida.public_fetch.PublicFetcher.get", fetch.get)
    source = CaptureSource(
        id="fixture",
        adapter="radio",
        input=BASE,
        sample_rate=192000,
        channels=2,
        max_seconds=1.0,
        producer_id="fixture:owner",
        consent="granted",
        consent_ref="fixture:rights",
        network_policy="public_radio",
        retention="temp_only",
        playlist_policy="finite",
    )
    output = tmp_path / "result.wav"
    capture_audio(source, 0.1, output, threading.Event())
    info = sf.info(output)
    assert (info.samplerate, info.channels) == (48000, 1)
    assert 0 < info.duration <= 0.1 + 1 / 48000
    assert not list(tmp_path.glob("oida-radio-*"))


def test_dns_cancel_retains_capacity_until_late_resolver_finishes(monkeypatch):
    import time

    from oida import public_fetch

    release, entered = threading.Event(), threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(public_fetch, "DNS_SLOTS", slots)

    def resolve(*args, **kwargs):
        entered.set()
        release.wait(1)
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(public_fetch.socket, "getaddrinfo", resolve)
    cancel = threading.Event()
    cancel.set()
    try:
        with pytest.raises(InterruptedError):
            public_fetch.resolve_public(
                "fixture.example", 443, deadline=time.monotonic() + 1, cancel=cancel
            )
        assert entered.is_set()
        with pytest.raises(ValueError, match="busy"):
            public_fetch.resolve_public(
                "fixture.example", 443, deadline=time.monotonic() + 1, cancel=None
            )
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "oida-public-dns":
                thread.join(2)
    assert slots.acquire(blocking=False)
    slots.release()
