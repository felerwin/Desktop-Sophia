"""Translate Director intent into one coherent voice/body/media performance."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .director import DirectorDecision
from .embodiment import BodyState


@dataclass(frozen=True)
class PerformancePlan:
    intent: str
    tone: str
    body_state: BodyState
    topic: str = ""
    interrupt: bool = False
    allow_media: bool = False
    speech_max_sentences: int = 1
    gaze: str = "user"

    def prompt_context(self) -> dict:
        data = asdict(self)
        data["body_state"] = self.body_state.value
        return data


_BODY_STATES = {state.value: state for state in BodyState}


def plan_performance(decision: DirectorDecision) -> PerformancePlan:
    """Create a bounded performance contract without asking a model to direct itself."""
    body = _BODY_STATES.get(str(decision.body_hint).casefold(), BodyState.IDLE)
    interrupt = decision.priority >= 8
    return PerformancePlan(
        intent=decision.intent,
        tone=decision.tone,
        body_state=body,
        topic=decision.topic,
        interrupt=interrupt,
        allow_media=bool(decision.allow_media and not interrupt),
        speech_max_sentences=2 if decision.intent == "support" else 1,
        gaze="target" if decision.intent == "specific_observation" else "user",
    )
