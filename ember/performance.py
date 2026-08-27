"""Coordinate one spoken line with Ember's body lifecycle."""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable

from .embodiment import BodyState, body_state_for_speech


StateSetter = Callable[[BodyState, str], None]
TimerFactory = Callable[[float, Callable[[], None]], threading.Timer]


@dataclass
class SpeechPerformance:
    """A single queued line, including its voice timing and body direction."""

    text: str
    timing: dict = field(default_factory=dict)
    opening_state: BodyState | None = None
    done: threading.Event | None = None
    talk_transition_seconds: float = 1.15
    _timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _finished: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.text = str(self.text).strip()
        self.timing = dict(self.timing or {})
        if self.opening_state is None:
            self.opening_state = body_state_for_speech(self.text)

    @property
    def expressive(self) -> bool:
        return self.opening_state not in {BodyState.SPEAKING, BodyState.IDLE}

    def begin(self, set_state: StateSetter) -> None:
        self._finished = False
        set_state(self.opening_state or BodyState.SPEAKING, "speech_reaction")

    def audio_started(
        self,
        set_state: StateSetter,
        timer_factory: TimerFactory = threading.Timer,
    ) -> None:
        if self._finished:
            return
        if not self.expressive:
            set_state(BodyState.SPEAKING, "speech_audio_start")
            return

        def transition() -> None:
            if not self._finished:
                set_state(BodyState.SPEAKING, "speech_talk_transition")

        self._timer = timer_factory(self.talk_transition_seconds, transition)
        self._timer.daemon = True
        self._timer.start()

    def finish(self, set_state: StateSetter, return_to_idle: bool) -> None:
        self._finished = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self.done is not None:
            self.done.set()
        if return_to_idle:
            set_state(BodyState.IDLE, "speech_complete")

