import unittest

from ember import BodyState, DirectorDecision, plan_performance


class PerformancePlanTests(unittest.TestCase):
    def test_critical_performance_suppresses_media(self):
        plan = plan_performance(DirectorDecision(
            True, "warn_or_rally", "tense", "boss", "concerned", True, 9,
        ))
        self.assertTrue(plan.interrupt)
        self.assertFalse(plan.allow_media)
        self.assertEqual(plan.body_state, BodyState.CONCERNED)

    def test_support_allows_two_sentences(self):
        plan = plan_performance(DirectorDecision(
            True, "support", "concerned", "death", "worried", False, 9,
        ))
        self.assertEqual(plan.speech_max_sentences, 2)
        self.assertEqual(plan.prompt_context()["body_state"], "worried")

    def test_unknown_pose_degrades_to_idle(self):
        plan = plan_performance(DirectorDecision(True, "observe", "warm", "test", "bogus"))
        self.assertEqual(plan.body_state, BodyState.IDLE)


if __name__ == "__main__":
    unittest.main()
