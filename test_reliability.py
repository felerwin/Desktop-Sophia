import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dashboard_server import DashboardHub
from game_events import GameEventEngine
from speech_filter import transcript_rejection_reason
from wow_pixel_bridge import WowPixelBridge


class ScaledGridShot:
    def __init__(self, symbols, scale, origin=(2, 2), size=260):
        self.symbols = symbols
        self.scale = scale
        self.origin = origin
        self.width = size
        self.height = size

    def pixel(self, x, y):
        relative_x = x - self.origin[0]
        relative_y = y - self.origin[1]
        if relative_x < 0 or relative_y < 0:
            return (23, 23, 23)
        column = int(relative_x / self.scale)
        row = int(relative_y / self.scale)
        if column < 0 or column >= 12 or row < 0 or row >= 12:
            return (23, 23, 23)
        return WowPixelBridge.PALETTE[self.symbols[row * 12 + column]]


class SpeechFilterTests(unittest.TestCase):
    def test_rejects_foreign_script_low_confidence_and_tiny_fragments(self):
        self.assertEqual(
            transcript_rejection_reason("什么?", average_logprob=-0.1, voiced_seconds=0.8),
            "non_english_script",
        )
        self.assertEqual(
            transcript_rejection_reason("Tuve lore della", average_logprob=-1.2, voiced_seconds=0.8),
            "low_confidence",
        )
        self.assertEqual(
            transcript_rejection_reason("Yes?", average_logprob=-0.1, voiced_seconds=0.2),
            "short_ambient_fragment",
        )

    def test_keeps_clear_conversation(self):
        self.assertIsNone(transcript_rejection_reason(
            "That's a kind of a hard time, huh?",
            average_logprob=-0.2,
            voiced_seconds=1.4,
        ))


class SoundCorrectionTests(unittest.TestCase):
    def test_voice_corrections_replace_bad_analysis_permanently(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            soundboard = root / "soundboard"
            soundboard.mkdir()
            (soundboard / "metal-pipe-sound.mp3").write_bytes(b"pipe")
            (soundboard / "erro.mp3").write_bytes(b"error")
            (soundboard / "library.json").write_text(json.dumps({
                "metal-pipe-sound.mp3": {
                    "status": "ready", "description": "rimshot", "use_when": "after jokes",
                },
                "erro.mp3": {
                    "status": "ready", "description": "fanfare", "use_when": "after wins",
                },
            }), encoding="utf-8")
            hub = DashboardHub(root, {}, threading.Event())

            corrections = hub.observe_sound_corrections(
                "But the metal pipes are metal pipes, and error is the Windows error message."
            )
            library = json.loads((soundboard / "library.json").read_text(encoding="utf-8"))

            self.assertEqual(len(corrections), 2)
            self.assertEqual(library["metal-pipe-sound.mp3"]["description"], "metal pipes")
            self.assertIn("Windows error", library["erro.mp3"]["description"])
            self.assertEqual(library["erro.mp3"]["description_source"], "user_voice")
            self.assertIn("never as a victory", library["erro.mp3"]["use_when"])


class CombatLogReliabilityTests(unittest.TestCase):
    def test_rewinds_reader_when_wow_truncates_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "WoWCombatLog.txt"
            path.write_text("new", encoding="utf-8")
            handle = io.StringIO("an older and much longer combat log")
            handle.seek(0, 2)

            self.assertTrue(GameEventEngine._rewind_if_truncated(handle, path))
            self.assertEqual(handle.tell(), 0)


class PixelBridgeReliabilityTests(unittest.TestCase):
    @staticmethod
    def state_packet_symbols():
        payload = bytes([87, 64, 45, 49, 12, 13, 2, 1, 1])
        raw = bytes([1, 7, 1, len(payload)]) + payload
        packet = raw + bytes([sum(raw) % 256])
        symbols = list(WowPixelBridge.MARKER)
        for value in packet:
            symbols.extend((value // 16, value % 16))
        return symbols + [0] * (12 * 12 - len(symbols))

    def test_decodes_fractionally_scaled_grid(self):
        statuses = []
        bridge = WowPixelBridge({}, on_status=statuses.append)
        shot = ScaledGridShot(self.state_packet_symbols(), scale=32 / 3)

        decoded = bridge._decode(shot)
        self.assertIsNotNone(decoded)
        bridge._handle(*decoded)

        self.assertEqual(bridge.cell_size, 10.667)
        self.assertEqual(bridge.snapshot()["status"], "live")
        self.assertEqual(bridge.snapshot()["state"]["health"], 87)
        self.assertEqual(statuses[-1]["status"], "live")

    def test_context_hides_stale_values_when_bridge_is_not_live(self):
        engine = GameEventEngine(".", {})
        engine.live_state = {"health": 12, "zone": "Stale Zone"}
        engine.pixel_bridge.state = dict(engine.live_state)
        engine.pixel_bridge.status = "searching"

        unavailable = engine.context()
        self.assertFalse(unavailable["telemetry_available"])
        self.assertEqual(unavailable["live_state"], {})
        self.assertIn("not live", unavailable["telemetry_warning"].lower())

        engine.pixel_bridge.status = "live"
        available = engine.context()
        self.assertTrue(available["telemetry_available"])
        self.assertEqual(available["live_state"]["health"], 12)


if __name__ == "__main__":
    unittest.main()
