import threading
import unittest

from ember import TranscriptInbox, UtteranceSegmenter, wait_for_transcript


class EarsTests(unittest.TestCase):
    def test_pending_transcript_wakes_immediately(self):
        inbox = TranscriptInbox()
        inbox.put({"text": "hey Ember"})
        self.assertEqual(
            wait_for_transcript(10, inbox, threading.Event()),
            {"text": "hey Ember"},
        )

    def test_shutdown_prevents_wait(self):
        shutdown = threading.Event()
        shutdown.set()
        self.assertIsNone(wait_for_transcript(10, TranscriptInbox(), shutdown))

    def test_segmenter_includes_tail_silence_and_finishes(self):
        segmenter = UtteranceSegmenter(end_silence=0.2, min_speech=0.1, max_speech=5)
        self.assertIsNone(segmenter.feed("voice", True, 1.0))
        self.assertIsNone(segmenter.feed("voice2", True, 1.1))
        utterance = segmenter.feed("tail", False, 1.31)
        self.assertEqual(utterance.frames, ("voice", "voice2", "tail"))
        self.assertAlmostEqual(utterance.last_loud_at, 1.1)

    def test_segmenter_discards_tiny_fragment_and_resets(self):
        segmenter = UtteranceSegmenter(end_silence=0.1, min_speech=0.5, max_speech=5)
        segmenter.feed("blip", True, 1.0)
        self.assertIsNone(segmenter.feed("tail", False, 1.11))
        self.assertIsNone(segmenter.speech_started_at)


if __name__ == "__main__":
    unittest.main()
