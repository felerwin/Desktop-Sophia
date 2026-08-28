import unittest
import numpy as np

try:
    from ember.audio_output import prepare_playback_audio
except ModuleNotFoundError as exc:
    if exc.name != "scipy":
        raise
    prepare_playback_audio = None


@unittest.skipIf(prepare_playback_audio is None, "audio tests run in Chatterbox environment")
class AudioOutputTests(unittest.TestCase):
    def test_resample_24k_to_48k_preserves_duration_and_headroom(self):
        source = np.sin(2 * np.pi * 440 * np.arange(2400) / 24000).astype(np.float32) * 2
        output = prepare_playback_audio(source, 24000, 48000)
        self.assertAlmostEqual(len(output) / 48000, len(source) / 24000, places=3)
        self.assertLessEqual(float(np.max(np.abs(output))), 0.921)

    def test_invalid_samples_and_dc_are_removed(self):
        output = prepare_playback_audio([np.nan, np.inf, 2.0, 2.0], 24000, 24000)
        self.assertTrue(np.all(np.isfinite(output)))
        self.assertAlmostEqual(float(np.mean(output)), 0.0, places=5)

    def test_bad_rate_is_rejected(self):
        with self.assertRaises(ValueError):
            prepare_playback_audio([1.0], 0, 48000)


if __name__ == "__main__":
    unittest.main()
