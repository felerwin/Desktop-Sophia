"""Centralized API cost estimates. Rates are USD and intentionally explicit."""


LUNA_INPUT_PER_MILLION = 0.20
LUNA_CACHED_INPUT_PER_MILLION = 0.02
LUNA_OUTPUT_PER_MILLION = 1.20

TERRA_INPUT_PER_MILLION = 2.00
TERRA_CACHED_INPUT_PER_MILLION = 0.20
TERRA_OUTPUT_PER_MILLION = 12.00

MODEL_TOKEN_RATES = {
    "gpt-5.6-luna": (
        LUNA_INPUT_PER_MILLION,
        LUNA_CACHED_INPUT_PER_MILLION,
        LUNA_OUTPUT_PER_MILLION,
    ),
    "gpt-5.6-terra": (
        TERRA_INPUT_PER_MILLION,
        TERRA_CACHED_INPUT_PER_MILLION,
        TERRA_OUTPUT_PER_MILLION,
    ),
}

TRANSCRIPTION_PER_MINUTE = {
    "gpt-transcribe": 0.0045,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
}


def response_cost(model, input_tokens, output_tokens, cached_input_tokens=0):
    rates = MODEL_TOKEN_RATES.get(str(model or ""))
    if rates is None:
        return None
    input_rate, cached_rate, output_rate = rates
    cached = max(0, min(int(input_tokens or 0), int(cached_input_tokens or 0)))
    uncached = max(0, int(input_tokens or 0) - cached)
    return (
        uncached * input_rate
        + cached * cached_rate
        + int(output_tokens or 0) * output_rate
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
