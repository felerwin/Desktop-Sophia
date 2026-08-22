import json
import tempfile
import unittest
from pathlib import Path

from game_events import GameEventEngine
from memory_store import MemoryStore


class GameSenseTests(unittest.TestCase):
    def setUp(self):
        self.observed = []
        self.engine = GameEventEngine(
            ".",
            {"game_activity_reaction_gap_seconds": 0},
            on_event=self.observed.append,
        )

    @staticmethod
    def state(**changes):
        packet = {
            "kind": "state",
            "health": 100,
            "power": 100,
            "target_health": 100,
            "combat": False,
            "resting": False,
            "mounted": False,
            "dead": False,
            "has_target": False,
            "hostile_target": False,
            "level": 10,
            "target_level": 10,
            "target_classification": 0,
            "threat": 0,
            "group_size": 1,
        }
        packet.update(changes)
        return packet

    def event_types(self):
        return [event["event_type"] for event in self.observed]

    def test_temporal_combat_detects_danger_recovery_and_hard_victory(self):
        self.engine._handle_pixel_packet(
            {"kind": "target", "name": "Stubborn Drake", "level": 12, "classification": 1}
        )
        self.engine._handle_pixel_packet(
            self.state(combat=True, has_target=True, hostile_target=True)
        )
        self.engine._pixel_combat["started_at"] -= 30
        self.engine._handle_pixel_packet(
            self.state(
                combat=True, health=20, target_health=0,
                has_target=True, hostile_target=True,
            )
        )
        self.engine._handle_pixel_packet(self.state(health=35, target_health=0))
        self.engine._handle_pixel_packet(self.state(health=60))

        types = self.event_types()
        self.assertIn("combat_start", types)
        self.assertIn("critical_health", types)
        self.assertIn("combat_end", types)
        self.assertIn("hard_fought_victory", types)
        self.assertIn("danger_recovered", types)
        victory = next(
            event for event in self.observed
            if event["event_type"] == "hard_fought_victory"
        )
        self.assertEqual(victory["details"]["target"], "Stubborn Drake")
        self.assertEqual(victory["evidence"], "telemetry_derived")
        self.assertGreaterEqual(victory["confidence"], 0.9)

    def test_activity_requires_stable_packets_before_transition(self):
        for _ in range(3):
            self.engine._handle_pixel_packet(self.state())
        self.assertEqual(self.engine.activity["name"], "exploration")

        for _ in range(2):
            self.engine._handle_pixel_packet(self.state(mounted=True))
        self.assertEqual(self.engine.activity["name"], "exploration")
        self.engine._handle_pixel_packet(self.state(mounted=True))

        self.assertEqual(self.engine.activity["name"], "travel")
        event = next(
            event for event in self.observed
            if event["event_type"] == "activity_change"
        )
        self.assertEqual(event["details"]["previous"], "exploration")
        self.assertEqual(event["details"]["activity"], "travel")

    def test_gear_delta_distinguishes_upgrade_from_swap(self):
        self.engine._handle_pixel_packet({
            "kind": "gear", "slot": 1, "item_id": 100, "quality": 1,
            "item_level": 10, "name": "Old Hood",
        })
        self.engine._handle_pixel_packet({
            "kind": "gear", "slot": 1, "item_id": 200, "quality": 2,
            "item_level": 15, "name": "Better Hood",
        })
        event = self.observed[-1]
        self.assertEqual(event["event_type"], "gear_upgrade")
        self.assertEqual(event["details"]["item_level_delta"], 5)
        self.assertEqual(event["details"]["previous"]["name"], "Old Hood")

    def test_death_transition_is_not_repeated_by_identical_state(self):
        self.engine._handle_pixel_packet(self.state(combat=True, health=10))
        self.engine._handle_pixel_packet(self.state(dead=True, health=0))
        self.engine._handle_pixel_packet(self.state(dead=True, health=0))
        deaths = [event for event in self.observed if event["event_type"] == "player_death"]
        self.assertEqual(len(deaths), 1)
        self.assertEqual(deaths[0]["priority"], "high")

    def test_context_and_persistence_keep_evidence_metadata(self):
        event = self.engine.inject(
            "zone_change", "Entered Ghostlands", {"zone": "Ghostlands"},
            "wow_pixel_bridge",
        )
        json.dumps(self.engine.context())
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(Path(folder) / "memory.db")
            try:
                store.record_game_event(event)
                stored = store.list_game_events(1)[0]
            finally:
                store.close()
        self.assertEqual(stored["evidence"], "telemetry")
        self.assertEqual(stored["confidence"], 0.98)
        self.assertEqual(stored["details"]["zone"], "Ghostlands")


if __name__ == "__main__":
    unittest.main()
