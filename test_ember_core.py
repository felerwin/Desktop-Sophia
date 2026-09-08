import unittest
import tempfile
import threading
import json
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from dashboard_server import DashboardHub
from ember import BodyState, EmbodimentController, SpriteAtlas, WorldState
from ember.overlay import EmberOverlay, add_pose_inbetweens, direction_degrees
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

    def test_pose_inbetweens_preserve_keyframes_and_add_blended_frames(self):
        black = Image.new("RGBA", (1, 1), (0, 0, 0, 255))
        white = Image.new("RGBA", (1, 1), (255, 255, 255, 255))

        frames = add_pose_inbetweens([black, white], count=1, loop=False)

        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0].getpixel((0, 0)), (0, 0, 0, 255))
        self.assertEqual(frames[1].getpixel((0, 0)), (127, 127, 127, 255))
        self.assertEqual(frames[2].getpixel((0, 0)), (255, 255, 255, 255))

    def test_added_frames_keep_original_animation_duration(self):
        self.assertEqual(EmberOverlay._frame_interval("idle", 12), 180)
        self.assertEqual(EmberOverlay._frame_interval("running-right", 16), 72)

    def test_embodiment_emits_animation_choreography(self):
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

    def test_mobile_turns_are_validated_and_queued(self):
        with tempfile.TemporaryDirectory() as folder:
            dashboard = DashboardHub(Path(folder), {}, threading.Event())
            result = dashboard.submit_mobile_turn("  Hello,   Ember!  ", "Tony's phone")
            turn = dashboard.pop_mobile_turn()

        self.assertTrue(result["accepted"])
        self.assertEqual(turn["text"], "Hello, Ember!")
        self.assertEqual(turn["source"], "android")
        self.assertIsNone(dashboard.pop_mobile_turn())
        with self.assertRaises(ValueError):
            dashboard.submit_mobile_turn("   ")

    def test_mobile_snapshot_carries_body_state(self):
        with tempfile.TemporaryDirectory() as folder:
            dashboard = DashboardHub(Path(folder), {}, threading.Event())
            dashboard.record_body_command({"action": "state", "state": "excited"})
            snapshot = dashboard.mobile_snapshot()

        self.assertEqual(snapshot["body"]["state"], "excited")

    def test_mobile_http_api_requires_token(self):
        with tempfile.TemporaryDirectory() as folder:
            dashboard = DashboardHub(
                Path(folder), {"mobile_access_token": "secret-test-token"}, threading.Event()
            )
            dashboard.start(port=0, open_browser=False)
            port = dashboard.server.server_address[1]
            url = f"http://127.0.0.1:{port}/api/mobile/state"
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(url)
                self.assertEqual(denied.exception.code, 401)

                request = urllib.request.Request(
                    url, headers={"Authorization": "Bearer secret-test-token"}
                )
                with urllib.request.urlopen(request) as response:
                    payload = json.load(response)
                self.assertEqual(payload["body"]["state"], "idle")
            finally:
                dashboard.stop()


if __name__ == "__main__":
    unittest.main()
