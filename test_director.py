import unittest

from ember import EmberDirector


class EmberDirectorTests(unittest.TestCase):
    def test_quiet_openings_vary_and_obey_gap(self):
        now = [1000.0]
        director = EmberDirector(
            {"director_minimum_initiative_gap_seconds": 120}, clock=lambda: now[0]
        )
        first = director.decide(silence=180, change=0, quiet_trigger=True)
        self.assertTrue(first.act)
        director.observe_response(first.intent)
        now[0] += 30
        self.assertFalse(director.decide(silence=210, change=0, quiet_trigger=True).act)
        now[0] += 100
        second = director.decide(silence=310, change=0, quiet_trigger=True)
        self.assertTrue(second.act)
        self.assertNotEqual(first.intent, second.intent)

    def test_boss_start_prefers_companionship_over_media(self):
        director = EmberDirector()
        decision = director.decide(
            silence=0, change=0,
            game_event={"event_type": "boss_start", "salience": 8},
        )
        self.assertEqual(decision.intent, "warn_or_rally")
        self.assertFalse(decision.allow_media)
        self.assertEqual(decision.body_hint, "concerned")

    def test_visual_change_is_specific_observation(self):
        director = EmberDirector({"screen_change_threshold": 5})
        decision = director.decide(silence=60, change=7, quiet_trigger=False)
        self.assertTrue(decision.act)
        self.assertEqual(decision.intent, "specific_observation")


if __name__ == "__main__":
    unittest.main()
