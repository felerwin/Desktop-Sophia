import ast
import unittest
from pathlib import Path

from model_routing import hybrid_route


class HybridRoutingTests(unittest.TestCase):
    def test_direct_conversation_uses_terra(self):
        route = hybrid_route(direct=True)
        self.assertEqual(route["role"], "companion")
        self.assertEqual(route["model"], "gpt-5.6-terra")
        self.assertEqual(route["reasoning_effort"], "low")

    def test_open_scene_and_game_events_use_terra(self):
        self.assertEqual(hybrid_route(tool_turn=False)["model"], "gpt-5.6-terra")
        self.assertEqual(
            hybrid_route(tool_turn=True, has_game_event=True)["model"],
            "gpt-5.6-terra",
        )

    def test_routine_media_routing_uses_luna(self):
        route = hybrid_route(tool_turn=True, has_game_event=False)
        self.assertEqual(route["role"], "media_router")
        self.assertEqual(route["model"], "gpt-5.6-luna")
        self.assertEqual(route["reasoning_effort"], "none")


class EventLoggingRegressionTests(unittest.TestCase):
    def test_log_event_allows_an_event_type_field(self):
        tree = ast.parse(
            (Path(__file__).parent / "sophia.py").read_text(encoding="utf-8")
        )
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "log_event"
        )
        self.assertEqual(function.args.args[0].arg, "event_name")


if __name__ == "__main__":
    unittest.main()
