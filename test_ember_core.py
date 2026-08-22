import unittest

from ember import WorldState
from ember.telemetry import WowTelemetryAdapter


class EmberWorldStateTests(unittest.TestCase):
    def test_semantic_adapter_deduplicates_replayed_context(self):
        world = WorldState()
        adapter = WowTelemetryAdapter(world)
        event = {
            "event_type": "zone_change",
            "title": "Entered Eversong Woods",
            "source": "wow_pixel_bridge",
            "time": "12:34:56",
            "details": {"zone": "Eversong Woods"},
        }
        context = {"live_state": {"zone": "Eversong Woods"}, "recent_events": [event]}

        adapter.ingest_context(context)
        adapter.ingest_context(context)

        snapshot = world.snapshot()
        self.assertEqual(snapshot["game"], "World of Warcraft")
        self.assertEqual(snapshot["location"], "Eversong Woods")
        self.assertEqual(len(snapshot["recent_events"]), 1)


if __name__ == "__main__":
    unittest.main()
