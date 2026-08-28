import base64
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import unquote


VOICE_OPTIONS = [
    {"name": "Chatterbox Turbo", "voice": "chatterbox-turbo", "language": "en"},
]

class DashboardHub:
    def __init__(self, root, config, shutdown_event):
        self.root = Path(root)
        self.youtube_library_path = self.root / "youtube_library.json"
        self.config = config
        self.shutdown_event = shutdown_event
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.messages = deque(maxlen=80)
        self.logs = deque(maxlen=120)
        self.youtube_history = deque(maxlen=30)
        self.youtube_command_seq = 0
        self.youtube_command_payload = None
        self.youtube_state = {
            "status": "idle", "video_id": None, "title": None,
            "current_seconds": 0, "autoplay_blocked": False,
        }
        self.memory_store = None
        self.game_events = None
        self.last_media_action = None
        self.server = None
        self.thread = None
        self.voice_change_handler = None
        self.microphone_change_handler = None
        self.audio_output_change_handler = None
        self.budget_state_provider = None
        self.budget_resume_handler = None
        self.body_test_handler = None
        self.microphone_options = []
        self.audio_output_options = []
        self.state = {
            "phase": "starting",
            "phase_label": "Starting up",
            "model": "—",
            "voice": "—",
            "microphone": "—",
            "session_cost": 0.0,
            "session_governed_cost": 0.0,
            "api_calls": 0,
            "first_audio": None,
            "first_text": None,
            "endpoint_wait": None,
            "stt_seconds": None,
        }

    def set_phase(self, phase, label=None):
        with self.lock:
            self.state["phase"] = phase
            self.state["phase_label"] = label or phase.replace("_", " ").title()

    def set_context_services(self, memory_store=None, game_events=None):
        self.memory_store = memory_store
        self.game_events = game_events

    def set_budget_handlers(self, state_provider, resume_handler):
        self.budget_state_provider = state_provider
        self.budget_resume_handler = resume_handler

    def set_body_test_handler(self, handler):
        self.body_test_handler = handler

    def resume_budget(self):
        if self.budget_resume_handler is None:
            raise ValueError("The budget governor is not ready.")
        return self.budget_resume_handler()

    def record(self, event_type, fields):
        timestamp = datetime.now().strftime("%H:%M:%S")
        field_text = ", ".join(f"{key}={value}" for key, value in fields.items())
        with self.lock:
            if "api_call_count" in fields:
                self.state["api_calls"] = fields["api_call_count"]
            self.logs.appendleft({
                "time": timestamp,
                "event": event_type,
                "text": field_text,
            })
            if event_type == "SESSION_START":
                self.state["model"] = fields.get("model", self.state["model"])
            elif event_type == "KOKORO_READY":
                voice_id = fields.get("voice", self.state["voice"])
                match = next((item for item in VOICE_OPTIONS if item["voice"] == voice_id), None)
                self.state["voice"] = match["name"] if match else voice_id
            elif event_type in {"MIC_READY", "MIC_RECONNECTED"}:
                self.state["microphone"] = fields.get("device", self.state["microphone"])
                self.state["phase"] = "listening"
                self.state["phase_label"] = "I’m listening."
            elif event_type == "HEARD":
                self.state["phase"] = "thinking"
                self.state["phase_label"] = "Thinking…"
                self.state["endpoint_wait"] = fields.get("endpoint_wait_seconds")
                self.state["stt_seconds"] = fields.get("stt_seconds")
                self.messages.append({
                    "speaker": "Tony",
                    "text": fields.get("text", ""),
                    "time": timestamp,
                })
            elif event_type == "MODEL_LATENCY":
                self.state["first_text"] = fields.get("first_text_seconds")
            elif event_type == "TTS_AUDIO_START" and fields.get("phrase_index", 1) == 1:
                latency = fields.get("response_latency_seconds")
                if latency:
                    self.state["first_audio"] = latency
                self.state["phase"] = "speaking"
                self.state["phase_label"] = "Speaking…"
            elif event_type in {"VOICE_REPLY", "SAY"}:
                text = fields.get("text", "")
                if text:
                    self.messages.append({"speaker": "Ember", "text": text, "time": timestamp})
            elif event_type == "SPOTIFY_COMMAND":
                reply = fields.get("reply", "")
                if reply:
                    self.messages.append({"speaker": "Ember", "text": reply, "time": timestamp})
            elif event_type == "API_USAGE":
                self.state["session_cost"] = fields.get(
                    "session_estimated_cost_usd", self.state["session_cost"]
                )
                self.state["session_governed_cost"] = fields.get(
                    "session_governed_cost_usd", self.state["session_governed_cost"]
                )
            elif event_type == "SESSION_END":
                self.state["phase"] = "sleeping"
                self.state["phase_label"] = "Asleep"

    def snapshot(self):
        budget = self.budget_state_provider() if self.budget_state_provider else {}
        if self.memory_store is not None:
            daily = self.memory_store.usage_rollup(
                day=datetime.now().astimezone().date().isoformat()
            )
            budget = {**budget, "daily": daily}
        with self.lock:
            controls = {
                "speak_out_loud": bool(self.config.get("speak_out_loud", True)),
                "screen_awareness": bool(self.config.get("screen_awareness", True)),
                "spontaneous_remarks": bool(self.config.get("spontaneous_remarks", True)),
                "music_autonomy": bool(self.config.get("music_autonomy", False)),
                "long_term_memory": bool(self.config.get("long_term_memory", True)),
                "game_event_awareness": bool(self.config.get("game_event_awareness", True)),
            }
            return {
                **self.state,
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "controls": controls,
                "budget": budget,
                "voice_options": VOICE_OPTIONS,
                "selected_voice": "chatterbox-turbo",
                "microphone_options": self.microphone_options,
                "selected_microphone": self.config.get("mic_device"),
                "audio_output_options": self.audio_output_options,
                "selected_audio_output": next((
                    item["id"] for item in self.audio_output_options
                    if item["name"] == self.config.get("tts_output_device")
                    and item["hostapi"] == self.config.get("tts_output_hostapi")
                ), None),
                "youtube": {
                    **self.youtube_state,
                    "command_seq": self.youtube_command_seq,
                    "command": self.youtube_command_payload,
                    "library": self.list_youtube_videos(),
                    "volume": int(self.config.get("youtube_volume", 70)),
                },
                "messages": list(self.messages),
                "logs": list(self.logs),
                "memory": {
                    "items": self.memory_store.list_memories(60) if self.memory_store else [],
                    "profile": self.memory_store.profile() if self.memory_store else {},
                    "stats": self.memory_store.stats() if self.memory_store else {},
                    "media_feedback": (
                        self.memory_store.list_media_feedback(60) if self.memory_store else []
                    ),
                    "sessions": self.memory_store.list_sessions(12) if self.memory_store else [],
                },
                "game_events": (
                    self.game_events.snapshot() if self.game_events else {
                        "status": "disabled", "log_path": None, "recent": []
                    }
                ),
            }

    def list_sounds(self):
        library = self._load_sound_library()
        sounds = []
        for path in sorted(self.soundboard_root.iterdir(), key=lambda item: item.name.lower()):
            if path.is_file() and path.suffix.lower() in SOUNDBOARD_EXTENSIONS:
                metadata = library.get(path.name, {})
                sounds.append({
                    "id": path.name,
                    "name": re.sub(r"[_-]+", " ", path.stem).strip(),
                    "bytes": path.stat().st_size,
                    "status": metadata.get("status", "unheard"),
                    "description": metadata.get("description", "Not analyzed yet."),
                    "use_when": metadata.get("use_when", ""),
                    "transcript": metadata.get("transcript", ""),
                    "description_source": metadata.get("description_source", "audio_analysis"),
                    "affinity": (
                        self.memory_store.media_score("sound", path.name)
                        if self.memory_store is not None else 0
                    ),
                })
        return sounds

    def _load_sound_library(self):
        try:
            return json.loads(self.soundboard_library_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_sound_library(self, library):
        temp_path = self.soundboard_library_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(library, indent=2), encoding="utf-8")
        os.replace(temp_path, self.soundboard_library_path)

    def correct_sound_metadata(
        self, sound_id, description, use_when=None, transcript=None,
        source="user_dashboard",
    ):
        sound_id = Path(str(sound_id or "")).name
        sound = next(
            (item for item in self.list_sounds() if item["id"].lower() == sound_id.lower()),
            None,
        )
        description = re.sub(r"\s+", " ", str(description or "")).strip()[:240]
        if sound is None:
            raise ValueError("Sound not found.")
        if not description:
            raise ValueError("Tell Ember what the clip actually contains.")
        with self.lock:
            library = self._load_sound_library()
            current = dict(library.get(sound["id"], {}))
            current.update({
                "status": "ready",
                "description": description,
                "use_when": (
                    re.sub(r"\s+", " ", str(use_when)).strip()[:240]
                    if use_when is not None else current.get("use_when", "")
                ),
                "transcript": (
                    re.sub(r"\s+", " ", str(transcript)).strip()[:500]
                    if transcript is not None else current.get("transcript", "")
                ),
                "description_source": source,
                "corrected_at": datetime.now().isoformat(timespec="seconds"),
            })
            current.pop("error", None)
            library[sound["id"]] = current
            self._save_sound_library(library)
        return {"id": sound["id"], **current}

    @staticmethod
    def _sound_aliases(sound):
        def normalize(value):
            words = re.findall(r"[a-z0-9]+", str(value or "").lower())
            words = [word[:-1] if word.endswith("s") and len(word) > 3 else word for word in words]
            return " ".join(words)

        aliases = {
            normalize(sound.get("name")),
            normalize(Path(sound.get("id", "")).stem),
        }
        aliases.update(alias.removesuffix(" sound").strip() for alias in list(aliases))
        if "erro" in aliases:
            aliases.add("error")
        return {alias for alias in aliases if alias}

    def observe_sound_corrections(self, text):
        """Persist plain-English corrections such as 'erro is the Windows error sound'."""
        original = re.sub(r"[.!?]+$", "", str(text or "").strip())
        if not original:
            return []
        sounds = self.list_sounds()
        corrected = []

        def normalize(value):
            words = re.findall(r"[a-z0-9]+", str(value or "").lower())
            words = [word[:-1] if word.endswith("s") and len(word) > 3 else word for word in words]
            return " ".join(words)

        clauses = re.split(r"\s*(?:,|;|\band\b|\bbut\b)\s*", original, flags=re.IGNORECASE)
        for clause in clauses:
            match = re.match(
                r"^(?:no\s+)?(?:the\s+)?(.+?)\s+(?:is|are|was|were)\s+"
                r"(?:actually\s+)?(.+)$",
                clause.strip(),
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            subject, description = match.groups()
            normalized_subject = normalize(subject)
            description = description.strip(" \"'")
            if re.match(r"^(?:not|what|why|wrong)\b", description, re.IGNORECASE):
                continue
            sound = next(
                (
                    item for item in sounds
                    if normalized_subject in self._sound_aliases(item)
                ),
                None,
            )
            if sound is None:
                continue
            use_when = "Use only when this literal sound matches the moment."
            lowered = description.lower()
            if "windows" in lowered and "error" in lowered:
                use_when = "After an error, failed action, or obvious mistake; never as a victory fanfare."
            elif "metal pipe" in lowered:
                use_when = "For a sudden impact, collision, or absurd failure; never as a rimshot."
            metadata = self.correct_sound_metadata(
                sound["id"], description, use_when=use_when, source="user_voice",
            )
            corrected.append({"id": sound["id"], "description": metadata["description"]})

        if not corrected and self.last_media_action and self.last_media_action.get("type") == "sound":
            match = re.search(
                r"(?:^|\bno[, —-]*)that(?: one)?\s+(?:is|was)\s+actually\s+(.+)$",
                original,
                flags=re.IGNORECASE,
            )
            if match:
                description = match.group(1).strip(" \"'")
                metadata = self.correct_sound_metadata(
                    self.last_media_action["id"], description,
                    use_when="Use only when this literal sound matches the moment.",
                    source="user_voice",
                )
                corrected.append({
                    "id": self.last_media_action["id"],
                    "description": metadata["description"],
                })
        return corrected

    def set_soundboard_handlers(self, analyzer, play, stop):
        self.sound_analyzer = analyzer
        self.sound_play_handler = play
        self.sound_stop_handler = stop
        self.analyze_pending_sounds()

    def analyze_pending_sounds(self):
        if self.sound_analyzer is None:
            return
        library = self._load_sound_library()
        pending_paths = []
        for sound in self.list_sounds():
            if sound["status"] in {"unheard", "pending"}:
                library[sound["id"]] = {
                    **library.get(sound["id"], {}),
                    "status": "analyzing",
                    "description": "Ember is listening…",
                }
                pending_paths.append(self.soundboard_root / sound["id"])
        self._save_sound_library(library)
        for path in pending_paths:
            threading.Thread(target=self._analyze_sound, args=(path,), daemon=True).start()

    def _analyze_sound(self, path):
        with self.sound_analysis_lock:
            try:
                result = self.sound_analyzer(path)
                metadata = {
                    "status": "ready",
                    "description": str(result.get("description", "Audio clip")).strip()[:240],
                    "use_when": str(result.get("use_when", "")).strip()[:240],
                    "transcript": str(result.get("transcript", "")).strip()[:500],
                    "description_source": "audio_analysis",
                }
            except Exception as exc:
                metadata = {
                    "status": "error",
                    "description": "Ember couldn’t identify this clip yet.",
                    "use_when": "",
                    "error": str(exc)[:300],
                }
            with self.lock:
                library = self._load_sound_library()
                library[path.name] = metadata
                self._save_sound_library(library)

    def soundboard_context(self, spontaneous=False):
        now = time.time()
        history = list(self.sound_history)
        minimum_gap = float(self.config.get("soundboard_minimum_gap_seconds", 45))
        clip_cooldown = float(self.config.get("soundboard_clip_cooldown_seconds", 300))
        last_spontaneous = next(
            (item for item in reversed(history) if item.get("source") == "sophia_spontaneous"),
            None,
        )
        if spontaneous and last_spontaneous and now - last_spontaneous["time"] < minimum_gap:
            return []

        clips = []
        for sound in self.list_sounds():
            if sound["status"] not in {"ready", "error"}:
                continue
            last_use = next(
                (item for item in reversed(history) if item["id"].lower() == sound["id"].lower()),
                None,
            )
            seconds_ago = round(now - last_use["time"]) if last_use else None
            if spontaneous and seconds_ago is not None and seconds_ago < clip_cooldown:
                continue
            clips.append({
                "id": sound["id"],
                "description": (
                    sound["description"] if sound["status"] == "ready"
                    else f"Clip named '{sound['name']}' (audio analysis unavailable)"
                ),
                "use_when": (
                    sound["use_when"] if sound["status"] == "ready"
                    else "Use only when the filename is a clear match for the moment."
                ),
                "transcript": sound.get("transcript", ""),
                "last_used_seconds_ago": seconds_ago,
                "affinity": (
                    self.memory_store.media_score("sound", sound["id"])
                    if self.memory_store is not None else 0
                ),
            })
        return sorted(clips, key=lambda item: item["affinity"], reverse=True)

    def play_sound(self, sound_id, source="sophia", volume=None):
        sound_id = Path(str(sound_id or "")).name
        sounds = self.list_sounds()
        match = next((sound for sound in sounds if sound["id"].lower() == sound_id.lower()), None)
        if match is None:
            match = next((sound for sound in sounds if sound["name"].lower() == sound_id.lower()), None)
        if match is None:
            requested_key = re.sub(r"[^a-z0-9]+", " ", Path(sound_id).stem.lower()).strip()
            fuzzy_matches = []
            if len(requested_key) >= 8:
                for sound in sounds:
                    keys = {
                        re.sub(r"[^a-z0-9]+", " ", Path(sound["id"]).stem.lower()).strip(),
                        re.sub(r"[^a-z0-9]+", " ", sound["name"].lower()).strip(),
                    }
                    if any(requested_key in key or key in requested_key for key in keys):
                        fuzzy_matches.append(sound)
            if len(fuzzy_matches) == 1:
                match = fuzzy_matches[0]
        if match is None:
            raise ValueError("Sound not found.")
        chosen_volume = self.config.get("soundboard_volume", 0.8) if volume is None else volume
        chosen_volume = max(0.0, min(1.0, float(chosen_volume)))
        if self.sound_play_handler is None:
            raise ValueError("Soundboard player is not ready.")
        now = time.time()
        if source == "sophia_spontaneous" and self.sound_history:
            minimum_gap = float(self.config.get("soundboard_minimum_gap_seconds", 45))
            last_spontaneous = next(
                (
                    item for item in reversed(self.sound_history)
                    if item.get("source") == "sophia_spontaneous"
                ),
                None,
            )
            if last_spontaneous and now - last_spontaneous["time"] < minimum_gap:
                raise ValueError("Ember's soundboard is cooling down.")
            clip_cooldown = float(self.config.get("soundboard_clip_cooldown_seconds", 300))
            last_same = next(
                (
                    item for item in reversed(self.sound_history)
                    if item["id"].lower() == match["id"].lower()
                ),
                None,
            )
            if last_same and now - last_same["time"] < clip_cooldown:
                raise ValueError("Ember used that clip too recently.")
        self.sound_play_handler(self.soundboard_root / match["id"], match["name"], chosen_volume)
        self.sound_history.append({
            "id": match["id"],
            "name": match["name"],
            "source": source,
            "time": now,
        })
        self.last_media_action = {
            "type": "sound", "id": match["id"], "name": match["name"], "time": now,
        }
        if self.memory_store is not None:
            self.memory_store.record_media_use("sound", match["id"])
        return match

    def stop_sound(self):
        if self.sound_stop_handler is not None:
            self.sound_stop_handler()

    def _load_youtube_library(self):
        try:
            payload = json.loads(self.youtube_library_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except Exception:
            return []

    def _save_youtube_library(self, library):
        temp_path = self.youtube_library_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_path, self.youtube_library_path)

    @staticmethod
    def _youtube_video_id(value):
        value = str(value or "").strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
            return value
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = parsed.netloc.lower().removeprefix("www.")
        candidate = None
        if host == "youtu.be":
            candidate = parsed.path.strip("/").split("/")[0]
        elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch":
                from urllib.parse import parse_qs
                candidate = parse_qs(parsed.query).get("v", [None])[0]
            else:
                parts = parsed.path.strip("/").split("/")
                if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
                    candidate = parts[1]
        if candidate and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate
        raise ValueError("Paste a valid YouTube video link.")

    @staticmethod
    def _time_seconds(value):
        if isinstance(value, (int, float)):
            return max(0.0, min(float(value), 86400.0))
        text = str(value or "0").strip()
        try:
            parts = [float(part) for part in text.split(":")]
        except ValueError:
            raise ValueError("Start time must be seconds or mm:ss.")
        if len(parts) == 1:
            total = parts[0]
        elif len(parts) == 2:
            total = parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            total = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError("Start time must be seconds or mm:ss.")
        return max(0.0, min(total, 86400.0))

    def list_youtube_videos(self):
        videos = self._load_youtube_library()
        if self.memory_store is None:
            return videos
        return [
            {
                **item,
                "affinity": self.memory_store.media_score("video", item.get("id")),
            }
            for item in videos
        ]

    def add_youtube_video(self, url, title, use_when, start_seconds=0):
        video_id = self._youtube_video_id(url)
        title = str(title or "").strip()[:120] or f"YouTube video {video_id}"
        entry = {
            "id": uuid.uuid4().hex[:12], "video_id": video_id, "title": title,
            "use_when": str(use_when or "").strip()[:280],
            "start_seconds": round(self._time_seconds(start_seconds), 3),
            "added_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self.lock:
            library = self._load_youtube_library()
            library.append(entry)
            self._save_youtube_library(library[-40:])
        return entry

    def remove_youtube_video(self, entry_id):
        entry_id = str(entry_id or "")
        with self.lock:
            library = self._load_youtube_library()
            updated = [item for item in library if item.get("id") != entry_id]
            if len(updated) == len(library):
                raise ValueError("Saved video not found.")
            self._save_youtube_library(updated)

    def youtube_context(self, spontaneous=False):
        if not self.config.get("music_autonomy", False):
            return {"status": self.youtube_state.get("status", "idle"), "videos": []}
        now = time.time()
        last_spontaneous = next(
            (
                item for item in reversed(self.youtube_history)
                if item.get("source") == "sophia_spontaneous"
            ),
            None,
        )
        if spontaneous and last_spontaneous:
            minimum_gap = float(self.config.get("youtube_minimum_gap_seconds", 300))
            if now - last_spontaneous["time"] < minimum_gap:
                return {"status": self.youtube_state.get("status", "idle"), "videos": []}
        videos = []
        for item in self.list_youtube_videos()[-12:]:
            last_use = next(
                (row for row in reversed(self.youtube_history) if row.get("id") == item.get("id")),
                None,
            )
            videos.append({
                "id": item.get("id"), "title": item.get("title"),
                "start_seconds": item.get("start_seconds", 0),
                "use_when": item.get("use_when", ""),
                "last_used_seconds_ago": round(now - last_use["time"]) if last_use else None,
                "affinity": (
                    self.memory_store.media_score("video", item.get("id"))
                    if self.memory_store is not None else 0
                ),
            })
        videos.sort(key=lambda item: item["affinity"], reverse=True)
        return {
            "status": self.youtube_state.get("status", "idle"),
            "title": self.youtube_state.get("title"),
            "current_seconds": self.youtube_state.get("current_seconds", 0),
            "videos": videos,
        }

    def youtube_command(self, action, entry_id=None, seconds=None, source="sophia"):
        action = str(action or "").lower().strip()
        if action not in {"play", "pause", "resume", "stop", "seek"}:
            raise ValueError("Unknown YouTube action.")
        command = {"action": action, "source": source}
        if action == "play":
            key = str(entry_id or "").lower().strip()
            library = self.list_youtube_videos()
            match = next((item for item in library if str(item.get("id", "")).lower() == key), None)
            if match is None:
                match = next((item for item in library if str(item.get("title", "")).lower() == key), None)
            if match is None:
                raise ValueError("Saved video not found.")
            now = time.time()
            if source == "sophia_spontaneous" and self.youtube_history:
                minimum_gap = float(self.config.get("youtube_minimum_gap_seconds", 300))
                last_spontaneous = next(
                    (
                        item for item in reversed(self.youtube_history)
                        if item.get("source") == "sophia_spontaneous"
                    ),
                    None,
                )
                if last_spontaneous and now - last_spontaneous["time"] < minimum_gap:
                    raise ValueError("Ember's YouTube player is cooling down.")
            command.update({
                "id": match["id"], "video_id": match["video_id"], "title": match["title"],
                "seconds": self._time_seconds(match.get("start_seconds", 0) if seconds is None else seconds),
            })
            self.youtube_history.append({"id": match["id"], "time": now, "source": source})
            self.last_media_action = {
                "type": "video", "id": match["id"], "name": match["title"], "time": now,
            }
            if self.memory_store is not None:
                self.memory_store.record_media_use("video", match["id"])
        elif action == "seek":
            command["seconds"] = self._time_seconds(seconds)
        with self.lock:
            self.youtube_command_seq += 1
            command["sequence"] = self.youtube_command_seq
            self.youtube_command_payload = command
            self.youtube_state["status"] = "commanded"
            if command.get("title"):
                self.youtube_state["title"] = command["title"]
                self.youtube_state["video_id"] = command["video_id"]
        return command

    def update_youtube_status(self, payload):
        allowed = {"status", "video_id", "title", "current_seconds", "autoplay_blocked"}
        with self.lock:
            for key in allowed:
                if key in payload:
                    self.youtube_state[key] = payload[key]
        return dict(self.youtube_state)

    def observe_media_feedback(self, text):
        if self.memory_store is None or self.last_media_action is None:
            return None
        action = dict(self.last_media_action)
        action["age_seconds"] = max(0, time.time() - action["time"])
        feedback = self.memory_store.observe_media_feedback(text, action)
        return feedback

    def add_memory(self, payload):
        if self.memory_store is None:
            raise ValueError("Long-term memory is not ready.")
        return self.memory_store.add_memory(
            payload.get("content"), payload.get("category", "fact"),
            payload.get("subject", ""), payload.get("importance", 0.5),
            payload.get("confidence", 1.0), "dashboard", payload.get("pinned", False),
            payload.get("id"),
        )

    def archive_memory(self, memory_id):
        if self.memory_store is None:
            raise ValueError("Long-term memory is not ready.")
        self.memory_store.archive_memory(memory_id)

    def update_personality(self, key, value):
        if self.memory_store is None:
            raise ValueError("Personality storage is not ready.")
        return self.memory_store.update_profile(key, value)

    def inject_game_event(self, event_type, title=None):
        if self.game_events is None:
            raise ValueError("Game event awareness is not ready.")
        return self.game_events.inject(event_type, title=title, source="dashboard_test")

    def test_body(self, preset):
        presets = {
            "idle": ["idle"],
            "wave": ["amused"],
            "jump": ["excited"],
            "worry": ["concerned"],
            "startle": ["startled"],
            "laugh": ["laughing"],
            "facepalm": ["facepalming"],
            "embarrassed": ["embarrassed"],
            "shy": ["shy"],
            "cry": ["crying"],
            "smug": ["smug"],
            "proud": ["proud"],
            "curious": ["curious"],
            "determined": ["determined"],
            "sleepy": ["sleepy"],
            "annoyed": ["annoyed"],
            "confused": ["confused"],
            "skeptical": ["skeptical"],
            "affectionate": ["affectionate"],
            "relieved": ["relieved"],
            "mischievous": ["mischievous"],
            "celebrate": ["excited", "amused", "excited"],
            "meltdown": ["concerned", "startled", "concerned"],
        }
        states = presets.get(str(preset or ""))
        if states is None:
            raise ValueError("Unknown body test preset.")
        if self.body_test_handler is None:
            raise ValueError("Ember's body is not ready.")
        self.body_test_handler(states)
        return {"preset": preset, "states": states}

    def update_game_config(self, log_path, player_name, character_class="", game_mode="Standard"):
        with self.lock:
            self.config["wow_combat_log_path"] = str(log_path or "").strip()
            self.config["wow_player_name"] = str(player_name or "").strip()[:80]
            self.config["wow_character_class"] = str(character_class or "").strip()[:40]
            mode = str(game_mode or "Standard").strip().title()
            self.config["wow_game_mode"] = mode if mode in {"Standard", "Hardcore"} else "Standard"
            config_path = self.root / "config.json"
            tmp_path = config_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            os.replace(tmp_path, config_path)
        if self.game_events is not None:
            self.game_events.log_path = None
        return {
            "wow_combat_log_path": self.config["wow_combat_log_path"],
            "wow_player_name": self.config["wow_player_name"],
            "wow_character_class": self.config["wow_character_class"],
            "wow_game_mode": self.config["wow_game_mode"],
        }

    def add_sound(self, original_name, encoded_data):
        original_name = Path(str(original_name or "")).name
        extension = Path(original_name).suffix.lower()
        if extension not in SOUNDBOARD_EXTENSIONS:
            raise ValueError("Use an MP3 or WAV audio file.")
        try:
            data = base64.b64decode(encoded_data, validate=True)
        except Exception:
            raise ValueError("The audio upload could not be decoded.")
        if not data or len(data) > MAX_SOUND_BYTES:
            raise ValueError("Each sound must be between 1 byte and 12 MB.")

        clean_stem = re.sub(r"[^A-Za-z0-9 _.-]+", "", Path(original_name).stem).strip()
        clean_stem = clean_stem[:80] or "sound"
        target = self.soundboard_root / f"{clean_stem}{extension}"
        counter = 2
        while target.exists():
            target = self.soundboard_root / f"{clean_stem} {counter}{extension}"
            counter += 1
        temp_path = target.with_suffix(target.suffix + ".upload")
        temp_path.write_bytes(data)
        os.replace(temp_path, target)
        with self.lock:
            library = self._load_sound_library()
            library[target.name] = {
                "status": "analyzing" if self.sound_analyzer is not None else "pending",
                "description": "Ember is listening…" if self.sound_analyzer is not None else "Waiting for Ember to listen.",
                "use_when": "",
            }
            self._save_sound_library(library)
        if self.sound_analyzer is not None:
            threading.Thread(target=self._analyze_sound, args=(target,), daemon=True).start()
        return {"id": target.name, "name": clean_stem, "bytes": len(data)}

    def archive_sound(self, sound_id):
        source = (self.soundboard_root / Path(str(sound_id or "")).name).resolve()
        if source.parent != self.soundboard_root.resolve() or not source.is_file():
            raise ValueError("Sound not found.")
        target = self.soundboard_archive / source.name
        counter = 2
        while target.exists():
            target = self.soundboard_archive / f"{source.stem} {counter}{source.suffix}"
            counter += 1
        os.replace(source, target)
        with self.lock:
            library = self._load_sound_library()
            library.pop(source.name, None)
            self._save_sound_library(library)

    def set_voice_change_handler(self, handler):
        self.voice_change_handler = handler

    def set_microphone_controls(self, options, selected, handler):
        with self.lock:
            self.microphone_options = list(options)
            self.config["mic_device"] = selected
        self.microphone_change_handler = handler

    def update_microphone(self, device_index):
        try:
            device_index = int(device_index)
        except (TypeError, ValueError):
            raise ValueError("Invalid microphone.")
        match = next(
            (item for item in self.microphone_options if item["index"] == device_index),
            None,
        )
        if match is None:
            raise ValueError("Unknown microphone.")
        with self.lock:
            self.config["mic_device"] = device_index
            self.state["phase"] = "reconnecting"
            self.state["phase_label"] = "Switching microphones…"
            config_path = self.root / "config.json"
            tmp_path = config_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            os.replace(tmp_path, config_path)
        if self.microphone_change_handler is not None:
            self.microphone_change_handler(device_index)
        return match

    def set_audio_output_controls(self, options, handler):
        with self.lock:
            self.audio_output_options = list(options)
        self.audio_output_change_handler = handler

    def update_audio_output(self, output_id):
        match = next(
            (item for item in self.audio_output_options if item["id"] == str(output_id)),
            None,
        )
        if match is None:
            raise ValueError("Unknown audio output.")
        with self.lock:
            self.config["tts_output_device"] = match["name"]
            self.config["tts_output_hostapi"] = match["hostapi"]
            self.state["phase"] = "warming"
            self.state["phase_label"] = "Switching speakers…"
            config_path = self.root / "config.json"
            tmp_path = config_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            os.replace(tmp_path, config_path)
        if self.audio_output_change_handler is not None:
            self.audio_output_change_handler(match["name"], match["hostapi"])
        return match

    def update_voice(self, voice_id):
        match = next((item for item in VOICE_OPTIONS if item["voice"] == voice_id), None)
        if match is None:
            raise ValueError("Unknown voice.")
        with self.lock:
            self.config["tts_voice"] = match["voice"]
            config_path = self.root / "config.json"
            tmp_path = config_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            os.replace(tmp_path, config_path)
        if self.voice_change_handler is not None:
            self.voice_change_handler(match["voice"], match["language"], match["name"])
        return match

    def update_control(self, name, value):
        allowed = {
            "speak_out_loud",
            "screen_awareness",
            "spontaneous_remarks",
            "music_autonomy",
            "long_term_memory",
            "game_event_awareness",
        }
        if name not in allowed:
            raise ValueError("Unknown dashboard control.")
        with self.lock:
            self.config[name] = bool(value)
            config_path = self.root / "config.json"
            tmp_path = config_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            os.replace(tmp_path, config_path)

    def start(self, port=8766, open_browser=True):
        hub = self
        dashboard_root = self.root / "dashboard_static"
        stylesheet_path = self.root / "dashboard" / "app" / "globals.css"

        class Handler(BaseHTTPRequestHandler):
            def _origin_allowed(self):
                origin = self.headers.get("Origin")
                return origin in {
                    None, f"http://127.0.0.1:{port}", f"http://localhost:{port}",
                    "http://127.0.0.1:3000", "http://localhost:3000",
                }

            def _headers(self, status=200, content_type="application/json; charset=utf-8", content_length=None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                origin = self.headers.get("Origin")
                if origin and self._origin_allowed():
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "Content-Type")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                if content_length is not None:
                    self.send_header("Content-Length", str(content_length))
                self.end_headers()

            def _json(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self._headers(status)
                self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/api/state":
                    self._json(hub.snapshot())
                    return
                if path == "/styles.css":
                    file_path = stylesheet_path
                elif path in {"/", "/index.html"}:
                    file_path = dashboard_root / "index.html"
                else:
                    file_path = (dashboard_root / path.lstrip("/")).resolve()
                    if dashboard_root.resolve() not in file_path.parents:
                        self._json({"error": "Not found"}, 404)
                        return
                if not file_path.is_file():
                    self._json({"error": "Not found"}, 404)
                    return
                body = file_path.read_bytes()
                content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                self._headers(200, content_type, len(body))
                self.wfile.write(body)

            def do_POST(self):
                if not self._origin_allowed():
                    self._json({"error": "Forbidden"}, 403)
                    return
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                request_limit = 4096
                if length > request_limit:
                    self._json({"error": "Request too large"}, 413)
                    return
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if path == "/api/control":
                        hub.update_control(payload.get("name"), payload.get("value"))
                        self._json({"ok": True, "controls": hub.snapshot()["controls"]})
                    elif path == "/api/voice":
                        voice = hub.update_voice(payload.get("voice"))
                        self._json({"ok": True, "voice": voice})
                    elif path == "/api/microphone":
                        microphone = hub.update_microphone(payload.get("device"))
                        self._json({"ok": True, "microphone": microphone})
                    elif path == "/api/audio-output":
                        output = hub.update_audio_output(payload.get("output"))
                        self._json({"ok": True, "audio_output": output})
                    elif path == "/api/youtube/add":
                        video = hub.add_youtube_video(
                            payload.get("url"), payload.get("title"), payload.get("use_when"),
                            payload.get("start_seconds", 0),
                        )
                        self._json({"ok": True, "video": video})
                    elif path == "/api/youtube/remove":
                        hub.remove_youtube_video(payload.get("id"))
                        self._json({"ok": True})
                    elif path == "/api/youtube/command":
                        command = hub.youtube_command(
                            payload.get("action"), payload.get("id"), payload.get("seconds"),
                            "dashboard",
                        )
                        self._json({"ok": True, "command": command})
                    elif path == "/api/youtube/status":
                        state = hub.update_youtube_status(payload)
                        self._json({"ok": True, "youtube": state})
                    elif path == "/api/memory/add":
                        memory = hub.add_memory(payload)
                        self._json({"ok": True, "memory": memory})
                    elif path == "/api/memory/archive":
                        hub.archive_memory(payload.get("id"))
                        self._json({"ok": True})
                    elif path == "/api/personality":
                        profile = hub.update_personality(payload.get("key"), payload.get("value"))
                        self._json({"ok": True, "profile": profile})
                    elif path == "/api/game/event":
                        event = hub.inject_game_event(payload.get("event_type"), payload.get("title"))
                        self._json({"ok": True, "event": event})
                    elif path == "/api/body/test":
                        sequence = hub.test_body(payload.get("preset"))
                        self._json({"ok": True, "sequence": sequence})
                    elif path == "/api/game/config":
                        settings = hub.update_game_config(
                            payload.get("log_path"), payload.get("player_name"),
                            payload.get("character_class"), payload.get("game_mode"),
                        )
                        self._json({"ok": True, "settings": settings})
                    elif path == "/api/budget/resume":
                        self._json({"ok": True, "budget": hub.resume_budget()})
                    elif path == "/api/sleep":
                        hub.shutdown_event.set()
                        self._json({"ok": True})
                    else:
                        self._json({"error": "Not found"}, 404)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._json({"error": str(exc)}, 400)

            def do_OPTIONS(self):
                self._headers(204 if self._origin_allowed() else 403)

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/")

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            if self.thread is not None:
                self.thread.join(timeout=2)
