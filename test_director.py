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

    def test_open_curiosity_becomes_next_initiative_and_then_resolves(self):
        now = [1000.0]
        director = EmberDirector(clock=lambda: now[0])
        director.add_curiosity("whether the dispatch puzzle was resolved", "scene")
        decision = director.decide(silence=200, change=0, quiet_trigger=True)
        self.assertEqual(decision.intent, "follow_up_curiosity")
        self.assertIn("dispatch", decision.topic)
        director.record_outcome(intent=decision.intent, spoke=True)
        director.observe_speech("Yeah, we resolved it.")
        self.assertEqual(director.context()["open_curiosity_threads"], [])

    def test_unanswered_curiosity_retires_after_two_attempts(self):
        now = [1000.0]
        director = EmberDirector(
            {"director_curiosity_retry_seconds": 10}, clock=lambda: now[0]
        )
        director.add_curiosity("the mysterious door")
        first = director.decide(silence=200, change=0, quiet_trigger=True)
        director.record_outcome(intent=first.intent, spoke=True)
        now[0] += 130
        second = director.decide(silence=330, change=0, quiet_trigger=True)
        director.record_outcome(intent=second.intent, spoke=True)
        self.assertEqual(director.context()["open_curiosity_threads"], [])

    def test_mood_decays_back_to_warm(self):
        now = [1000.0]
        director = EmberDirector({"director_mood_decay_seconds": 60}, clock=lambda: now[0])
        director.observe_speech("This is ridiculous", "frustrated")
        self.assertEqual(director.mood, "concerned")
        now[0] += 61
        director.decide(silence=0, change=0)
        self.assertEqual(director.mood, "warm")

    def test_headpat_updates_affection_and_engagement(self):
        director = EmberDirector()
        before = director.engagement
        director.observe_affection()
        self.assertEqual(director.mood, "affectionate")
        self.assertGreater(director.engagement, before)
        self.assertEqual(director.context()["last_intent"], "headpat")

    def test_duplicate_minor_event_is_suppressed_but_critical_is_not(self):
        now = [1000.0]
        director = EmberDirector(clock=lambda: now[0])
        event = {"event_type": "valuable_loot", "title": "Blue sword", "salience": 5}
        self.assertTrue(director.decide(silence=0, change=0, game_event=event).act)
        self.assertFalse(director.decide(silence=0, change=0, game_event=event).act)
        critical = {"event_type": "critical_health", "title": "Tony", "salience": 9}
        self.assertTrue(director.decide(silence=0, change=0, game_event=critical).act)
        self.assertTrue(director.decide(silence=0, change=0, game_event=critical).act)

    def test_visual_reactions_have_a_refractory_period(self):
        now = [1000.0]
        director = EmberDirector(
            {"screen_change_threshold": 5, "director_visual_reaction_gap_seconds": 20},
            clock=lambda: now[0],
        )
        self.assertTrue(director.decide(silence=60, change=8).act)
        now[0] += 5
        held = director.decide(silence=65, change=9)
        self.assertFalse(held.act)
        self.assertEqual(held.reason, "visual_reaction_cooldown")
        now[0] += 16
        self.assertTrue(director.decide(silence=81, change=7).act)

    def test_event_emotion_is_exposed_to_the_performer(self):
        director = EmberDirector()
        decision = director.decide(
            silence=0, change=0,
            game_event={"event_type": "danger_recovered", "salience": 7},
        )
        self.assertEqual(decision.body_hint, "relieved")
        self.assertEqual(director.context()["mood"], "relieved")
        self.assertEqual(director.context()["energy"], "settled")

    def test_fresh_curiosity_precedes_an_old_retry(self):
        now = [1000.0]
        director = EmberDirector(
            {"director_curiosity_retry_seconds": 10}, clock=lambda: now[0]
        )
        old = director.add_curiosity("old mystery")
        old.attempts = 1
        old.last_asked_at = 900
        now[0] += 1
        director.add_curiosity("fresh mystery")
        decision = director.decide(silence=200, change=0, quiet_trigger=True)
        self.assertEqual(decision.topic, "fresh mystery")


if __name__ == "__main__":
    unittest.main()
