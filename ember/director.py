"""Deterministic attention and performance direction for Ember."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import time


@dataclass
class DirectorDecision:
    act: bool
    intent: str
    tone: str
    reason: str
    body_hint: str = "idle"
    allow_media: bool = False
    priority: int = 0


class EmberDirector:
    """Turns signals into an intent before an LLM is asked to perform it.

    The Director deliberately does not write dialogue. It controls attention,
    cadence, emotional continuity, and the kind of opening Ember should attempt.
    """

    OPENINGS = ("curiosity", "shared_callback", "playful_check_in", "specific_observation")

    def __init__(self, config=None, clock=None):
        self.config = config if config is not None else {}
        self.clock = clock or time.time
        self.mood = "warm"
        self.engagement = 0.5
        self.last_direct_speech_at = 0.0
        self.last_initiative_at = 0.0
        self.last_intent = "listen"
        self.opening_index = 0

    def observe_speech(self, text, tone="neutral"):
        self.last_direct_speech_at = self.clock()
        self.engagement = min(1.0, self.engagement + 0.18)
        self.mood = {
            "frustrated": "concerned",
            "surprised": "excited",
        }.get(str(tone), "warm")
        self.last_intent = "respond"

    def observe_response(self, intent="respond"):
        self.last_intent = str(intent)
        if intent != "respond":
            self.last_initiative_at = self.clock()

    def decide(self, *, silence, change, game_event=None, quiet_trigger=False):
        event = game_event or {}
        event_type = str(event.get("event_type") or "")
        priority = int(event.get("salience") or (9 if event.get("priority") == "critical" else 0))
        if event_type:
            intent, tone, body = self._event_performance(event_type, priority)
            decision = DirectorDecision(
                True, intent, tone, f"game_event:{event_type}", body,
                allow_media=event_type in {"zone_change", "activity_change", "boss_victory"},
                priority=max(priority, 5),
            )
            self.last_intent = intent
            return decision

        threshold = float(self.config.get("screen_change_threshold", 5.0))
        if change >= threshold:
            return DirectorDecision(
                True, "specific_observation", "curious", "meaningful_visual_change",
                "curious", False, 4,
            )

        initiative_gap = float(self.config.get("director_minimum_initiative_gap_seconds", 120))
        now = self.clock()
        if quiet_trigger and now - self.last_initiative_at >= initiative_gap:
            intent = self.OPENINGS[self.opening_index % len(self.OPENINGS)]
            self.opening_index += 1
            return DirectorDecision(
                True, intent, self.mood, "quiet_time_opening", "curious", False, 3,
            )
        return DirectorDecision(False, "observe", self.mood, "nothing_salient")

    def context(self):
        return {
            "mood": self.mood,
            "engagement": round(self.engagement, 2),
            "last_intent": self.last_intent,
            "direction": (
                "Choose words that serve the Director intent. Body and voice should "
                "support the same emotional beat; do not narrate the intent."
            ),
        }

    @staticmethod
    def _event_performance(event_type, priority):
        if event_type in {"player_death", "hardcore_player_death", "boss_wipe"}:
            return "support", "concerned", "worried"
        if event_type in {"critical_health", "boss_start"}:
            return "warn_or_rally", "tense", "concerned"
        if event_type in {"boss_victory", "level_up", "valuable_loot", "quest_complete"}:
            return "celebrate", "excited", "excited"
        if priority >= 7:
            return "react", "attentive", "startled"
        return "observe", "curious", "curious"

