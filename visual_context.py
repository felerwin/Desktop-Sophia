"""Parse and normalize the semantic scene envelope returned by vision turns."""

import json
import re


SCENE_RE = re.compile(r"^\s*SCENE\s*:\s*(\{[^\r\n]*\})\s*[\r\n]+", re.IGNORECASE)


def parse_scene_envelope(raw):
    """Return ``(scene, action_text)`` while remaining compatible with old output."""
    text = str(raw or "").strip()
    match = SCENE_RE.match(text)
    if not match:
        return None, text
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None, text[match.end():].strip()
    if not isinstance(payload, dict):
        return None, text[match.end():].strip()

    scene = {}
    for key in ("game", "location", "activity", "summary", "continuity", "change"):
        value = str(payload.get(key) or "").strip()
        if value:
            scene[key] = value[:500]
    try:
        scene["confidence"] = max(0.0, min(1.0, float(payload.get("confidence"))))
    except (TypeError, ValueError):
        pass
    targets = payload.get("targets")
    if isinstance(targets, list):
        scene["targets"] = [item for item in targets[:8] if isinstance(item, dict)]
    return scene or None, text[match.end():].strip()


def scene_memory_note(scene):
    if not scene:
        return None
    parts = [scene.get("summary"), scene.get("activity"), scene.get("change")]
    return "Visual scene: " + " | ".join(dict.fromkeys(part for part in parts if part))
