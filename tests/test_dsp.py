from __future__ import annotations

import math
import unittest

import numpy as np

from oida.dsp import AudioData, analyze_audio
from oida.reporting import corroborate_events
from oida.reportschema import Event


class DspTests(unittest.TestCase):
    def test_full_scale_sine_matches_reference_levels(self) -> None:
        sample_rate = 48_000
        duration_s = 3
        t = np.arange(sample_rate * duration_s) / sample_rate
        sine = np.sin(2 * math.pi * 1000 * t).astype(np.float32)[:, None]

        features = analyze_audio(AudioData(sine, sample_rate, 1, duration_s))

        self.assertAlmostEqual(features.rmsDbfs or 0, -3.0103, places=3)
        self.assertAlmostEqual(features.integratedLufs or 0, -3.05, delta=0.12)
        self.assertAlmostEqual(features.zeroCrossingRate or 0, 2000, delta=2)

    def test_long_file_preserves_transient_peak_and_clipping(self) -> None:
        # > 1,000,000 samples so the previous [::step] decimation used step >= 2 and
        # dropped odd indices. A single full-scale transient at an odd index must still
        # be reflected in peak level and clipping ratio.
        sample_rate = 48_000
        duration_s = 25
        samples = np.zeros(sample_rate * duration_s, dtype=np.float32)
        samples[1] = 1.0

        features = analyze_audio(AudioData(samples[:, None], sample_rate, 1, duration_s))

        self.assertIsNotNone(features.peakDbfs)
        self.assertGreater(features.peakDbfs, -1.0)  # full-scale transient -> ~0 dBFS
        self.assertIsNotNone(features.clippedSampleRatio)
        self.assertGreater(features.clippedSampleRatio, 0.0)

    def test_click_track_yields_120_bpm_candidate(self) -> None:
        sample_rate = 48_000
        duration_s = 10
        clicks = np.zeros(sample_rate * duration_s, dtype=np.float32)
        clicks[:: sample_rate // 2] = 1.0

        features = analyze_audio(AudioData(clicks[:, None], sample_rate, 1, duration_s))

        self.assertIsNotNone(features.bpmCandidate)
        self.assertAlmostEqual(features.bpmCandidate or 0, 120, delta=1)
        self.assertIsInstance(features.onsetTimes, list)
        self.assertGreater(len(features.onsetTimes or []), 8)

    def test_event_corroboration_uses_onset_window_not_global_count(self) -> None:
        events = [
            Event(t0=0.95, t1=1.05, label="near click", description="", confidence="medium"),
            Event(t0=4.0, t1=4.5, label="far claim", description="", confidence="medium"),
        ]

        result = corroborate_events(events, {"onsetCount": 12, "onsetTimes": [1.0]})

        self.assertTrue(result[0].corroborated_by_dsp)
        self.assertEqual(result[0].confidence, "high")
        self.assertFalse(result[1].corroborated_by_dsp)
        self.assertEqual(result[1].confidence, "medium")


if __name__ == "__main__":
    unittest.main()
