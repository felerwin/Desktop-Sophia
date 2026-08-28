import unittest

from ember import AutonomyCadence, RateCap


class AutonomyTests(unittest.TestCase):
    def test_rate_cap_reopens_after_window(self):
        now = [100.0]
        cap = RateCap(2, clock=lambda: now[0])
        self.assertTrue(cap.allow())
        self.assertTrue(cap.allow())
        self.assertFalse(cap.allow())
        now[0] += 60
        self.assertTrue(cap.allow())

    def test_cadence_offers_media_only_when_due_and_available(self):
        cadence = AutonomyCadence(4, initial_streak=3)
        self.assertFalse(cadence.should_offer_tool(interesting_change=True, media=True))
        cadence.record(False)
        self.assertTrue(cadence.should_offer_tool(interesting_change=True, media=True))
        cadence.record(True)
        self.assertEqual(cadence.non_tool_streak, 0)
        self.assertFalse(cadence.should_offer_tool(interesting_change=True, media=True))

    def test_game_event_can_offer_available_media_immediately(self):
        cadence = AutonomyCadence(initial_streak=0)
        self.assertTrue(cadence.should_offer_tool(game_event=True, media=True))
        self.assertFalse(cadence.should_offer_tool(game_event=True, media=False))


if __name__ == "__main__":
    unittest.main()
