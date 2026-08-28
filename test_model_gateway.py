import unittest
from types import SimpleNamespace

from ember import ModelGateway, RetrySchedule, response_content


class TimeoutFailure(Exception): pass
class RateFailure(Exception): pass
class ApiFailure(Exception): pass


class FakeResponses:
    def __init__(self, results): self.results = iter(results)
    def create(self, **kwargs):
        result = next(self.results)
        if isinstance(result, Exception): raise result
        return result


class FakeClient:
    def __init__(self, results): self.responses = FakeResponses(results)
    def with_options(self, **kwargs): return self


class ModelGatewayTests(unittest.TestCase):
    def test_request_image_is_optional(self):
        text_only = response_content("hello")
        with_image = response_content("look", "data:image/jpeg;base64,abc")
        self.assertEqual(len(text_only[0]["content"]), 1)
        self.assertEqual(with_image[0]["content"][1]["type"], "input_image")

    def test_retry_schedule_is_bounded_exponential(self):
        retry = RetrySchedule(2, 2)
        self.assertEqual(retry.delay_after(0), 2)
        self.assertEqual(retry.delay_after(1), 4)
        self.assertIsNone(retry.delay_after(2))

    def gateway(self, results, events, calls, sleeps):
        return ModelGateway(
            FakeClient(results), "instructions", lambda image: "image-url",
            lambda response, model, kind: "usage-1",
            lambda *args, **kwargs: "usage-error",
            lambda *args: events.append(("outcome", args)),
            lambda *args, **kwargs: events.append((args, kwargs)),
            count_call=lambda: calls.append(1),
            timeout_error=TimeoutFailure, rate_limit_error=RateFailure,
            api_error=ApiFailure, sleep=sleeps.append,
        )

    def test_call_retries_rate_limit_then_returns_response(self):
        events, calls, sleeps = [], [], []
        response = SimpleNamespace(output_text="SAY: hello")
        result = self.gateway(
            [RateFailure("busy"), response], events, calls, sleeps
        ).call("model", "prompt", None, 10)
        self.assertEqual(result, ("SAY: hello", None, "usage-1"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [2.0])

    def test_stream_emits_complete_phrases(self):
        events, calls, sleeps, phrases = [], [], [], []
        stream = [
            SimpleNamespace(type="response.output_text.delta", delta="SAY: Hello there."),
            SimpleNamespace(type="response.completed", response=SimpleNamespace()),
        ]
        result = self.gateway([stream], events, calls, sleeps).stream(
            "model", "prompt", None, 10,
            lambda phrase, index: phrases.append((phrase, index)),
        )
        self.assertIsNone(result[1])
        self.assertEqual(phrases, [("Hello there.", 1)])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
