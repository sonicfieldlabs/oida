"""Song identification: opt-in, offline-safe, cached (mocked provider — no network)."""
import os
import tempfile
import unittest
from pathlib import Path

from oida import songid


class FakeProvider:
    def __init__(self, result: songid.SongIdResult):
        self.name = "fake"
        self.result = result
        self.calls = 0

    def identify(self, audio_path: Path) -> songid.SongIdResult:
        self.calls += 1
        return self.result


class TestSongId(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        self.f.write(b"RIFF....fake-audio-bytes")
        self.f.close()
        self.path = self.f.name
        os.environ.pop("OIDA_SONGID", None)

    def tearDown(self):
        os.unlink(self.path)
        os.environ.pop("OIDA_SONGID", None)

    def test_off_by_default(self):
        ext = songid.identify_song(self.path)
        self.assertFalse(ext["matched"])
        self.assertEqual(ext["provider"], "description")

    def test_enabled_match_shape(self):
        fake = FakeProvider(songid.SongIdResult(
            provider="fake", matched=True, title="Windowlicker", artist="Aphex Twin", isrc="GBAAA0000001"
        ))
        ext = songid.identify_song(self.path, enabled=True, provider=fake)
        self.assertTrue(ext["matched"])
        self.assertEqual(ext["title"], "Windowlicker")
        self.assertEqual(ext["artist"], "Aphex Twin")
        self.assertEqual(ext["isrc"], "GBAAA0000001")
        self.assertIn("checked_at", ext)

    def test_offline_degrades_to_no_match(self):
        fake = FakeProvider(songid.SongIdResult(provider="fake", matched=False, note="offline"))
        ext = songid.identify_song(self.path, enabled=True, provider=fake)
        self.assertFalse(ext["matched"])
        self.assertEqual(ext["note"], "offline")

    def test_cache_by_audio_hash(self):
        fake = FakeProvider(songid.SongIdResult(provider="fake", matched=True, title="T"))
        cache: dict = {}
        songid.identify_song(self.path, enabled=True, provider=fake, cache=cache)
        songid.identify_song(self.path, enabled=True, provider=fake, cache=cache)
        self.assertEqual(fake.calls, 1)  # second call served from cache

    def test_enrich_akousma_places_result_in_extensions(self):
        fake = FakeProvider(songid.SongIdResult(provider="fake", matched=True, title="T", artist="A"))
        record = {"extensions": {}}
        songid.enrich_akousma(record, self.path, enabled=True, provider=fake)
        self.assertTrue(record["extensions"]["songid"]["matched"])
        self.assertEqual(record["extensions"]["songid"]["title"], "T")

    def test_env_toggle(self):
        fake = FakeProvider(songid.SongIdResult(provider="fake", matched=True, title="T"))
        self.assertFalse(songid.identify_song(self.path, provider=fake)["matched"])  # env unset -> off
        os.environ["OIDA_SONGID"] = "1"
        self.assertTrue(songid.identify_song(self.path, provider=fake)["matched"])   # env on


if __name__ == "__main__":
    unittest.main()
