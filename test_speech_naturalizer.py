import unittest

from speech_naturalizer import normalize_spoken_text


class SpeechNaturalizerTests(unittest.TestCase):
    def test_normalizes_spacing_without_flattening_punctuation(self):
        self.assertEqual(
            normalize_spoken_text("Wait—look   at that!!!!"),
            "Wait—look at that!!!",
        )

    def test_preserves_a_continuous_multi_sentence_utterance(self):
        self.assertEqual(
            normalize_spoken_text("Oh! Is that ours? I love it."),
            "Oh! Is that ours? I love it.",
        )


if __name__ == "__main__":
    unittest.main()
