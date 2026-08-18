"""Centralized API cost estimates. Rates are USD and intentionally explicit."""


LUNA_INPUT_PER_MILLION = 0.20
LUNA_CACHED_INPUT_PER_MILLION = 0.02
LUNA_OUTPUT_PER_MILLION = 1.20

TRANSCRIPTION_PER_MINUTE = {
    "gpt-transcribe": 0.0045,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
}


def response_cost(model, input_tokens, output_tokens, cached_input_tokens=0):
    if model != "gpt-5.6-luna":
        return None
    cached = max(0, min(int(input_tokens or 0), int(cached_input_tokens or 0)))
    uncached = max(0, int(input_tokens or 0) - cached)
    return (
        uncached * LUNA_INPUT_PER_MILLION
        + cached * LUNA_CACHED_INPUT_PER_MILLION
        + int(output_tokens or 0) * LUNA_OUTPUT_PER_MILLION
    ) / 1_000_000


def transcription_cost(model, audio_seconds):
    rate = TRANSCRIPTION_PER_MINUTE.get(str(model or ""))
    if rate is None:
        return None
    return max(0.0, float(audio_seconds or 0)) / 60 * rate


def budget_decision(governed_cost, warning, ceiling, enabled=True, override=False):
    governed_cost = max(0.0, float(governed_cost or 0))
    warning = max(0.0, float(warning or 0))
    ceiling = max(warning, float(ceiling or 0))
    enabled = bool(enabled)
    return {
        "warning": enabled and governed_cost >= warning,
        "paused": enabled and not bool(override) and governed_cost >= ceiling,
    }
