import threading
import unittest

from ember import BodyState, EmberSpeechService, SpeechPerformance


class FakeProcess:
    def __init__(self):
        self.running = False
        self.sent = []
        self.events = []

    def start(self):
        self.running = True
        return {"event": "READY", "playback_backend": "fake"}

    def send(self, payload):
        self.sent.append(payload)

    def read_event(self, wanted):
        return self.events.pop(0)

    def shutdown(self):
        self.running = False


class SpeechServiceTests(unittest.TestCase):
    def test_performance_lifecycle_with_fake_process(self):
        process = FakeProcess()
        process.events = [
            {"event": "AUDIO_START", "synthesis_seconds": 0.2},
            {"event": "SPOKEN", "chars": 5, "playback_seconds": 0.4},
        ]
        states, logs, phases = [], [], []
        service = EmberSpeechService(
            ".", {"speak_out_loud": True},
            lambda event, **fields: logs.append(event),
            lambda state, reason: states.append(state), threading.Event(),
            lambda phase, label=None: phases.append(phase), process=process, autostart=False,
        )
        process.start()
        service._perform(SpeechPerformance("hello", opening_state=BodyState.AFFECTIONATE))
        self.assertEqual(process.sent, [{"text": "hello"}])
        self.assertIn("TTS_AUDIO_START", logs)
        self.assertIn("TTS_SPOKE", logs)
        self.assertEqual(states[0], BodyState.AFFECTIONATE)
        self.assertEqual(states[-1], BodyState.IDLE)
        self.assertEqual(phases[-1], "listening")

    def test_disabled_speech_does_not_queue(self):
        service = EmberSpeechService(
            ".", {"speak_out_loud": False}, lambda *args, **kwargs: None,
            lambda *args: None, threading.Event(), process=FakeProcess(), autostart=False,
        )
        self.assertFalse(service.say("nope"))
        self.assertTrue(service.queue.empty())


if __name__ == "__main__":
    unittest.main()
