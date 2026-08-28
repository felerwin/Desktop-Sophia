import tempfile
import unittest
from pathlib import Path

from ember.runtime import AtomicJsonStore, SessionUsage, default_companion_memory


class AtomicJsonStoreTests(unittest.TestCase):
    def test_recovers_last_valid_generation_without_overwriting_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "memory.json"
            store = AtomicJsonStore(path, default_companion_memory)
            first = {"recent_observations": [{"note": "safe"}], "recent_utterances": []}
            store.save(first)
            store.save({"recent_observations": [], "recent_utterances": ["new"]})
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(store.load(), first)
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_missing_store_returns_independent_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AtomicJsonStore(Path(folder) / "missing.json", default_companion_memory)
            first, second = store.load(), store.load()
            first["recent_observations"].append("x")
            self.assertEqual(second["recent_observations"], [])


class SessionUsageTests(unittest.TestCase):
    def test_records_guarded_cost_without_losing_raw_usage(self):
        usage = SessionUsage()
        usage.record(input_tokens=10, output_tokens=2, cost=0.20, multiplier=1.25)
        self.assertEqual(usage.api_calls, 1)
        self.assertEqual(usage.input_tokens, 10)
        self.assertAlmostEqual(usage.estimated_cost, 0.20)
        self.assertAlmostEqual(usage.governed_cost, 0.25)


if __name__ == "__main__":
    unittest.main()
