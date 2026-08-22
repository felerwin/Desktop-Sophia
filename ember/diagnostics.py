"""Offline-friendly behavioral diagnostics for Ember v0.2.

The Blood Elf test is intentionally behavioral: perception should infer an activity,
not merely identify the game. The API-facing runner can feed its visual observation
through evaluate_blood_elf_observation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiagnosticResult:
    passed: bool
    score: int
    notes: list[str]


def evaluate_blood_elf_observation(observation: dict) -> DiagnosticResult:
    text = " ".join(str(observation.get(k) or "") for k in ("game", "location", "activity", "summary")).lower()
    score = 0
    notes = []

    if "world of warcraft" in text or "wow" in text:
        score += 1
    else:
        notes.append("Did not identify World of Warcraft.")

    if any(term in text for term in ("blood elf", "eversong", "sunstrider", "silvermoon")):
        score += 2
    else:
        notes.append("Did not infer Blood Elf starting-area context.")

    if any(term in text for term in ("quest", "questing", "starting zone", "starter", "chain", "objective")):
        score += 2
    else:
        notes.append("Identified scenery but not the player's likely activity.")

    return DiagnosticResult(passed=score >= 4, score=score, notes=notes)


def evaluate_semantic_telemetry(snapshot: dict) -> DiagnosticResult:
    notes = []
    score = 0
    if snapshot.get("game"):
        score += 1
    if snapshot.get("location"):
        score += 1
    if snapshot.get("live"):
        score += 1
    if snapshot.get("recent_events"):
        score += 2
    else:
        notes.append("Telemetry reached the world state but produced no semantic events.")
    return DiagnosticResult(passed=score >= 3, score=score, notes=notes)
