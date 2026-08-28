"""Device-free transcript queue and wake-up behavior for Ember's ears."""
from __future__ import annotations

import queue
import time
from dataclasses import dataclass


class TranscriptInbox:
    def __init__(self):
        self._queue = queue.Queue()

    def put(self, turn):
        self._queue.put(turn)

    def pop(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def get_nowait(self):
        return self._queue.get_nowait()


def wait_for_transcript(seconds, inbox, shutdown_event, clock=None, sleeper=None):
    """Wait in short slices so speech or shutdown can interrupt autonomy promptly."""
    clock = clock or time.time
    sleeper = sleeper or time.sleep
    deadline = clock() + max(0.0, float(seconds))
    while clock() < deadline:
        if shutdown_event.is_set():
            return None
        turn = inbox.pop()
        if turn:
            return turn
        sleeper(min(0.15, max(0.0, deadline - clock())))
    return inbox.pop()


@dataclass(frozen=True)
class CompletedUtterance:
    frames: tuple
    speech_started_at: float
    last_loud_at: float
    detected_at: float

    @property
    def duration(self):
        return self.detected_at - self.speech_started_at


class UtteranceSegmenter:
    """Turn VAD decisions into bounded utterances without owning an audio device."""

    def __init__(self, end_silence=0.8, min_speech=0.35, max_speech=15.0):
        self.end_silence = max(0.0, float(end_silence))
        self.min_speech = max(0.0, float(min_speech))
        self.max_speech = max(self.min_speech, float(max_speech))
        self.reset()

    def reset(self):
        self.frames = []
        self.speech_started_at = None
        self.last_loud_at = None

    def feed(self, frame, voiced, now):
        now = float(now)
        if voiced:
            if self.speech_started_at is None:
                self.speech_started_at = now
                self.frames = []
            self.last_loud_at = now
            self.frames.append(frame)
        elif self.speech_started_at is not None:
            self.frames.append(frame)

        if self.speech_started_at is None:
            return None
        duration = now - self.speech_started_at
        ended = self.last_loud_at is not None and now - self.last_loud_at >= self.end_silence
        if not ended and duration < self.max_speech:
            return None
        result = None
        if duration >= self.min_speech and self.frames:
            result = CompletedUtterance(
                tuple(self.frames), self.speech_started_at,
                self.last_loud_at or now, now,
            )
        self.reset()
        return result
