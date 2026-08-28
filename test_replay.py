import unittest

from ember import ReplaySignal, replay_signals


class ReplayTests(unittest.TestCase):
    def test_replay_routes_curiosity_then_critical_event(self):
        timeline = replay_signals([
            ReplaySignal(1000, silence=200, quiet_trigger=True, curiosity="the odd door"),
            ReplaySignal(1100, game_event={"event_type": "boss_start", "salience": 9}),
        ])
        self.assertEqual(timeline[0]["decision"]["intent"], "follow_up_curiosity")
        self.assertEqual(timeline[1]["decision"]["intent"], "warn_or_rally")
        self.assertTrue(timeline[1]["performance"]["interrupt"])
        self.assertFalse(timeline[1]["performance"]["allow_media"])

    def test_replay_is_deterministic(self):
        signals = [ReplaySignal(1000, silence=200, quiet_trigger=True)]
        self.assertEqual(replay_signals(signals), replay_signals(signals))


if __name__ == "__main__":
    unittest.main()
