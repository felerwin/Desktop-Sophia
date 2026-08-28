import unittest
import tempfile
import threading
from pathlib import Path

from dashboard_server import DashboardHub
from ember import BodyState, EmbodimentController, ReactionImages, WorldState, body_state_for_speech
from ember.overlay import EmberOverlay, direction_degrees, is_headpat_point, target_destination
from ember.telemetry import WowTelemetryAdapter


class EmberWorldStateTests(unittest.TestCase):
    def test_headpat_hitbox_is_small_and_head_shaped(self):
        self.assertTrue(is_headpat_point(96, 30, 192, 208))
        self.assertFalse(is_headpat_point(10, 30, 192, 208))
        self.assertFalse(is_headpat_point(96, 180, 192, 208))
    def test_spoken_lines_choose_conversational_emotes(self):
        self.assertEqual(body_state_for_speech("That is hilarious."), BodyState.LAUGHING)
        self.assertEqual(body_state_for_speech("I told you that would work."), BodyState.SMUG)
        self.assertEqual(body_state_for_speech("Careful, your health is low."), BodyState.WORRIED)
        self.assertEqual(body_state_for_speech("I am proud of you."), BodyState.PROUD)
        self.assertEqual(body_state_for_speech("Are you sure about that?"), BodyState.SKEPTICAL)
        self.assertEqual(body_state_for_speech("That was close, but we're safe now."), BodyState.RELIEVED)
        self.assertEqual(body_state_for_speech("Here is the answer."), BodyState.SPEAKING)

    def test_overlay_settles_at_destination_without_unpacking_focus_metadata(self):
        self.assertEqual(target_destination((120, 340, 900, 500)), (120, 340))
        self.assertEqual(target_destination((10, 20)), (10, 20))

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

    def test_overlay_loads_independent_static_reactions_and_look_directions(self):
        images = ReactionImages(Path(__file__).parent / "ember" / "assets" / "reactions")
        self.assertEqual(images.reaction("idle").size, (192, 208))
        self.assertEqual(images.reaction("amused").size, (192, 208))
        self.assertEqual(images.reaction("laughing").size, (192, 208))
        self.assertEqual(images.reaction("facepalming").size, (192, 208))
        self.assertEqual(images.look(270).size, (192, 208))
        self.assertEqual(len(images.animation("idle")), 6)
        self.assertEqual(len(images.animation("moving-left")), 8)
        self.assertEqual(len(images.animation("moving-right")), 8)
        for reaction in (
            "proud", "curious", "determined", "sleepy", "annoyed",
            "confused", "skeptical", "affectionate", "relieved", "mischievous",
        ):
            image = images.reaction(reaction)
            self.assertEqual(image.size, (192, 208))
            alpha = image.getchannel("A")
            edges = (
                alpha.crop((0, 0, 192, 1)), alpha.crop((0, 207, 192, 208)),
                alpha.crop((0, 0, 1, 208)), alpha.crop((191, 0, 192, 208)),
            )
            self.assertTrue(all(edge.getbbox() is None for edge in edges), reaction)

    def test_screen_deltas_use_clockwise_up_zero_directions(self):
        self.assertEqual(direction_degrees(0, -1), 0)
        self.assertEqual(direction_degrees(1, 0), 90)
        self.assertEqual(direction_degrees(0, 1), 180)
        self.assertEqual(direction_degrees(-1, 0), 270)

    def test_embodiment_emits_static_reaction_sequence(self):
        commands = []
        body = EmbodimentController(commands.append)

        body.perform([BodyState.EXCITED, BodyState.AMUSED, BodyState.EXCITED], "level_up")

        self.assertEqual(commands, [{
            "action": "sequence",
            "states": ["excited", "amused", "excited"],
            "reason": "level_up",
        }])

    def test_dashboard_body_lab_uses_safe_presets(self):
        received = []
        with tempfile.TemporaryDirectory() as folder:
            dashboard = DashboardHub(Path(folder), {}, threading.Event())
            dashboard.set_body_test_handler(received.append)

            result = dashboard.test_body("celebrate")

        self.assertEqual(result["states"], ["excited", "amused", "excited"])
        self.assertEqual(received, [["excited", "amused", "excited"]])
        with self.assertRaises(ValueError):
            dashboard.test_body("invented-animation")

    def test_dashboard_audio_output_selection_persists_and_reloads_voice(self):
        received = []
        config = {}
        with tempfile.TemporaryDirectory() as folder:
            dashboard = DashboardHub(Path(folder), config, threading.Event())
            dashboard.set_audio_output_controls([{
                "id": "14",
                "index": 14,
                "name": "Headphones",
                "hostapi": "Windows WASAPI",
                "label": "Headphones · Windows WASAPI",
            }], lambda name, hostapi: received.append((name, hostapi)))

            result = dashboard.update_audio_output("14")

        self.assertEqual(result["name"], "Headphones")
        self.assertEqual(config["tts_output_device"], "Headphones")
        self.assertEqual(config["tts_output_hostapi"], "Windows WASAPI")
        self.assertEqual(received, [("Headphones", "Windows WASAPI")])


if __name__ == "__main__":
    unittest.main()
