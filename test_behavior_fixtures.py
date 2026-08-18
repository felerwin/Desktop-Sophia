import json
import unittest
from pathlib import Path


class BehaviorFixtureTests(unittest.TestCase):
    def test_fixture_set_stays_small_and_high_signal(self):
        fixtures = json.loads(
            (Path(__file__).parent / "behavior_fixtures.json").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(len(fixtures), 8)
        self.assertLessEqual(len(fixtures), 10)
        self.assertEqual(len({item["id"] for item in fixtures}), len(fixtures))
        self.assertTrue(all(item["scenario"] and item["expected"] for item in fixtures))
        self.assertEqual(
            {item["layer"] for item in fixtures},
            {"deterministic", "manual_model_review"},
        )


if __name__ == "__main__":
    unittest.main()
