"""Translate existing GameEventEngine output into Ember's semantic world model."""
from __future__ import annotations

from collections import deque

from .core import SemanticEvent, WorldState


class WowTelemetryAdapter:
    def __init__(self, world: WorldState):
        self.world = world
        self._seen_signatures: set[tuple[str, str, str, str]] = set()
        self._signature_order: deque[tuple[str, str, str, str]] = deque(maxlen=128)

    def ingest_context(self, context: dict | None) -> None:
        if not context:
            return
        self.world.game = "World of Warcraft"
        self.world.apply_live_state(context.get("live_state") or {})
        for raw in reversed((context.get("recent_events") or [])[:8]):
            self.ingest_event(raw)

    def ingest_event(self, raw: dict | None) -> SemanticEvent | None:
        if not raw:
            return None
        signature = (
            str(raw.get("event_type") or "game_event"),
            str(raw.get("title") or "Game state changed"),
            str(raw.get("source") or "wow"),
            str(raw.get("time") or ""),
        )
        if signature in self._seen_signatures:
            return None
        if len(self._signature_order) == self._signature_order.maxlen:
            self._seen_signatures.discard(self._signature_order[0])
        self._signature_order.append(signature)
        self._seen_signatures.add(signature)
        details = dict(raw.get("details") or {})
        details.setdefault("game", raw.get("game") or "World of Warcraft")
        event = SemanticEvent(
            kind=str(raw.get("event_type") or "game_event"),
            summary=str(raw.get("title") or "Game state changed"),
            source=str(raw.get("source") or "wow"),
            priority=str(raw.get("priority") or "normal"),
            details=details,
        )
        self.world.apply_event(event)
        return event
