import pytest
from oida.capture_registry import CaptureRegistry, Registration


def request(url="https://example.org/audio.mp3"):
    return Registration(
        url=url,
        consent_ref="owner:consent",
        rights_ref="owner:rights",
        source_ref="station:source",
        retention="temp_only",
        consent="granted",
    )


def test_idempotent_registry_separate_from_configuration(tmp_path):
    registry = CaptureRegistry(tmp_path, {})
    item = registry.register(request())
    assert registry.register(request("https://EXAMPLE.org:443/audio.mp3")) == item
    assert len(registry.entries()) == 1
    assert registry.source(item["id"]).runtime_registered
    registry.revoke(item["id"])
    assert registry.source(item["id"]) is None
    replacement = registry.register(request())
    assert replacement["id"] == item["id"]
    assert replacement["revision"] != item["revision"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a",
        "http://169.254.169.254/a",
        "https://host.ts.net/a",
        "https://user:pass@example.org/a",
        "file:///etc/passwd",
        "https://example.org/a.m3u8",
    ],
)
def test_escape_and_hls_refused(tmp_path, url):
    with pytest.raises(ValueError):
        CaptureRegistry(tmp_path, {}).register(request(url))


def test_cap_and_conflicting_permission_refused(tmp_path):
    registry = CaptureRegistry(tmp_path, {})
    for i in range(64):
        registry.register(request(f"https://example.org/{i}.mp3"))
    with pytest.raises(ValueError):
        registry.register(request("https://example.org/extra.mp3"))
    original = request("https://example.org/0.mp3")
    with pytest.raises(ValueError):
        registry.register(original.model_copy(update={"consent_ref": "different"}))


def test_dns_and_redirect_admission(monkeypatch):
    from types import SimpleNamespace
    from oida import public_fetch

    visited = []
    monkeypatch.setattr(
        public_fetch.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, **kwargs):
            visited.append(path)

        def getresponse(self):
            return SimpleNamespace(
                status=302, getheader=lambda key: "http://127.0.0.1/private"
            )

        def close(self):
            pass

    monkeypatch.setattr(public_fetch.http.client, "HTTPSConnection", Connection)
    with pytest.raises(ValueError):
        public_fetch.PublicFetcher().get("https://example.org/")

    assert visited == ["/"]
    monkeypatch.setattr(
        public_fetch.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(ValueError):
        public_fetch.PublicFetcher().get("https://example.org/")


def test_guarded_direct_stream_preserves_actual_format(tmp_path, monkeypatch):
    import io
    import shutil
    import threading
    import numpy as np
    import soundfile as sf
    from types import SimpleNamespace
    from oida.public_fetch import PublicFetcher
    from oida.source_capture import capture_audio
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg unavailable')
    stream = io.BytesIO()
    sf.write(stream, np.zeros(4410),44100,format='WAV')
    monkeypatch.setattr(PublicFetcher, 'get', lambda *a, **k: SimpleNamespace(data=stream.getvalue(), content_type='audio/wav'))
    registry = CaptureRegistry(tmp_path/'registry',{})
    source = registry.source(registry.register(request())['id'])
    destination = tmp_path/'result.wav'
    capture_audio(source,0.1,destination,threading.Event())
    assert sf.info(destination).samplerate == 44100
    assert sf.info(destination).channels == 1
    monkeypatch.setattr(PublicFetcher, 'get', lambda *a, **k: SimpleNamespace(data=b'#EXTM3U\nhttp://127.0.0.1/private', content_type='audio/mpeg'))
    with pytest.raises(ValueError, match='HLS'):
        capture_audio(source,0.1,tmp_path/'forbidden.wav',threading.Event())
