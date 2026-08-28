import unittest

from speech_naturalizer import normalize_spoken_text


class SpeechNaturalizerTests(unittest.TestCase):
    def test_normalizes_spacing_without_flattening_punctuation(self):
        self.assertEqual(
            normalize_spoken_text("Wait—look   at that!!!!"),
            "Wait—look at that!",
        )

    def test_preserves_a_continuous_multi_sentence_utterance(self):
        self.assertEqual(
            normalize_spoken_text("Oh! Is that ours? I love it.", max_sentences=3),
            "Oh! Is that ours? I love it.",
        )

    def test_limits_default_speech_to_two_sentences(self):
        self.assertEqual(normalize_spoken_text("One. Two! Three?"), "One. Two!")


if __name__ == "__main__":
    unittest.main()
