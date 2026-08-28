"""Provider execution boundary for Ember's text-and-vision reasoning."""
from __future__ import annotations

import time

from .model_protocol import StreamedSpeechParser


def response_content(prompt, image_url=None):
    content = [{"type": "input_text", "text": str(prompt)}]
    if image_url:
        content.append({"type": "input_image", "image_url": str(image_url)})
    return [{"role": "user", "content": content}]


class RetrySchedule:
    def __init__(self, max_retries=2, initial_seconds=2.0):
        self.max_retries = max(0, int(max_retries))
        self.initial_seconds = max(0.0, float(initial_seconds))

    def delay_after(self, attempt):
        """Return retry delay after a zero-based attempt, or None when exhausted."""
        attempt = max(0, int(attempt))
        if attempt >= self.max_retries:
            return None
        return self.initial_seconds * (2 ** attempt)


class ModelGateway:
    """Own API calls, retries, streaming assembly, and failure accounting.

    The application supplies persistence callbacks so this service remains
    independent of Ember's dashboard and session globals.
    """

    def __init__(
        self, client, instructions, image_encoder, log_usage, record_usage,
        update_outcome, log_event, count_call=lambda: None,
        timeout_error=TimeoutError, rate_limit_error=RuntimeError,
        api_error=Exception, sleep=time.sleep, clock=time.perf_counter,
    ):
        self.client = client
        self.instructions = instructions
        self.image_encoder = image_encoder
        self.log_usage = log_usage
        self.record_usage = record_usage
        self.update_outcome = update_outcome
        self.log_event = log_event
        self.count_call = count_call
        self.timeout_error = timeout_error
        self.rate_limit_error = rate_limit_error
        self.api_error = api_error
        self.sleep = sleep
        self.clock = clock

    def _input(self, prompt, image):
        image_url = self.image_encoder(image) if image is not None else None
        return response_content(prompt, image_url)

    def call(
        self, model, prompt, image, timeout_seconds, reasoning_effort="none",
        call_type="autonomous_response", max_retries=2,
    ):
        retry = RetrySchedule(max_retries=max_retries, initial_seconds=2.0)
        for attempt in range(max_retries + 1):
            self.count_call()
            try:
                response = self.client.with_options(timeout=timeout_seconds).responses.create(
                    model=model,
                    instructions=self.instructions,
                    reasoning={"effort": reasoning_effort},
                    input=self._input(prompt, image),
                )
                usage_id = self.log_usage(response, model, call_type)
                return response.output_text, None, usage_id
            except self.timeout_error as exc:
                usage_id = self.record_usage(call_type, model, 0, "unknown")
                self.update_outcome(usage_id, "timeout", str(exc))
                return None, ("TIMEOUT", str(exc)), usage_id
            except self.rate_limit_error as exc:
                delay = retry.delay_after(attempt)
                if delay is not None:
                    self.log_event(
                        "RATE_LIMITED", attempt=attempt + 1,
                        retrying_in_seconds=delay,
                    )
                    self.sleep(delay)
                    continue
                usage_id = self.record_usage(
                    call_type, model, 0, "not_billed_rate_limit"
                )
                self.update_outcome(usage_id, "rate_limited", str(exc))
                return None, ("RATE_LIMITED", str(exc)), usage_id
            except self.api_error as exc:
                usage_id = self.record_usage(call_type, model, 0, "unknown")
                self.update_outcome(usage_id, "api_error", str(exc))
                return None, ("API_ERROR", str(exc)), usage_id
            except Exception as exc:
                usage_id = self.record_usage(call_type, model, 0, "unknown")
                self.update_outcome(usage_id, "api_error", str(exc))
                return None, ("API_ERROR", str(exc)), usage_id
        return None, ("RATE_LIMITED", "max retries exceeded"), None

    def stream(
        self, model, prompt, image, timeout_seconds, on_phrase,
        reasoning_effort="low", call_type="conversation_response",
    ):
        self.count_call()
        parser = StreamedSpeechParser(on_phrase)
        first_delta_at = None
        completed_response = None
        failed_response = None
        try:
            stream = self.client.with_options(timeout=timeout_seconds).responses.create(
                model=model,
                instructions=self.instructions,
                reasoning={"effort": reasoning_effort},
                input=self._input(prompt, image),
                stream=True,
            )
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        if first_delta_at is None:
                            first_delta_at = self.clock()
                        parser.feed(delta)
                elif event_type == "response.completed":
                    completed_response = getattr(event, "response", None)
                elif event_type in {"response.failed", "error"}:
                    failed_response = getattr(event, "response", None)
                    raise RuntimeError(str(event))

            parser.finish()
            if completed_response is not None:
                usage_id = self.log_usage(completed_response, model, call_type)
            else:
                usage_id = self.record_usage(call_type, model, 0, "unknown")
                self.update_outcome(usage_id, "incomplete_stream")
            return parser.raw, None, first_delta_at, usage_id
        except self.timeout_error as exc:
            usage_id = self.record_usage(call_type, model, 0, "unknown")
            self.update_outcome(usage_id, "timeout", str(exc))
            return None, ("TIMEOUT", str(exc)), first_delta_at, usage_id
        except self.rate_limit_error as exc:
            usage_id = self.record_usage(
                call_type, model, 0, "not_billed_rate_limit"
            )
            self.update_outcome(usage_id, "rate_limited", str(exc))
            return None, ("RATE_LIMITED", str(exc)), first_delta_at, usage_id
        except self.api_error as exc:
            usage_id = self.record_usage(call_type, model, 0, "unknown")
            self.update_outcome(usage_id, "api_error", str(exc))
            return None, ("API_ERROR", str(exc)), first_delta_at, usage_id
        except Exception as exc:
            usage_id = (
                self.log_usage(failed_response, model, call_type)
                if failed_response is not None else
                self.record_usage(call_type, model, 0, "unknown")
            )
            self.update_outcome(usage_id, "api_error", str(exc))
            return None, ("API_ERROR", str(exc)), first_delta_at, usage_id
