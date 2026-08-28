import threading
import unittest

from ember import BodyState, SpeechPerformance


class FakeTimer:
    def __init__(self, seconds, callback):
        self.seconds = seconds
        self.callback = callback
        self.daemon = False
        self.cancelled = False

    def start(self):
        self.callback()

    def cancel(self):
        self.cancelled = True


class SpeechPerformanceTests(unittest.TestCase):
    def test_performance_is_not_mistaken_for_worker_command(self):
        performance = SpeechPerformance("Hello, Tony.")
        command = performance.get("command") if isinstance(performance, dict) else None
        self.assertIsNone(command)

    def test_expressive_line_reacts_then_talks_then_idles(self):
        states = []
        performance = SpeechPerformance("That is hilarious.")
        setter = lambda state, reason: states.append((state, reason))

        performance.begin(setter)
        performance.audio_started(setter, FakeTimer)
        performance.finish(setter, return_to_idle=True)

        self.assertEqual(
            [state for state, _ in states],
            [BodyState.LAUGHING, BodyState.SPEAKING, BodyState.IDLE],
        )

    def test_neutral_line_enters_talking_immediately(self):
        states = []
        performance = SpeechPerformance("Here is the answer.")
        setter = lambda state, reason: states.append((state, reason))

        performance.begin(setter)
        performance.audio_started(setter, FakeTimer)

        self.assertEqual([state for state, _ in states], [BodyState.SPEAKING, BodyState.SPEAKING])

    def test_intermediate_phrase_does_not_flash_idle(self):
        states = []
        done = threading.Event()
        performance = SpeechPerformance("Nice.", done=done)

        performance.finish(lambda state, reason: states.append(state), return_to_idle=False)

        self.assertTrue(done.is_set())
        self.assertEqual(states, [])


if __name__ == "__main__":
    unittest.main()
