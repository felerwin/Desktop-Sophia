import unittest
from pathlib import Path

from ember import BodyState, EmbodimentController, SpriteAtlas, WorldState
from ember.overlay import direction_degrees
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

    def test_overlay_atlas_exposes_animation_rows_and_look_directions(self):
        atlas = SpriteAtlas(Path(__file__).parent / "ember" / "assets" / "spritesheet.webp")
        self.assertEqual(len(atlas.frames("idle")), 6)
        self.assertEqual(len(atlas.frames("running-right")), 8)
        self.assertEqual(atlas.look_frame(270).size, (192, 208))

    def test_screen_deltas_use_clockwise_up_zero_directions(self):
        self.assertEqual(direction_degrees(0, -1), 0)
        self.assertEqual(direction_degrees(1, 0), 90)
        self.assertEqual(direction_degrees(0, 1), 180)
        self.assertEqual(direction_degrees(-1, 0), 270)

    def test_embodiment_emits_animation_choreography(self):
        commands = []
        body = EmbodimentController(commands.append)

        body.perform([BodyState.EXCITED, BodyState.AMUSED, BodyState.EXCITED], "level_up")

        self.assertEqual(commands, [{
            "action": "sequence",
            "states": ["excited", "amused", "excited"],
            "reason": "level_up",
        }])


if __name__ == "__main__":
    unittest.main()
