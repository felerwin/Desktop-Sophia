import unittest
from types import SimpleNamespace

from ember import normalize_local_transcription, normalize_provider_transcription


class TranscriptionTests(unittest.TestCase):
    def test_local_segments_share_one_result_contract(self):
        result = normalize_local_transcription([
            SimpleNamespace(text=" hello ", avg_logprob=-0.2),
            SimpleNamespace(text="Ember", avg_logprob=-0.4),
        ], 1.5)
        self.assertEqual(result.text, "hello Ember")
        self.assertAlmostEqual(result.average_logprob, -0.3)
        self.assertEqual(result.audio_seconds, 1.5)

    def test_provider_usage_and_logprobs_are_normalized(self):
        response = SimpleNamespace(
            text="  hi there ",
            usage=SimpleNamespace(input_tokens=12, output_tokens=3, seconds=2.25),
            logprobs=[SimpleNamespace(logprob=-0.1), SimpleNamespace(logprob=-0.3)],
        )
        result = normalize_provider_transcription(response, 1.0)
        self.assertEqual(result.text, "hi there")
        self.assertEqual(result.input_tokens, 12)
        self.assertAlmostEqual(result.average_logprob, -0.2)
        self.assertEqual(result.audio_seconds, 2.25)

    def test_provider_uses_fallback_duration_when_usage_is_absent(self):
        result = normalize_provider_transcription(SimpleNamespace(text="okay"), 0.75)
        self.assertEqual(result.audio_seconds, 0.75)


if __name__ == "__main__":
    unittest.main()
