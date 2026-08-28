import unittest

from ember.tts_protocol import should_log_worker_stderr, worker_command


class TTSProtocolTests(unittest.TestCase):
    def test_progress_repaints_are_drained_but_not_logged(self):
        self.assertFalse(should_log_worker_stderr("42%|####      | 420/1000"))
        self.assertTrue(should_log_worker_stderr("FutureWarning: dependency is deprecated"))
        self.assertFalse(should_log_worker_stderr("  "))

    def test_cancel_is_a_protocol_command_not_a_process_restart(self):
        self.assertEqual(worker_command("cancel"), {"cmd": "cancel"})
        self.assertNotIn("restart", worker_command("cancel").values())


if __name__ == "__main__":
    unittest.main()

