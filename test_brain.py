import unittest

from ember import EmberBrain, RunningBitManager, WorldState


class EmberBrainTests(unittest.TestCase):
    def test_hardcore_player_death_ends_run_and_forces_interrupt(self):
        brain = EmberBrain(WorldState(), {
            "wow_player_name": "PetBrittney",
            "wow_character_class": "Hunter",
            "wow_game_mode": "Hardcore",
        })

        plan = brain.observe_game_event({
            "event_type": "player_death",
            "title": "Player death detected",
            "source": "wow_pixel_bridge",
            "details": {"health_percent": 0},
        })
        context = brain.context()

        self.assertEqual(plan.priority, 10)
        self.assertTrue(plan.respond)
        self.assertTrue(plan.interrupt)
        self.assertFalse(plan.allow_running_bits)
        self.assertEqual(context["world_state"]["live"]["character"], "PetBrittney")
        self.assertEqual(context["world_state"]["live"]["class"], "Hunter")
        self.assertEqual(context["world_state"]["live"]["game_mode"], "Hardcore")
        self.assertEqual(context["world_state"]["live"]["run_status"], "Ended")
        self.assertEqual(context["critical_event"]["kind"], "hardcore_player_death")

    def test_standard_death_does_not_claim_permanent_run_end(self):
        brain = EmberBrain(WorldState(), {"wow_game_mode": "Standard"})
        plan = brain.observe_game_event({"event_type": "player_death", "details": {}})

        self.assertEqual(plan.priority, 9)
        self.assertNotIn("run_status", brain.context()["world_state"]["live"])

    def test_user_frustration_joins_same_world_state(self):
        brain = EmberBrain(WorldState(), {"wow_game_mode": "Hardcore"})
        brain.observe_game_event({"event_type": "player_death", "details": {}})
        plan = brain.observe_speech("You've gotta be fucking kidding me")

        context = brain.context()
        self.assertEqual(plan.priority, 10)
        self.assertEqual(plan.topic, "the current character's Hardcore run ended")
        self.assertEqual(plan.tone, "frustrated")
        self.assertEqual(context["world_state"]["user_tone"], "frustrated")
        self.assertIsNotNone(context["critical_event"])

    def test_running_bits_decay_and_require_user_revival(self):
        bits = RunningBitManager()
        bits.register("The murlocs unionized", now=100)
        bits.register("The murlocs unionized", now=200)

        self.assertEqual(bits.available(now=1000), [])
        bits.revive_from_user("Are the murlocs still unionized?")
        self.assertEqual(bits.available(now=201), ["The murlocs unionized"])


if __name__ == "__main__":
    unittest.main()
