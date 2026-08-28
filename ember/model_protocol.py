"""Provider-neutral response protocol for Ember's model boundary."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable


ACTION_RE = re.compile(
    r"^\s*(SAY|SILENT|VIDEO|POINT)\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True)
class ModelAction:
    kind: str
    content: str


def parse_model_action(text: str) -> ModelAction | None:
    match = ACTION_RE.fullmatch(str(text or "").strip())
    if not match:
        return None
    return ModelAction(match.group(1).upper(), match.group(2).strip())


class StreamedSpeechParser:
    """Release complete SAY phrases while retaining the provider's raw reply."""

    def __init__(self, on_phrase: Callable[[str, int], None]):
        self.on_phrase = on_phrase
        self.raw = ""
        self.kind = None
        self.buffer = ""
        self.phrase_index = 0

    def feed(self, delta):
        self.raw += str(delta or "")
        if self.kind is None:
            match = re.match(r"^\s*(SAY|SILENT|VIDEO|POINT)\s*:\s*", self.raw, re.IGNORECASE)
            if not match:
                return
            self.kind = match.group(1).upper()
            if self.kind == "SAY":
                self.buffer = self.raw[match.end():]
        elif self.kind == "SAY":
            self.buffer += str(delta or "")
        if self.kind == "SAY":
            self._release_ready_phrases()

    def _release_ready_phrases(self):
        while self.buffer:
            boundary = None
            for match in re.finditer(r"[.!?][\"'”’]?|[,;:—]", self.buffer):
                candidate = self.buffer[:match.end()].strip()
                strong = match.group(0)[0] in ".!?"
                if (strong and len(candidate) >= 12) or len(candidate) >= 45:
                    boundary = match.end()
                    break
            if boundary is None:
                return
            self._emit(self.buffer[:boundary])
            self.buffer = self.buffer[boundary:].lstrip()

    def finish(self):
        if self.kind == "SAY" and self.buffer.strip():
            self._emit(self.buffer)
            self.buffer = ""

    def _emit(self, text):
        text = text.strip()
        if text:
            self.phrase_index += 1
            self.on_phrase(text, self.phrase_index)

