import re


def transcript_rejection_reason(
    text, average_logprob=None, voiced_seconds=None,
    minimum_logprob=-0.7, short_fragment_seconds=0.45,
):
    """Reject obvious ambient/NPC fragments before they become conversation."""
    text = str(text or "").strip()
    if not text:
        return "empty"
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text):
        return "non_english_script"
    if average_logprob is not None and average_logprob < float(minimum_logprob):
        return "low_confidence"
    words = re.findall(r"[A-Za-z0-9']+", text)
    if (
        voiced_seconds is not None
        and len(words) <= 2
        and float(voiced_seconds) < float(short_fragment_seconds)
    ):
        return "short_ambient_fragment"
    return None
