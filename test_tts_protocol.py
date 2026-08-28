import unittest

from ember.tts_protocol import parse_worker_event, should_log_worker_stderr, worker_command


class TTSProtocolTests(unittest.TestCase):
    def test_worker_event_parser_separates_protocol_from_diagnostics(self):
        self.assertEqual(parse_worker_event('{"event":"READY","voice":"Ember"}')["event"], "READY")
        self.assertIsNone(parse_worker_event("downloading model"))
        self.assertIsNone(parse_worker_event("{}"))

    def test_progress_repaints_are_drained_but_not_logged(self):
        self.assertFalse(should_log_worker_stderr("42%|####      | 420/1000"))
        self.assertTrue(should_log_worker_stderr("FutureWarning: dependency is deprecated"))
        self.assertFalse(should_log_worker_stderr("  "))

    def test_cancel_is_a_protocol_command_not_a_process_restart(self):
        self.assertEqual(worker_command("cancel"), {"cmd": "cancel"})
        self.assertNotIn("restart", worker_command("cancel").values())


if __name__ == "__main__":
    unittest.main()
