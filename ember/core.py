"""Core data model for Portable Ember v0.2.

Keep this module boring and dependency-free. Hardware adapters feed observations into
WorldState; the reasoning layer consumes a compact semantic snapshot. This prevents
raw telemetry from becoming prompt wallpaper.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from collections import deque
from datetime import datetime
from typing import Any


@dataclass
class HostCapabilities:
    screen_capture: bool = False
    microphone: bool = False
    local_tts: bool = False
    gpu: bool = False
    wow_telemetry: bool = False
    overlay: bool = False
    spotify: bool = False
    youtube: bool = False

    def available(self) -> list[str]:
        return [name for name, enabled in asdict(self).items() if enabled]


@dataclass
class SemanticEvent:
    kind: str
    summary: str
    source: str = "unknown"
    priority: str = "normal"
    details: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class VisualObservation:
    game: str | None = None
    location: str | None = None
    activity: str | None = None
    summary: str | None = None
    confidence: float | None = None
    targets: list[dict[str, Any]] = field(default_factory=list)


class WorldState:
    """Persistent, compact understanding of what is happening on the host."""

    def __init__(self, event_limit: int = 24):
        self.game: str | None = None
        self.location: str | None = None
        self.activity: str | None = None
        self.visual_summary: str | None = None
        self.visual_confidence: float | None = None
        self.live: dict[str, Any] = {}
        self.targets: list[dict[str, Any]] = []
        self.events: deque[SemanticEvent] = deque(maxlen=event_limit)
        self.updated_at: str | None = None

    def apply_visual(self, observation: VisualObservation) -> None:
        if observation.game:
            self.game = observation.game
        if observation.location:
            self.location = observation.location
        if observation.activity:
            self.activity = observation.activity
        if observation.summary:
            self.visual_summary = observation.summary
        if observation.confidence is not None:
            self.visual_confidence = observation.confidence
        if observation.targets:
            self.targets = observation.targets
        self._touch()

    def apply_event(self, event: SemanticEvent) -> None:
        self.events.appendleft(event)
        details = event.details
        if event.kind == "zone_change" and details.get("zone"):
            self.location = str(details["zone"])
        if details.get("game"):
            self.game = str(details["game"])
        self._touch()

    def apply_live_state(self, state: dict[str, Any]) -> None:
        # Only retain useful semantic fields. Adapters can contain implementation noise.
        allowed = {
            "health", "power", "combat", "dead", "level", "class", "race",
            "zone", "subzone", "target_name", "target_level", "target_classification",
            "pvp", "group_size", "quest_title", "objective",
        }
        self.live.update({k: v for k, v in state.items() if k in allowed and v is not None})
        if self.live.get("zone"):
            self.location = str(self.live["zone"])
        self._touch()

    def snapshot(self, recent_events: int = 8) -> dict[str, Any]:
        return {
            "game": self.game,
            "location": self.location,
            "activity": self.activity,
            "visual_summary": self.visual_summary,
            "visual_confidence": self.visual_confidence,
            "live": dict(self.live),
            "recent_events": [asdict(e) for e in list(self.events)[:recent_events]],
            "screen_targets": list(self.targets),
            "updated_at": self.updated_at,
        }

    def _touch(self) -> None:
        self.updated_at = datetime.now().isoformat(timespec="seconds")
