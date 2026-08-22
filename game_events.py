import csv
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path

from wow_pixel_bridge import WowPixelBridge


class GameEventEngine:
    """Tails local game logs and emits a small, normalized event vocabulary."""

    REACTION_TYPES = {
        "boss_start", "boss_victory", "boss_wipe", "player_death", "level_up",
        "quest_complete", "critical_health", "danger_recovered", "valuable_loot",
        "gear_upgrade", "hard_fought_victory", "zone_change", "activity_change",
    }

    SOURCE_METADATA = {
        "wow_pixel_bridge": ("telemetry", 0.98),
        "wow_combat_log": ("combat_log", 0.99),
        "wow_log": ("game_log", 0.98),
        "dashboard": ("manual_test", 1.0),
    }

    def __init__(self, root, config, on_event=None, on_status=None):
        self.root = Path(root)
        self.config = config
        self.on_event = on_event
        self.on_status = on_status
        self.events = deque(maxlen=80)
        self.reaction_queue = queue.Queue(maxsize=30)
        self.stop_event = threading.Event()
        self.thread = None
        self.log_path = None
        self.status = "searching"
        self.last_error = None
        self._recent_signatures = {}
        self._recent_event_types = {}
        self.active_fight = None
        self.live_state = {}
        self._known_gear = {}
        self._last_low_health_at = 0
        self._critical_active = False
        self._pixel_combat = None
        self._fight_history = deque(maxlen=20)
        self.activity = {
            "name": "unknown", "previous": None, "changed_at": time.time(),
            "source": "wow_pixel_bridge", "confidence": 0.98,
        }
        self._activity_candidate = None
        self._activity_candidate_count = 0
        self._activity_last_emit_at = 0
        self.pixel_bridge = WowPixelBridge(
            config, self._handle_pixel_packet, self._handle_pixel_status
        )

    def _candidate_paths(self):
        configured = str(self.config.get("wow_combat_log_path") or "").strip()
        if configured:
            yield Path(configured)
        bases = [Path("C:/Program Files (x86)/World of Warcraft"), Path("C:/Program Files/World of Warcraft")]
        bases.extend([
            Path.home() / "Desktop" / "ChromieCraft_3.3.5a",
            Path.home() / "Desktop" / "world of warcraft 3.3.5a hd",
        ])
        flavors = ["_classic_", "_classic_era_", "_retail_", "_ptr_"]
        for base in bases:
            for flavor in flavors:
                yield base / flavor / "Logs" / "WoWCombatLog.txt"

    def discover(self):
        for candidate in self._candidate_paths():
            try:
                if candidate.is_file():
                    self.log_path = candidate
                    return candidate
            except OSError:
                continue
        return None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="sophia-game-events", daemon=True)
        self.thread.start()
        if self.config.get("wow_pixel_bridge", True):
            self.pixel_bridge.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.pixel_bridge.stop()

    def snapshot(self):
        return {
            "status": self.status,
            "log_path": str(self.log_path) if self.log_path else None,
            "configured_path": str(self.config.get("wow_combat_log_path") or ""),
            "player_name": str(self.config.get("wow_player_name") or ""),
            "last_error": self.last_error,
            "recent": list(self.events)[:30],
            "active_fight": self._fight_context(),
            "temporal_combat": self._pixel_combat_context(),
            "activity": self._activity_context(),
            "telemetry": self.pixel_bridge.snapshot(),
        }

    def context(self):
        telemetry = self.pixel_bridge.snapshot()
        telemetry_live = telemetry.get("status") == "live"
        return {
            "status": self.status,
            "player": str(self.config.get("wow_player_name") or ""),
            "telemetry_available": telemetry_live,
            "telemetry_status": telemetry.get("status"),
            "telemetry_warning": (
                None if telemetry_live else
                "The pixel bridge is not live. Do not attribute screenshot observations "
                "or combat-log details to the pixel grid/addon."
            ),
            "active_fight": self._fight_context(),
            "temporal_combat": self._pixel_combat_context() if telemetry_live else None,
            "activity": self._activity_context() if telemetry_live else {
                "name": "unknown", "source": "unavailable", "confidence": 0,
            },
            "live_state": dict(self.live_state) if telemetry_live else {},
            "equipped_gear": [
                {key: item.get(key) for key in ("slot", "item_id", "quality", "item_level", "name")}
                for item in telemetry.get("gear", [])
                if item.get("item_id")
            ] if telemetry_live else [],
            "recent_events": list(self.events)[:8],
            "evidence_guide": {
                "telemetry": "Exact values decoded from the visible, checksummed WoW addon signal.",
                "telemetry_derived": "A high-confidence conclusion formed from several exact telemetry states over time.",
                "combat_log": "Exact event parsed from WoW's local combat log.",
                "visual_inference": "A screenshot interpretation; describe it as an inference, not telemetry.",
            },
        }

    def semantic_context(self):
        """Return compact, evidence-aware input for Ember's WorldState."""
        context = self.context()
        return {
            "game": "World of Warcraft",
            "live_state": context["live_state"],
            "recent_events": list(self.events)[:12],
            "active_fight": context["active_fight"],
            "temporal_combat": context["temporal_combat"],
            "activity": context["activity"],
            "telemetry_available": context["telemetry_available"],
        }

    def _handle_pixel_status(self, status):
        if self.on_status:
            self.on_status(dict(status))

    def _handle_pixel_packet(self, packet):
        kind = packet.get("kind")
        if kind == "state":
            previous = dict(self.live_state)
            previous_health = int(previous.get("health", 100))
            self.live_state.update(packet)
            health = int(packet.get("health", 100))
            now = time.time()
            self._observe_activity(packet, now)
            self._observe_pixel_combat(previous, packet, now)
            if packet.get("dead") and not previous.get("dead"):
                self._critical_active = False
                self._finish_pixel_combat(now, forced_outcome="player_death")
                self.inject(
                    "player_death", "Player death detected",
                    {"health_percent": health, "target": self.live_state.get("target_name")},
                    "wow_pixel_bridge",
                )
            if (
                packet.get("combat") and health <= 25 and previous_health > 25
                and now - self._last_low_health_at >= 45
            ):
                self._last_low_health_at = now
                self._critical_active = True
                self.inject(
                    "critical_health", f"Health dropped to {health}%",
                    {"health_percent": health, "target": self.live_state.get("target_name")},
                    "wow_pixel_bridge",
                )
            elif self._critical_active and health >= 55 and not packet.get("dead"):
                self._critical_active = False
                self.inject(
                    "danger_recovered", f"Recovered to {health}% health",
                    {"health_percent": health, "target": self.live_state.get("target_name")},
                    "wow_pixel_bridge",
                )
            return
        if kind == "gear":
            slot = int(packet.get("slot", 0))
            item_id = int(packet.get("item_id", 0))
            current = {
                key: packet.get(key)
                for key in ("slot", "item_id", "quality", "item_level", "name")
            }
            previous = self._known_gear.get(slot)
            self._known_gear[slot] = current
            if previous is not None and int(previous.get("item_id") or 0) != item_id:
                previous_level = int(previous.get("item_level") or 0)
                current_level = int(packet.get("item_level") or 0)
                upgraded = item_id > 0 and current_level > previous_level
                self.inject(
                    "gear_upgrade" if upgraded else "gear_change",
                    (
                        f"Equipped upgrade: {packet.get('name') or 'new item'}"
                        if upgraded else f"Equipment changed: {packet.get('name') or 'empty slot'}"
                    ),
                    {
                        **current,
                        "previous": previous,
                        "item_level_delta": current_level - previous_level,
                    },
                    "wow_pixel_bridge",
                )
            return
        if kind == "loot":
            event_type = "valuable_loot" if int(packet.get("quality", 0)) >= 3 else "loot_pickup"
            self.inject(
                event_type, f"Looted {packet.get('name') or 'an item'}",
                {key: packet.get(key) for key in ("item_id", "count", "quality", "item_level", "name")},
                "wow_pixel_bridge",
            )
            return
        if kind == "zone":
            previous_zone = self.live_state.get("zone")
            self.live_state.update(packet)
            if previous_zone and packet.get("zone") != previous_zone:
                self.inject(
                    "zone_change", f"Entered {packet.get('zone')}", dict(packet), "wow_pixel_bridge"
                )
            return
        if kind == "target":
            self.live_state.update({
                "target_name": packet.get("name"), "target_level": packet.get("level"),
                "target_classification": packet.get("classification"),
            })
            if self._pixel_combat is not None and packet.get("name"):
                self._pixel_combat["target"] = packet.get("name")
                self._pixel_combat["target_level"] = packet.get("level")
            if int(packet.get("classification", 0)) == 4 and packet.get("name"):
                self.inject(
                    "boss_start", f"Targeted world boss: {packet.get('name')}",
                    dict(packet), "wow_pixel_bridge",
                )

    @staticmethod
    def _derived_activity(state):
        if state.get("dead"):
            return "dead"
        if state.get("combat"):
            return "combat"
        if state.get("mounted"):
            return "travel"
        if state.get("resting"):
            return "resting"
        if state.get("hostile_target") and state.get("has_target"):
            return "hunting"
        return "exploration"

    def _observe_activity(self, state, now):
        candidate = self._derived_activity(state)
        if candidate != self._activity_candidate:
            self._activity_candidate = candidate
            self._activity_candidate_count = 1
            return
        self._activity_candidate_count += 1
        if self._activity_candidate_count < 3 or candidate == self.activity["name"]:
            return
        previous = self.activity["name"]
        self.activity = {
            "name": candidate, "previous": previous, "changed_at": now,
            "source": "wow_pixel_bridge", "confidence": 0.98,
        }
        gap = float(self.config.get("game_activity_reaction_gap_seconds", 90))
        reaction_phases = {"travel", "resting", "hunting", "exploration"}
        if (
            previous != "unknown" and candidate in reaction_phases
            and now - self._activity_last_emit_at >= gap
        ):
            self._activity_last_emit_at = now
            self.inject(
                "activity_change", f"Activity changed from {previous} to {candidate}",
                {"previous": previous, "activity": candidate}, "wow_pixel_bridge",
                evidence="telemetry_derived", confidence=0.92,
            )

    def _activity_context(self):
        activity = dict(self.activity)
        activity["seconds"] = round(max(0, time.time() - activity.pop("changed_at")), 1)
        return activity

    def _observe_pixel_combat(self, previous, current, now):
        was_in_combat = bool(previous.get("combat"))
        in_combat = bool(current.get("combat"))
        if in_combat and not was_in_combat:
            target = self.live_state.get("target_name")
            self._pixel_combat = {
                "started_at": now,
                "starting_health": int(current.get("health", 100)),
                "lowest_health": int(current.get("health", 100)),
                "starting_target_health": int(current.get("target_health", 100)),
                "lowest_target_health": int(current.get("target_health", 100)),
                "target_zero_seen": int(current.get("target_health", 100)) <= 0,
                "target": self.live_state.get("target_name"),
                "target_level": self.live_state.get("target_level"),
            }
            self.inject(
                "combat_start",
                f"Combat started with {target}" if target else "Combat started",
                {"target": target, "health_percent": current.get("health")},
                "wow_pixel_bridge",
                evidence="telemetry_derived", confidence=0.96,
            )
        if in_combat and self._pixel_combat is not None:
            health = int(current.get("health", 100))
            target_health = int(current.get("target_health", 100))
            self._pixel_combat["lowest_health"] = min(self._pixel_combat["lowest_health"], health)
            self._pixel_combat["lowest_target_health"] = min(
                self._pixel_combat["lowest_target_health"], target_health
            )
            self._pixel_combat["target_zero_seen"] |= target_health <= 0
            if self.live_state.get("target_name"):
                self._pixel_combat["target"] = self.live_state.get("target_name")
        if was_in_combat and not in_combat:
            self._finish_pixel_combat(now)

    def _pixel_combat_context(self):
        if self._pixel_combat is None:
            return None
        fight = self._pixel_combat
        return {
            "seconds": round(max(0, time.time() - fight["started_at"]), 1),
            "target": fight.get("target"),
            "target_level": fight.get("target_level"),
            "starting_health": fight["starting_health"],
            "lowest_health": fight["lowest_health"],
            "target_health": fight["lowest_target_health"],
            "source": "wow_pixel_bridge",
            "confidence": 0.98,
        }

    def _finish_pixel_combat(self, now, forced_outcome=None):
        fight = self._pixel_combat
        if fight is None:
            return
        self._pixel_combat = None
        duration = round(max(0.1, now - fight["started_at"]), 1)
        outcome = forced_outcome or ("victory" if fight["target_zero_seen"] else "disengaged")
        details = {
            "duration_seconds": duration,
            "target": fight.get("target"),
            "target_level": fight.get("target_level"),
            "starting_health": fight["starting_health"],
            "lowest_health": fight["lowest_health"],
            "starting_target_health": fight["starting_target_health"],
            "lowest_target_health": fight["lowest_target_health"],
            "outcome": outcome,
        }
        self._fight_history.appendleft({**details, "ended_at": now})
        self.inject(
            "combat_end",
            f"Combat ended against {fight.get('target') or 'the current target'}",
            details,
            "wow_pixel_bridge",
            evidence="telemetry_derived", confidence=0.95,
        )
        hard_fought = (
            outcome == "victory"
            and (duration >= 25 or fight["lowest_health"] <= 30)
        )
        if hard_fought:
            self.inject(
                "hard_fought_victory",
                f"Hard-fought victory over {fight.get('target') or 'the target'}",
                details,
                "wow_pixel_bridge",
                evidence="telemetry_derived", confidence=0.9,
            )

    def _fight_context(self):
        if not self.active_fight:
            return None
        fight = self.active_fight
        return {
            "seconds": round(time.time() - fight["started_at"], 1),
            "targets": sorted(fight["targets"])[:6],
            "damage_done": fight["damage_done"],
            "damage_taken": fight["damage_taken"],
            "kills": fight["kills"],
        }

    def _touch_fight(self, target=None):
        now = time.time()
        if self.active_fight is None:
            self.active_fight = {
                "started_at": now, "last_at": now, "targets": set(),
                "damage_done": 0, "damage_taken": 0, "kills": 0,
            }
        self.active_fight["last_at"] = now
        if target:
            self.active_fight["targets"].add(target)

    def _finish_fight_if_idle(self, force=False):
        fight = self.active_fight
        if not fight or (not force and time.time() - fight["last_at"] < 6):
            return
        duration = max(0.1, fight["last_at"] - fight["started_at"])
        targets = sorted(fight["targets"])
        self.active_fight = None
        self.inject(
            "combat_summary",
            f"Fight ended: {', '.join(targets[:3]) or 'unknown target'}",
            {
                "duration_seconds": round(duration, 1), "targets": targets[:8],
                "damage_done": fight["damage_done"], "damage_taken": fight["damage_taken"],
                "kills": fight["kills"],
            },
            "wow_combat_log",
        )

    def pop_reaction(self):
        try:
            return self.reaction_queue.get_nowait()
        except queue.Empty:
            return None

    def inject(
        self, event_type, title=None, details=None, source="dashboard",
        evidence=None, confidence=None,
    ):
        labels = {
            "boss_start": "Boss encounter started",
            "boss_victory": "Boss defeated",
            "boss_wipe": "Boss attempt failed",
            "player_death": "Player died",
            "level_up": "Level gained",
            "quest_complete": "Quest completed",
            "zone_change": "Zone changed",
        }
        priority = "high" if event_type in self.REACTION_TYPES else "normal"
        default_evidence, default_confidence = self.SOURCE_METADATA.get(
            source, ("local_signal", 0.9)
        )
        event = {
            "event_type": event_type,
            "game": "World of Warcraft",
            "title": title or labels.get(event_type, event_type.replace("_", " ").title()),
            "details": details or {},
            "priority": priority,
            "source": source,
            "evidence": evidence or default_evidence,
            "confidence": default_confidence if confidence is None else float(confidence),
            "time": time.strftime("%H:%M:%S"),
        }
        self._emit(event)
        return event

    def _emit(self, event):
        signature = f"{event['event_type']}:{event['title']}"
        now = time.time()
        if now - self._recent_signatures.get(signature, 0) < 12:
            return
        semantic_windows = {
            "boss_start": 20,
            "boss_victory": 12,
            "boss_wipe": 12,
            "player_death": 12,
            "critical_health": 30,
            "danger_recovered": 30,
        }
        semantic_window = semantic_windows.get(event["event_type"], 0)
        if now - self._recent_event_types.get(event["event_type"], 0) < semantic_window:
            return
        self._recent_signatures[signature] = now
        self._recent_event_types[event["event_type"]] = now
        self.events.appendleft(event)
        if event["event_type"] in self.REACTION_TYPES:
            try:
                self.reaction_queue.put_nowait(event)
            except queue.Full:
                pass
        if self.on_event:
            self.on_event(event)

    @staticmethod
    def _event_payload(line):
        match = re.search(
            r"\b(ENCOUNTER_START|ENCOUNTER_END|UNIT_DIED|PARTY_KILL|PLAYER_LEVEL_UP|QUEST_TURNED_IN)\b",
            line,
        )
        if not match:
            return None, []
        payload = line[match.start():]
        try:
            parts = next(csv.reader([payload]))
        except Exception:
            parts = payload.split(",")
        return match.group(1), [part.strip().strip('"') for part in parts]

    def parse_line(self, line):
        payload_match = re.search(r"\s{2}([A-Z][A-Z_]+),", line)
        if not payload_match:
            return None
        payload = line[payload_match.start(1):]
        try:
            parts = [part.strip().strip('"') for part in next(csv.reader([payload]))]
        except Exception:
            parts = [part.strip().strip('"') for part in payload.split(",")]
        kind = parts[0] if parts else None
        if not kind:
            return None
        if kind == "ENCOUNTER_START":
            name = parts[2] if len(parts) > 2 else "Boss"
            return self.inject("boss_start", f"Encounter started: {name}", {"encounter": name}, "wow_combat_log")
        if kind == "ENCOUNTER_END":
            name = parts[2] if len(parts) > 2 else "Boss"
            success = parts[-1] == "1" if parts else False
            return self.inject(
                "boss_victory" if success else "boss_wipe",
                f"{name} defeated" if success else f"Attempt on {name} ended",
                {"encounter": name, "success": success},
                "wow_combat_log",
            )
        if kind == "UNIT_DIED":
            configured_player = str(self.config.get("wow_player_name") or "").strip().lower()
            name = next(
                (part for part in parts[1:] if configured_player and configured_player in part.lower()),
                None,
            )
            if name:
                self._finish_fight_if_idle(force=True)
                return self.inject("player_death", f"{name} died", {"name": name}, "wow_combat_log")
            return None
        configured_player = str(self.config.get("wow_player_name") or "").strip().lower()
        source_name = parts[2] if len(parts) > 2 else ""
        dest_name = parts[5] if len(parts) > 5 else ""
        player_source = configured_player and source_name.lower() == configured_player
        player_dest = configured_player and dest_name.lower() == configured_player
        if kind == "PARTY_KILL" and player_source:
            self._touch_fight(dest_name)
            self.active_fight["kills"] += 1
            return self.inject(
                "enemy_kill", f"Defeated {dest_name}", {"target": dest_name}, "wow_combat_log"
            )
        if kind.endswith("_DAMAGE") and (player_source or player_dest):
            amount_index = 7 if kind == "SWING_DAMAGE" else 10
            try:
                amount = int(float(parts[amount_index]))
            except (IndexError, TypeError, ValueError):
                amount = 0
            target = dest_name if player_source else source_name
            self._touch_fight(target)
            if player_source:
                self.active_fight["damage_done"] += amount
            if player_dest:
                self.active_fight["damage_taken"] += amount
            return None
        if kind == "SPELL_INTERRUPT" and player_source:
            interrupted = parts[11] if len(parts) > 11 else "spell"
            self._touch_fight(dest_name)
            return self.inject(
                "interrupt", f"Interrupted {interrupted}",
                {"target": dest_name, "spell": interrupted}, "wow_combat_log",
            )
        if kind == "PLAYER_LEVEL_UP":
            level = parts[1] if len(parts) > 1 else ""
            return self.inject("level_up", f"Level {level} reached".strip(), {"level": level}, "wow_log")
        if kind == "QUEST_TURNED_IN":
            quest = parts[2] if len(parts) > 2 else "Quest"
            return self.inject("quest_complete", f"Quest completed: {quest}", {"quest": quest}, "wow_log")
        return None

    def _run(self):
        while not self.stop_event.is_set():
            path = self.discover()
            if path is None:
                self.status = "waiting_for_log"
                self.stop_event.wait(10)
                continue
            try:
                self.status = "watching"
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(0, 2)
                    while not self.stop_event.is_set():
                        line = handle.readline()
                        if not line:
                            # WoW truncates its combat log when a new logging
                            # session begins. A reader left beyond the new EOF
                            # otherwise waits forever and misses the whole run.
                            if self._rewind_if_truncated(handle, path):
                                continue
                            self._finish_fight_if_idle()
                            self.stop_event.wait(0.5)
                            continue
                        self.parse_line(line)
            except Exception as exc:
                self.status = "error"
                self.last_error = str(exc)[:300]
                self.stop_event.wait(5)

    @staticmethod
    def _rewind_if_truncated(handle, path):
        try:
            if path.stat().st_size < handle.tell():
                handle.seek(0)
                return True
        except (OSError, ValueError):
            return False
        return False
