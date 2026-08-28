"""Normalize local and provider transcription responses into one contract."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    average_logprob: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    audio_seconds: float = 0.0


def normalize_local_transcription(segments, audio_seconds=0.0):
    segments = list(segments)
    text = " ".join(str(segment.text).strip() for segment in segments).strip()
    probabilities = [
        float(segment.avg_logprob) for segment in segments
        if getattr(segment, "avg_logprob", None) is not None
    ]
    return TranscriptionResult(
        text=text,
        average_logprob=(sum(probabilities) / len(probabilities) if probabilities else None),
        audio_seconds=max(0.0, float(audio_seconds)),
    )


def normalize_provider_transcription(response, fallback_audio_seconds=0.0):
    usage = getattr(response, "usage", None)
    logprobs = [
        float(item.logprob) for item in (getattr(response, "logprobs", None) or [])
        if getattr(item, "logprob", None) is not None
    ]
    return TranscriptionResult(
        text=str(getattr(response, "text", "") or "").strip(),
        average_logprob=(sum(logprobs) / len(logprobs) if logprobs else None),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        audio_seconds=float(getattr(usage, "seconds", 0) or fallback_audio_seconds),
    )
