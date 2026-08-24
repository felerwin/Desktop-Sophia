import unittest

from visual_context import parse_scene_envelope, scene_memory_note


class VisualContextTests(unittest.TestCase):
    def test_extracts_scene_and_leaves_action_protocol_intact(self):
        scene, action = parse_scene_envelope(
            'SCENE: {"game":"WoW","activity":"fishing","summary":"Tony is fishing",'
            '"change":"a catch landed","confidence":0.86}\nSAY: Nice catch!'
        )
        self.assertEqual(action, "SAY: Nice catch!")
        self.assertEqual(scene["activity"], "fishing")
        self.assertEqual(scene_memory_note(scene), "Visual scene: Tony is fishing | fishing | a catch landed")

    def test_accepts_legacy_action_and_clamps_confidence(self):
        self.assertEqual(parse_scene_envelope("SILENT: nothing changed"), (None, "SILENT: nothing changed"))
        scene, _ = parse_scene_envelope('SCENE: {"summary":"menu","confidence":4}\nSILENT: stable')
        self.assertEqual(scene["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
