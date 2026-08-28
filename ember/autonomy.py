"""Deterministic autonomy scheduling, independent of models and devices."""
from __future__ import annotations

import time


class RateCap:
    def __init__(self, max_per_minute, clock=None):
        self.max_per_minute = max(0, int(max_per_minute))
        self.clock = clock or time.time
        self._timestamps = []

    def allow(self):
        now = self.clock()
        self._timestamps = [stamp for stamp in self._timestamps if now - stamp < 60]
        if len(self._timestamps) >= self.max_per_minute:
            return False
        self._timestamps.append(now)
        return True


class AutonomyCadence:
    """Track tool/open turn cadence without coupling it to the main loop."""

    def __init__(self, tool_after=4, initial_streak=None):
        self.tool_after = max(2, int(tool_after))
        self.non_tool_streak = (
            self.tool_after if initial_streak is None else max(0, int(initial_streak))
        )

    def should_offer_tool(self, *, game_event=False, interesting_change=False, media=False):
        return bool(
            media and (game_event or (
                interesting_change and self.non_tool_streak >= self.tool_after
            ))
        )

    def record(self, tool_used):
        self.non_tool_streak = 0 if tool_used else self.non_tool_streak + 1
        return self.non_tool_streak
