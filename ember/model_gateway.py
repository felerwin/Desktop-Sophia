"""Pure request and retry policy for Ember's provider adapters."""
from __future__ import annotations


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
