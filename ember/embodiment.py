"""Semantic body interface for Ember's movement and reaction poses."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Callable, Protocol


class BodyAdapter(Protocol):
    def submit(self, command: dict) -> None: ...


class SpriteBodyAdapter:
    """Keep Ember's semantic body commands independent of the sprite renderer."""

    def __init__(self, overlay):
        self.overlay = overlay

    def submit(self, command: dict) -> None:
        self.overlay.submit(command)


class BodyState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    AMUSED = "amused"
    EXCITED = "excited"
    CONCERNED = "concerned"
    STARTLED = "startled"
    LAUGHING = "laughing"
    FACEPALMING = "facepalming"
    EMBARRASSED = "embarrassed"
    SHY = "shy"
    WORRIED = "worried"
    CRYING = "crying"
    SMUG = "smug"
    PROUD = "proud"
    CURIOUS = "curious"
    DETERMINED = "determined"
    SLEEPY = "sleepy"
    ANNOYED = "annoyed"
    CONFUSED = "confused"
    SKEPTICAL = "skeptical"
    AFFECTIONATE = "affectionate"
    RELIEVED = "relieved"
    MISCHIEVOUS = "mischievous"
    POINTING = "pointing"
    MOVING = "moving"


def body_state_for_speech(text: str) -> BodyState:
    """Choose a readable conversational pose while Ember delivers a line."""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    rules = (
        (BodyState.PROUD, r"\b(?:proud|nailed it|well done|look at you)\b"),
        (BodyState.CURIOUS, r"\b(?:curious|wonder|what if|how does|tell me more)\b"),
        (BodyState.DETERMINED, r"\b(?:we've got this|lets do this|let's do this|determined|not giving up)\b"),
        (BodyState.SLEEPY, r"\b(?:sleepy|tired|yawn|bedtime|exhausted)\b"),
        (BodyState.ANNOYED, r"\b(?:annoying|annoyed|ugh|irritating|fed up)\b"),
        (BodyState.CONFUSED, r"\b(?:confused|doesn't make sense|do not understand|wait what)\b"),
        (BodyState.SKEPTICAL, r"\b(?:skeptical|doubt|not convinced|are you sure|suspicious)\b"),
        (BodyState.AFFECTIONATE, r"\b(?:love you|love that|dear|darling|my favorite)\b"),
        (BodyState.RELIEVED, r"\b(?:relieved|thank goodness|that was close|safe now|finally)\b"),
        (BodyState.MISCHIEVOUS, r"\b(?:mischief|naughty|sneaky|chaos|bad idea)\b"),
        (BodyState.LAUGHING, r"\b(?:haha|hahaha|lmao|rofl|hilarious|cracking me up)\b"),
        (BodyState.FACEPALMING, r"\b(?:facepalm|oh god|oh no|seriously|what a mess)\b"),
        (BodyState.EMBARRASSED, r"\b(?:embarrass|awkward|blush|mortif)\w*\b"),
        (BodyState.SHY, r"\b(?:sweet of you|flatter|adorable)\w*\b"),
        (BodyState.WORRIED, r"\b(?:careful|danger|worried|worry|hurt|health|dying)\b"),
        (BodyState.CRYING, r"\b(?:crying|heartbroken|devastat|so sad)\w*\b"),
        (BodyState.STARTLED, r"\b(?:whoa|woah|holy|what the|startled|scared me)\b"),
        (BodyState.SMUG, r"\b(?:told you|called it|knew it|obviously|as expected)\b"),
        (BodyState.EXCITED, r"\b(?:awesome|amazing|excellent|victory|level up|congrat|hell yes)\w*\b"),
        (BodyState.AMUSED, r"\b(?:funny|cute|nice|hehe|teasing|gremlin|good one)\b"),
        (BodyState.CONCERNED, r"\b(?:sorry|problem|error|failed|wrong|rough)\b"),
    )
    for state, pattern in rules:
        if re.search(pattern, normalized):
            return state
    if normalized.endswith("!"):
        return BodyState.EXCITED
    return BodyState.SPEAKING


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
    def __init__(self, renderer: Callable[[dict], None] | BodyAdapter | None = None):
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
        if hasattr(self.renderer, "submit"):
            self.renderer.submit(command)
        elif self.renderer:
            self.renderer(command)
