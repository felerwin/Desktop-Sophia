"""Thread-safe speech lifecycle queue, separate from the Chatterbox process."""
from __future__ import annotations

import queue

from .performance import SpeechPerformance


class SpeechQueue:
    STOP = object()

    def __init__(self):
        self._queue = queue.Queue()

    def put(self, item):
        self._queue.put(item)

    def get(self):
        return self._queue.get()

    def empty(self):
        return self._queue.empty()

    def stop(self):
        self._queue.put(self.STOP)

    def drain_performances(self):
        """Discard pending speech but preserve shutdown and control commands."""
        performances = []
        preserved = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, SpeechPerformance):
                performances.append(item)
            else:
                preserved.append(item)
        for item in preserved:
            self._queue.put(item)
        return performances
