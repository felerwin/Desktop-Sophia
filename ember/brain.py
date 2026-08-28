"""Deterministic cognitive layer shared by every Ember input."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
import re
import time
from typing import Any, Callable

from .core import SemanticEvent, WorldState


SALIENCE = {
    "enemy_kill": 1,
    "loot_pickup": 2,
    "target_changed": 2,
    "zone_change": 3,
    "activity_change": 3,
    "user_speech": 5,
    "gear_upgrade": 6,
    "valuable_loot": 7,
    "quest_complete": 7,
    "level_up": 8,
    "boss_victory": 8,
    "boss_wipe": 8,
    "critical_health": 9,
    "player_death": 9,
    "hardcore_player_death": 10,
}


@dataclass
class ResponsePlan:
    respond: bool
    priority: int
    topic: str
    tone: str
    length: str = "short"
    speech_act: str = "acknowledge"
    allow_running_bits: bool = True
    interrupt: bool = False
    reason: str = ""


@dataclass
class RunningBit:
    topic: str
    uses: int = 0
    last_used_at: float = 0.0
    cooldown_seconds: float = 300.0
    revived: bool = False


class RunningBitManager:
    """Keep callbacks scarce and prevent jokes from becoming world facts."""

    def __init__(self, max_bits: int = 12):
        self.bits: dict[str, RunningBit] = {}
        self.max_bits = max_bits

    def register(self, topic: str, now: float | None = None) -> RunningBit:
        key = self._key(topic)
        bit = self.bits.get(key) or RunningBit(topic=str(topic).strip()[:160])
        bit.uses += 1
        bit.last_used_at = now if now is not None else time.time()
        bit.revived = False
        self.bits[key] = bit
        if len(self.bits) > self.max_bits:
            oldest = min(self.bits, key=lambda item: self.bits[item].last_used_at)
            self.bits.pop(oldest, None)
        return bit

    def revive_from_user(self, text: str) -> None:
        normalized = self._key(text)
        for key, bit in self.bits.items():
            words = {word for word in key.split() if len(word) > 4}
            if words and sum(word in normalized for word in words) >= min(2, len(words)):
                bit.revived = True

    def available(self, now: float | None = None) -> list[str]:
        current = now if now is not None else time.time()
        result = []
        for bit in self.bits.values():
            use_limit = 3 if bit.revived else 2
            cooldown_ready = current - bit.last_used_at >= bit.cooldown_seconds
            if bit.uses < use_limit and (cooldown_ready or bit.revived):
                result.append(bit.topic)
        return result

    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", str(text).casefold()).strip()


class EventNormalizer:
    @staticmethod
    def game(raw: dict[str, Any]) -> SemanticEvent:
        details = dict(raw.get("details") or {})
        details.setdefault("game", raw.get("game") or "World of Warcraft")
        return SemanticEvent(
            kind=str(raw.get("event_type") or "game_event"),
            summary=str(raw.get("title") or "Game state changed"),
            source=str(raw.get("source") or "game"),
            priority=str(raw.get("priority") or "normal"),
            details=details,
        )

    @staticmethod
    def speech(text: str) -> SemanticEvent:
        normalized = str(text or "").strip()
        tone = "neutral"
        if re.search(r"\b(fuck|fucking|shit|damn|kidding me|seriously)\b", normalized, re.I):
            tone = "frustrated"
        elif re.search(r"[!?]{2,}|\b(holy|whoa|wow)\b", normalized, re.I):
            tone = "surprised"
        return SemanticEvent(
            kind="user_speech", summary=normalized, source="microphone",
            priority="high", details={"text": normalized, "tone_estimate": tone},
        )


class EmberBrain:
    """Owns normalized events, unified state, attention, bits, and plans."""

    def __init__(
        self,
        world: WorldState | None = None,
        config: dict[str, Any] | None = None,
        memory_retriever: Callable[[str, int], list[dict]] | None = None,
    ):
        self.world = world or WorldState()
        self.config = config if config is not None else {}
        self.memory_retriever = memory_retriever
        self.working_memory: deque[SemanticEvent] = deque(maxlen=40)
        self.bits = RunningBitManager()
        self.current_plan: ResponsePlan | None = None
        self.critical_event: SemanticEvent | None = None

    def observe_game_event(self, raw: dict[str, Any]) -> ResponsePlan:
        event = EventNormalizer.game(raw)
        self._apply(event)
        hardcore = self._is_hardcore(event.details)
        if event.kind == "player_death" and hardcore:
            event.kind = "hardcore_player_death"
            event.priority = "critical"
            event.details["game_mode"] = "Hardcore"
            event.details["run_status"] = "Ended"
            self.world.live["dead"] = True
            self.world.live["status"] = "Dead"
            self.world.live["run_status"] = "Ended"
            self.critical_event = event
        plan = self.plan(event)
        self.current_plan = plan
        return plan

    def observe_speech(self, text: str) -> ResponsePlan:
        event = EventNormalizer.speech(text)
        self.bits.revive_from_user(text)
        self._apply(event)
        plan = self.plan(event)
        if self.critical_event is not None and self.current_plan and self.current_plan.priority >= 9:
            plan = ResponsePlan(
                respond=True,
                priority=self.current_plan.priority,
                topic=self.current_plan.topic,
                tone=str(event.details.get("tone_estimate") or self.current_plan.tone),
                length="short",
                speech_act="respond to Tony in the critical moment",
                allow_running_bits=False,
                interrupt=True,
                reason="Tony's speech belongs to the active critical event.",
            )
        self.current_plan = plan
        return plan

    def plan(self, event: SemanticEvent) -> ResponsePlan:
        priority = SALIENCE.get(event.kind, 4 if event.priority == "high" else 2)
        if event.kind == "hardcore_player_death":
            character = self.world.live.get("character") or "the current character"
            return ResponsePlan(
                respond=True, priority=10, topic=f"{character}'s Hardcore run ended",
                tone="shocked and sympathetic", length="short",
                speech_act="immediate reaction", allow_running_bits=False,
                interrupt=True,
                reason="Authoritative telemetry reported permanent Hardcore player death.",
            )
        if event.kind == "player_death":
            return ResponsePlan(
                True, priority, "player character death", "concerned", "short",
                "immediate reaction", False, True,
                "Authoritative telemetry reported player death.",
            )
        if event.kind == "user_speech":
            return ResponsePlan(
                True, priority, self.world.conversation_topic or event.summary,
                str(event.details.get("tone_estimate") or "neutral"), "brief",
                "answer", priority < 9, False, "Tony spoke directly to Ember.",
            )
        respond = priority >= 6
        return ResponsePlan(
            respond, priority, event.summary,
            "celebratory" if event.kind in {"level_up", "boss_victory", "valuable_loot"} else "attentive",
            "short", "react" if respond else "observe",
            priority < 8, priority >= 9,
            "Salience threshold selected this event." if respond else "Event remains context only.",
        )

    def context(self, query: str = "") -> dict[str, Any]:
        memories = []
        if self.memory_retriever and query:
            memories = self.memory_retriever(query, 4) or []
        return {
            "world_state": self.world.snapshot(recent_events=8),
            "attention": asdict(self.current_plan) if self.current_plan else None,
            "critical_event": asdict(self.critical_event) if self.critical_event else None,
            "relevant_memory": memories,
            "available_running_bits": (
                self.bits.available() if not self.current_plan or self.current_plan.allow_running_bits else []
            ),
            "rule": "Telemetry is authoritative. Jokes are callbacks, never facts.",
        }

    def record_response(self, text: str) -> None:
        self.world.last_response = str(text).strip()[:500]
        self.world.updated_at = datetime.now().isoformat(timespec="seconds")
        self.current_plan = None

    def _apply(self, event: SemanticEvent) -> None:
        details = event.details
        character = details.get("character") or details.get("name") or self.config.get("wow_player_name")
        if character:
            self.world.live["character"] = str(character)
        character_class = details.get("class") or self.config.get("wow_character_class")
        if character_class:
            self.world.live["class"] = str(character_class)
        mode = details.get("game_mode") or details.get("mode") or self.config.get("wow_game_mode")
        if mode:
            self.world.live["game_mode"] = str(mode)
        if event.kind == "user_speech":
            self.world.user_tone = str(details.get("tone_estimate") or "neutral")
            self.world.conversation_topic = event.summary[:180]
        self.world.apply_event(event)
        self.working_memory.appendleft(event)

    def _is_hardcore(self, details: dict[str, Any]) -> bool:
        value = details.get("hardcore")
        if isinstance(value, bool):
            return value
        mode = details.get("game_mode") or details.get("mode") or self.config.get("wow_game_mode")
        return str(mode or "").strip().casefold() == "hardcore" or bool(self.config.get("wow_hardcore"))
