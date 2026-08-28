import threading
import unittest

from ember import TranscriptInbox, wait_for_transcript


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


if __name__ == "__main__":
    unittest.main()
