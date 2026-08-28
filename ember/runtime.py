"""Thread-safe runtime persistence and session state for Ember."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Callable


class AtomicJsonStore:
    """Serialize JSON updates and retain the last parseable generation."""

    def __init__(self, path: str | Path, default_factory: Callable[[], Any]):
        self.path = Path(path)
        self.backup_path = self.path.with_suffix(self.path.suffix + ".bak")
        self.default_factory = default_factory
        self.lock = threading.RLock()

    def load(self, on_error: Callable[[Exception], None] | None = None):
        with self.lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self.default_factory()
            except Exception as exc:
                if on_error:
                    on_error(exc)
                try:
                    return json.loads(self.backup_path.read_text(encoding="utf-8"))
                except Exception:
                    return self.default_factory()

    def save(self, value) -> None:
        with self.lock:
            if self.path.is_file():
                try:
                    json.loads(self.path.read_text(encoding="utf-8"))
                    shutil.copy2(self.path, self.backup_path)
                except Exception:
                    pass
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)


@dataclass
class SessionUsage:
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    governed_cost: float = 0.0
    budget_override: bool = False
    warning_emitted: bool = False
    pause_emitted: bool = False

    def record(self, *, input_tokens=0, output_tokens=0, cost=0.0, multiplier=1.0):
        self.api_calls += 1
        self.input_tokens += max(0, int(input_tokens or 0))
        self.output_tokens += max(0, int(output_tokens or 0))
        self.estimated_cost += max(0.0, float(cost or 0.0))
        self.governed_cost += max(0.0, float(cost or 0.0)) * max(1.0, float(multiplier or 1.0))

    def snapshot(self):
        return asdict(self)


def default_companion_memory():
    return {"recent_observations": [], "recent_utterances": []}

