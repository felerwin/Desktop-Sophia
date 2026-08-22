"""Semantic body interface for Ember.

The brain requests intent; this controller chooses body behavior. No API call should
be required for blinking, idling, walking, or synchronizing speech animation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class BodyState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    AMUSED = "amused"
    EXCITED = "excited"
    CONCERNED = "concerned"
    STARTLED = "startled"
    POINTING = "pointing"
    MOVING = "moving"


@dataclass
class ScreenTarget:
    """Normalized screen target. x/y are 0..1 so resolution does not matter."""
    x: float
    y: float
    label: str = "interesting thing"
    confidence: float = 1.0

    def __post_init__(self):
        self.x = min(1.0, max(0.0, float(self.x)))
        self.y = min(1.0, max(0.0, float(self.y)))


class EmbodimentController:
    def __init__(self, renderer: Callable[[dict], None] | None = None):
        self.renderer = renderer
        self.state = BodyState.IDLE
        self.target: ScreenTarget | None = None

    def set_state(self, state: BodyState, reason: str | None = None) -> None:
        self.state = state
        self._emit({"action": "state", "state": state.value, "reason": reason})

    def point_at(self, target: ScreenTarget, remark: str | None = None) -> None:
        self.target = target
        self.state = BodyState.POINTING
        self._emit({
            "action": "point_at",
            "state": self.state.value,
            "target": {"x": target.x, "y": target.y, "label": target.label, "confidence": target.confidence},
            "remark": remark,
        })

    def perform(self, states: list[BodyState], reason: str | None = None) -> None:
        sequence = [state.value for state in states if state != BodyState.IDLE]
        if not sequence:
            self.set_state(BodyState.IDLE, reason)
            return
        self.state = states[-1]
        self._emit({"action": "sequence", "states": sequence, "reason": reason})

    def clear_target(self) -> None:
        self.target = None
        self.set_state(BodyState.IDLE)

    def _emit(self, command: dict) -> None:
        if self.renderer:
            self.renderer(command)
