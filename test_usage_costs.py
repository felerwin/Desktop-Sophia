import tempfile
import unittest
from pathlib import Path

from memory_store import MemoryStore
from usage_costs import budget_decision, response_cost, transcription_cost


class UsageCostTests(unittest.TestCase):
    def test_luna_cost_includes_cached_input_discount(self):
        self.assertAlmostEqual(
            response_cost("gpt-5.6-luna", 1_000_000, 1_000_000, 250_000),
            1.355,
        )

    def test_transcription_is_charged_by_audio_duration(self):
        self.assertAlmostEqual(
            transcription_cost("gpt-4o-mini-transcribe", 30), 0.0015
        )

    def test_budget_pauses_autonomy_but_override_resumes_it(self):
        paused = budget_decision(0.25, 0.15, 0.25, enabled=True, override=False)
        resumed = budget_decision(0.25, 0.15, 0.25, enabled=True, override=True)
        self.assertTrue(paused["warning"])
        self.assertTrue(paused["paused"])
        self.assertFalse(resumed["paused"])


class UsageLedgerTests(unittest.TestCase):
    def test_discarded_outputs_remain_in_cost_rollup(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(Path(folder) / "memory.db")
            session_id = store.start_session()
            malformed = store.record_usage_event(
                session_id, "conversation_response", "gpt-5.6-luna",
                billing_status="usage_returned", input_tokens=100,
                output_tokens=20, estimated_cost=0.001,
                governed_cost=0.00125,
            )
            rejected = store.record_usage_event(
                session_id, "transcription", "gpt-4o-mini-transcribe",
                billing_status="duration_estimate", audio_seconds=12,
                estimated_cost=0.0006, governed_cost=0.00075,
            )
            store.update_usage_outcome(malformed, "malformed_output")
            store.update_usage_outcome(rejected, "transcript_rejected", "ambient")

            rollup = store.usage_rollup(session_id=session_id)
            rows = store.connection.execute(
                "SELECT outcome, estimated_cost FROM usage_events ORDER BY created_at, id"
            ).fetchall()

            self.assertEqual(rollup["calls"], 2)
            self.assertAlmostEqual(rollup["estimated_cost"], 0.0016)
            self.assertAlmostEqual(rollup["governed_cost"], 0.002)
            self.assertEqual(
                {row["outcome"] for row in rows},
                {"malformed_output", "transcript_rejected"},
            )
            self.assertTrue(all(row["estimated_cost"] > 0 for row in rows))
            store.close()

    def test_unknown_usage_is_visible_instead_of_recorded_as_free(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(Path(folder) / "memory.db")
            session_id = store.start_session()
            event_id = store.record_usage_event(
                session_id, "conversation_response", "unknown-model",
                billing_status="unknown", outcome="api_error",
            )
            self.assertTrue(event_id)
            self.assertEqual(store.usage_rollup(session_id=session_id)["unknown_calls"], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
