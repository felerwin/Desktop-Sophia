"""Offline Director replay: no devices, model calls, or application startup."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from .director import EmberDirector
from .performance_plan import plan_performance


@dataclass(frozen=True)
class ReplaySignal:
    at: float
    silence: float = 0.0
    change: float = 0.0
    quiet_trigger: bool = False
    game_event: dict = field(default_factory=dict)
    curiosity: str = ""
    user_speech: str = ""
    user_tone: str = "neutral"


def replay_signals(signals: Iterable[ReplaySignal], config=None) -> list[dict]:
    """Replay synthetic signals and return inspectable decisions and state snapshots."""
    clock_value = [0.0]
    director = EmberDirector(config or {}, clock=lambda: clock_value[0])
    timeline = []
    for signal in signals:
        clock_value[0] = float(signal.at)
        if signal.curiosity:
            director.add_curiosity(signal.curiosity, "replay")
        if signal.user_speech:
            director.observe_speech(signal.user_speech, signal.user_tone)
        decision = director.decide(
            silence=signal.silence,
            change=signal.change,
            game_event=signal.game_event,
            quiet_trigger=signal.quiet_trigger,
        )
        performance = plan_performance(decision)
        timeline.append({
            "at": signal.at,
            "decision": asdict(decision),
            "performance": performance.prompt_context(),
            "director": director.context(),
        })
        if decision.act:
            director.record_outcome(intent=decision.intent, spoke=True)
    return timeline
