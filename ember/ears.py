"""Device-free transcript queue and wake-up behavior for Ember's ears."""
from __future__ import annotations

import queue
import time


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
