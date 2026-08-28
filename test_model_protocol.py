import unittest

from ember.model_protocol import StreamedSpeechParser, parse_model_action


class ModelProtocolTests(unittest.TestCase):
    def test_action_parser_rejects_scene_leak_and_extra_preamble(self):
        self.assertIsNone(parse_model_action('SCENE: {}\nSAY: hello'))
        self.assertIsNone(parse_model_action('Certainly. SAY: hello'))
        action = parse_model_action('  point: {"x":0.5}  ')
        self.assertEqual((action.kind, action.content), ("POINT", '{"x":0.5}'))

    def test_stream_parser_releases_only_say_phrases(self):
        phrases = []
        parser = StreamedSpeechParser(lambda text, index: phrases.append((index, text)))
        parser.feed("SAY: This is the first sentence. And ")
        parser.feed("this is the second.")
        parser.finish()
        self.assertEqual(phrases[0], (1, "This is the first sentence."))
        self.assertEqual(phrases[1], (2, "And this is the second."))

        silent = []
        parser = StreamedSpeechParser(lambda text, index: silent.append(text))
        parser.feed("SILENT: nothing worth adding")
        parser.finish()
        self.assertEqual(silent, [])


if __name__ == "__main__":
    unittest.main()
