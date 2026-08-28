"""Small deterministic prosody helpers for local speech synthesis."""
from __future__ import annotations

import re


def normalize_spoken_text(text: str, max_sentences: int = 2, max_chars: int = 320) -> str:
    """Keep spoken output compact and expressive without punctuation spam."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\?{2,}", "?", text)
    boundaries = list(re.finditer(r"[.!?](?:[\"'”’])?(?:\s+|$)", text))
    if len(boundaries) > max_sentences:
        text = text[:boundaries[max_sentences - 1].end()].strip()
    if len(text) > max_chars:
        shortened = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-")
        text = shortened + ("." if shortened and shortened[-1] not in ".!?" else "")
    return text
