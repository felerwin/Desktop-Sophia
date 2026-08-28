"""Deterministic attention and performance direction for Ember."""
from __future__ import annotations

from collections import deque
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
    topic: str = ""
    body_only: bool = False


@dataclass
class CuriosityThread:
    topic: str
    source: str
    created_at: float
    last_asked_at: float = 0.0
    attempts: int = 0
    status: str = "open"


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
        self.last_emotion_at = self.clock()
        self.last_decay_at = self.last_emotion_at
        self.last_visual_reaction_at = 0.0
        self.last_event_key = ""
        self.last_event_at = 0.0
        self.last_body_event_key = ""
        self.last_body_event_at = 0.0
        self.event_history = deque(maxlen=12)
        self.intent_history = deque(maxlen=8)
        self.curiosity_threads: list[CuriosityThread] = []
        self.active_curiosity: CuriosityThread | None = None

    def observe_speech(self, text, tone="neutral"):
        now = self.clock()
        self.last_direct_speech_at = now
        self.engagement = min(1.0, self.engagement + 0.18)
        self.mood = {
            "frustrated": "concerned",
            "surprised": "excited",
        }.get(str(tone), "warm")
        self.last_emotion_at = now
        self.last_intent = "respond"
        if self.active_curiosity is not None:
            self.active_curiosity.status = "answered"
            self.active_curiosity = None

    def observe_response(self, intent="respond"):
        self.last_intent = str(intent)
        self.intent_history.append(str(intent))
        if intent != "respond":
            self.last_initiative_at = self.clock()

    def observe_affection(self, kind="headpat"):
        self.mood = "affectionate"
        self.engagement = min(1.0, self.engagement + 0.2)
        self.last_emotion_at = self.clock()
        self.last_intent = str(kind)
        self.intent_history.append(str(kind))

    def add_curiosity(self, topic, source="scene"):
        normalized = " ".join(str(topic or "").split())[:180]
        if not normalized:
            return None
        for thread in self.curiosity_threads:
            if thread.status == "open" and thread.topic.casefold() == normalized.casefold():
                return thread
        thread = CuriosityThread(normalized, str(source), self.clock())
        self.curiosity_threads.append(thread)
        self.curiosity_threads = self.curiosity_threads[-8:]
        return thread

    def record_outcome(self, *, intent, spoke, user_responded=False):
        """Feed performance results back into cadence without model interpretation."""
        self.observe_response(intent)
        if user_responded:
            self.engagement = min(1.0, self.engagement + 0.12)
        elif spoke and intent != "respond":
            self.engagement = max(0.15, self.engagement - 0.05)
        if self.active_curiosity is not None and spoke:
            self.active_curiosity.last_asked_at = self.clock()
            self.active_curiosity.attempts += 1
            if self.active_curiosity.attempts >= 2:
                self.active_curiosity.status = "retired"
                self.active_curiosity = None

    def accept_body_event(self, event):
        """Gate renderer reactions independently from conversational decisions."""
        event_type = str((event or {}).get("event_type") or "")
        if not event_type:
            return False
        priority = int(
            (event or {}).get("salience")
            or (9 if (event or {}).get("priority") == "critical" else 0)
        )
        now = self.clock()
        key = self._event_key(event_type, event or {})
        repeat_window = float(self.config.get("director_body_repeat_seconds", 8))
        if (
            priority < 8 and key == self.last_body_event_key
            and now - self.last_body_event_at < repeat_window
        ):
            return False
        self.last_body_event_key, self.last_body_event_at = key, now
        return True

    def decide(self, *, silence, change, game_event=None, quiet_trigger=False):
        self._decay()
        now = self.clock()
        event = game_event or {}
        event_type = str(event.get("event_type") or "")
        priority = int(event.get("salience") or (9 if event.get("priority") == "critical" else 0))
        if event_type:
            event_key = self._event_key(event_type, event)
            repeat_window = float(self.config.get("director_event_repeat_seconds", 12))
            if (
                priority < 8 and event_key == self.last_event_key
                and now - self.last_event_at < repeat_window
            ):
                return DirectorDecision(
                    False, "hold_reaction", self.mood, "duplicate_game_event",
                    self._event_performance(event_type, priority)[2],
                    priority=priority, topic=str(event.get("title") or event_type),
                )
            intent, tone, body = self._event_performance(event_type, priority)
            self.last_event_key, self.last_event_at = event_key, now
            self.event_history.append({
                "event_type": event_type, "intent": intent, "tone": tone,
                "at": now, "priority": priority,
            })
            self._absorb_event_emotion(tone, priority, now)
            decision = DirectorDecision(
                True, intent, tone, f"game_event:{event_type}", body,
                allow_media=event_type in {"zone_change", "activity_change", "boss_victory"},
                priority=max(priority, 5), topic=str(event.get("title") or event_type),
            )
            self.last_intent = intent
            return decision

        threshold = float(self.config.get("screen_change_threshold", 5.0))
        if change >= threshold:
            visual_gap = float(self.config.get("director_visual_reaction_gap_seconds", 20))
            if self.last_visual_reaction_at and now - self.last_visual_reaction_at < visual_gap:
                return DirectorDecision(
                    False, "track_change", self.mood, "visual_reaction_cooldown",
                    "curious", priority=2, topic="continuing visual change",
                    body_only=True,
                )
            self.last_visual_reaction_at = now
            return DirectorDecision(
                True, "specific_observation", "curious", "meaningful_visual_change",
                "curious", False, 4, "current visual change",
            )

        initiative_gap = float(self.config.get("director_minimum_initiative_gap_seconds", 120))
        # Engaged conversations invite a slightly sooner follow-up; when Ember has
        # repeatedly spoken without response, she gives the room more air.
        cadence_factor = 1.3 - (0.55 * self.engagement)
        effective_gap = initiative_gap * cadence_factor
        if quiet_trigger and now - self.last_initiative_at >= effective_gap:
            curiosity = self._next_curiosity(now)
            if curiosity is not None:
                intent = "follow_up_curiosity"
                topic = curiosity.topic
                reason = "open_curiosity_thread"
                self.active_curiosity = curiosity
            else:
                intent = self._next_opening()
                topic = "shared current context"
                reason = "quiet_time_opening"
            return DirectorDecision(
                True, intent, self.mood, reason, "curious", False, 3, topic,
            )
        return DirectorDecision(False, "observe", self.mood, "nothing_salient")

    def context(self):
        return {
            "mood": self.mood,
            "engagement": round(self.engagement, 2),
            "last_intent": self.last_intent,
            "recent_intents": list(self.intent_history),
            "energy": self._energy(),
            "recent_events": list(self.event_history)[-4:],
            "open_curiosity_threads": [
                asdict(thread) for thread in self.curiosity_threads if thread.status == "open"
            ],
            "direction": (
                "Choose words that serve the Director intent. Body and voice should "
                "support the same emotional beat; do not narrate the intent."
            ),
        }

    def _next_opening(self):
        for _ in self.OPENINGS:
            intent = self.OPENINGS[self.opening_index % len(self.OPENINGS)]
            self.opening_index += 1
            if intent not in list(self.intent_history)[-2:]:
                return intent
        return "playful_check_in"

    def _next_curiosity(self, now):
        cooldown = float(self.config.get("director_curiosity_retry_seconds", 600))
        candidates = [
            thread for thread in self.curiosity_threads
            if thread.status == "open" and thread.attempts < 2
            and (not thread.last_asked_at or now - thread.last_asked_at >= cooldown)
        ]
        # Fresh scene questions beat old retried questions; a retry remains possible
        # once nothing newer is waiting.
        candidates.sort(key=lambda thread: (thread.attempts, -thread.created_at))
        return candidates[0] if candidates else None

    def _decay(self):
        now = self.clock()
        emotion_age = max(0.0, now - self.last_emotion_at)
        tick_age = max(0.0, now - self.last_decay_at)
        if emotion_age >= float(self.config.get("director_mood_decay_seconds", 600)):
            self.mood = "warm"
        self.engagement = max(0.2, self.engagement - min(0.05, tick_age / 36000))
        self.last_decay_at = now

    def _absorb_event_emotion(self, tone, priority, now):
        self.mood = {
            "excited": "excited", "concerned": "concerned", "tense": "focused",
            "relieved": "relieved", "amused": "playful", "proud": "proud",
        }.get(tone, self.mood)
        self.engagement = min(1.0, self.engagement + (0.12 if priority >= 7 else 0.05))
        self.last_emotion_at = now

    def _energy(self):
        if self.mood in {"excited", "playful", "proud"}: return "bright"
        if self.mood in {"concerned", "focused"}: return "focused"
        if self.engagement < 0.35: return "quiet"
        return "settled"

    @staticmethod
    def _event_key(event_type, event):
        identity = event.get("id") or event.get("title") or event.get("target") or ""
        return f"{event_type}:{str(identity).strip().casefold()}"

    @staticmethod
    def _event_performance(event_type, priority):
        if event_type in {"player_death", "hardcore_player_death", "boss_wipe"}:
            return "support", "concerned", "worried"
        if event_type in {"critical_health", "boss_start"}:
            return "warn_or_rally", "tense", "concerned"
        if event_type in {"boss_victory", "level_up", "quest_complete", "hard_fought_victory"}:
            return "celebrate", "excited", "excited"
        if event_type in {"valuable_loot", "gear_upgrade"}:
            return "admire", "proud", "proud"
        if event_type in {"danger_recovered", "resurrection"}:
            return "release_tension", "relieved", "relieved"
        if event_type in {"player_mistake", "failed_pull"}:
            return "gentle_tease", "amused", "mischievous"
        if event_type in {"zone_change", "activity_change"}:
            return "orient_and_notice", "curious", "curious"
        if priority >= 7:
            return "react", "attentive", "startled"
        return "observe", "curious", "curious"
