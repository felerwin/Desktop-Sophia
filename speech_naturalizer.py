"""Small deterministic prosody helpers for local speech synthesis."""
from __future__ import annotations

import re


def normalize_spoken_text(text: str) -> str:
    """Clean model text without flattening its expressive punctuation."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"!{4,}", "!!!", text)
    text = re.sub(r"\?{4,}", "???", text)
    return text
