"""Small testable helpers shared by Ember's TTS process boundary."""
from __future__ import annotations

import re
import json


_PROGRESS_RE = re.compile(r"\d+%\|.*\|")


def should_log_worker_stderr(line: str) -> bool:
    """Drain every line but retain only useful Chatterbox diagnostics."""
    text = str(line or "").strip()
    return bool(text and not _PROGRESS_RE.search(text))


def worker_command(kind: str, **fields) -> dict:
    """Build the tiny JSON command vocabulary used by the persistent worker."""
    command = {"cmd": str(kind)}
    command.update(fields)
    return command


def parse_worker_event(line: str):
    """Decode one worker line; return None for diagnostics/non-JSON output."""
    text = str(line or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and value.get("event") else None
