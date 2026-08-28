import unittest

from ember import SpeechPerformance, SpeechQueue


class SpeechQueueTests(unittest.TestCase):
    def test_drain_discards_only_speech_and_preserves_control_order(self):
        speech = SpeechPerformance("hello")
        queue = SpeechQueue()
        queue.put({"command": "change_output"})
        queue.put(speech)
        queue.stop()
        self.assertEqual(queue.drain_performances(), [speech])
        self.assertEqual(queue.get(), {"command": "change_output"})
        self.assertIs(queue.get(), queue.STOP)

    def test_empty_tracks_pending_work(self):
        queue = SpeechQueue()
        self.assertTrue(queue.empty())
        queue.put(SpeechPerformance("hello"))
        self.assertFalse(queue.empty())


if __name__ == "__main__":
    unittest.main()
