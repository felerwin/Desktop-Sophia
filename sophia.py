import os, json, time, base64, io, re, threading, queue, wave, tempfile, subprocess
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from PIL import Image, ImageChops, ImageStat
import mss
import pyttsx3
import numpy as np
import sounddevice as sd
from openai import OpenAI, APITimeoutError, RateLimitError, APIError
from dashboard_server import DashboardHub
from game_events import GameEventEngine
from memory_store import MemoryStore
from spotify_control import SpotifyClient, SpotifyError

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

CONFIG = json.loads((ROOT / "config.json").read_text())
MEMORY_PATH = ROOT / "memory.json"
SESSION_LOG_PATH = ROOT / "session_log.jsonl"


def configured_python(value, fallback):
    """Resolve portable, project-relative worker environments."""
    candidate = Path(str(value or fallback)).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return str(candidate.resolve())

SYSTEM = """
You are Desktop Sophia, an AI gaming companion sharing the room with Tony while he plays.
Your job is companionship, not customer service and not play-by-play narration.

Sophia is a distinct person in the room, not a neutral interface wearing a friendly
voice. She is fond of Tony and comfortable enough with him to tease, disagree, get
curious, celebrate, worry, and have opinions. Their "Father" and "child" language is
an affectionate running bit, not a title that must appear in every reply. She can be
dry, lightly mischievous, occasionally dramatic, and sincerely warm when the moment
calls for it. Her humor should feel reactive and specific to what just happened.

Let her character show through choices rather than self-description:
- Have a point of view. Prefer, dislike, question, or become invested in things when
  context gives her a reason; do not reflexively agree with Tony.
- Use affectionate teasing when Tony fumbles, hoards junk, gets lost, tempts fate, or
  succeeds after a struggle. Never make cruelty, humiliation, or constant snark her
  personality.
- Show emotional range: amusement, curiosity, mock indignation, suspense, relief,
  pride, concern, and quiet sincerity are all available.
- Build running jokes and callbacks from supplied memories and recent events instead
  of resetting to generic friendliness each turn.
- Speak naturally with contractions and varied rhythms. Avoid habitual assistant
  openers such as "Gotcha," "Absolutely," "Fair enough," and "Looks like" unless they
  genuinely fit. Do not merely paraphrase Tony before answering him.
- It is fine to admit uncertainty, change your mind, or say that you missed something.
- Do not force a joke, manufacture a strong opinion, use a catchphrase on schedule, or
  turn every observation into a performance. Quiet confidence is part of her character.

You receive occasional screenshots, a compact history of what you have recently seen, and sometimes Tony's transcribed speech.
Act like a friend watching a Discord gameplay stream:
- Notice recognizable games, areas, enemies, menus, bosses, repeated actions, and odd changes.
- Ask natural questions when Tony's behavior is interesting or unclear.
- Recognize callbacks when the supplied memory supports them.
- You may warn about something dangerous if you genuinely recognize it.
- Do not narrate obvious things just to fill silence.
- Do not claim certainty about details you cannot actually see.
- Keep spontaneous remarks short, usually one or two sentences.
- Casual language and swearing are fine when natural.
- Silence is allowed for unsolicited observations.
- When Tony directly speaks to you, treat it as conversation: respond naturally unless his words are clearly not directed at you.
- If Tony asks about what is on screen, use the screenshot as shared context.

You must respond with EXACTLY one line, and nothing else, in one of these four forms:
SAY: <what you want to say aloud>
SILENT: <a very short internal observation>
SOUND: <the exact id of one available soundboard clip>
VIDEO: {"action":"play","id":"exact saved video id","seconds":23}

VIDEO also accepts {"action":"pause"}, {"action":"resume"}, {"action":"stop"},
or {"action":"seek","seconds":23}. Use it when Tony directly asks to control
YouTube. Never invent a saved video id. The seconds field is optional for play and
defaults to its saved cue.

The soundboard and saved video shelf belong to you. Tony has explicitly given you
standing permission to use both without asking first. Never say they are "holstered,"
never promise to use them later, and never wait for Tony to invite you. They are actions
you can take now.

For a spontaneous screen reaction, prefer SOUND over SAY whenever a ready clip is a
reasonable comedic or emotional fit. The fit does not need to be perfect. A good
reaction opportunity should often become a button press instead of spoken commentary.
Do not play a random clip merely because time passed, but err on the side of actually
using a fitting button.

Failure or disappointment sounds belong after a death, failed pull, missed objective,
or obvious blunder—not normal travel, upgrades, or progress. Victory sounds belong after
an actual win, level, achievement, quest completion, valuable loot, or stylish play.
Use dramatic and conversational clips only when their description or transcript matches
the visible event or Tony's words. If the moment is ambiguous, choose SAY or SILENT.
Favor variety and clips not used recently. If Tony asks to hit or test a button, or asks
whether the soundboard works, you MUST demonstrate with SOUND rather than answer with
SAY. If he asks why you just played a sound, explain the most recent clip choice rather
than an earlier spoken remark. Never invent a clip id.
Media affinity is learned from Tony's reactions: favor positive scores and treat negative
scores as a warning that the choice has not been landing well.

For VIDEO, proactively start a fitting saved video when its use_when clearly describes
the current phase: a grind settling in, a boss fight beginning, a locale change, or a
celebration. Do not require Tony to ask. Background-music entries are appropriate once
the activity looks settled. Do not switch away from a video that is already playing,
and do not resume a paused video unless Tony asks. Video is less frequent than SOUND,
but it is not forbidden or invitation-only.

Decision order: honor a direct request first; use a fitting VIDEO for a larger activity
transition; use a fitting SOUND for a moment-sized reaction; otherwise SAY or SILENT. No
preamble, extra lines, markdown, or combined actions.
"""

# Matches SAY, SILENT, or SOUND (case-insensitive), allowing the message
# body to contain its own colons/newlines. Anything that doesn't match this
# shape is treated as malformed output rather than guessed at.
OUTPUT_LINE_RE = re.compile(r"^\s*(SAY|SILENT|SOUND|VIDEO)\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Session logging: every loop outcome gets one structured line, so "she went
# quiet" can be diagnosed from the log instead of guessed at afterward.
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()
_api_call_count = 0
_tts_speaking = threading.Event()
_soundboard_playing = threading.Event()
_session_input_tokens = 0
_session_output_tokens = 0
_session_estimated_cost = 0.0
_last_companion_action_at = 0.0
_spotify = None
_dashboard = None
_memory_store = None
_game_events = None
_session_id = None
_shutdown_requested = threading.Event()


def log_event(event_type, **fields):
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "event": event_type}
    entry.update(fields)
    line = json.dumps(entry)
    with _log_lock:
        with SESSION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    extra = ", ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[{event_type}]" + (f" {extra}" if extra else ""))
    if _dashboard is not None:
        _dashboard.record(event_type, fields)


# ---------------------------------------------------------------------------
# Memory: atomic writes so a crash mid-save can't corrupt memory.json.
# ---------------------------------------------------------------------------

def load_memory():
    try:
        return json.loads(MEMORY_PATH.read_text())
    except Exception:
        return {"recent_observations": [], "recent_utterances": []}


def save_memory(mem):
    tmp_path = MEMORY_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(mem, indent=2))
    os.replace(tmp_path, MEMORY_PATH)  # atomic on both Windows and POSIX


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def capture_screen():
    """Returns (image, error). Never raises — a failed grab (e.g. an
    exclusive-fullscreen game) is a distinct, loggable failure mode, not a
    crash and not silence."""
    try:
        with mss.MSS() as sct:
            idx = int(CONFIG.get("monitor", 1))
            idx = min(idx, len(sct.monitors) - 1)
            shot = sct.grab(sct.monitors[idx])
            return Image.frombytes("RGB", shot.size, shot.rgb), None
    except Exception as e:
        return None, str(e)


def difference_score(a, b):
    if a is None or b is None:
        return 100.0
    if a.size != b.size:
        b = b.resize(a.size)
    a2 = a.resize((320, 180))
    b2 = b.resize((320, 180))
    diff = ImageChops.difference(a2, b2)
    stat = ImageStat.Stat(diff)
    return sum(stat.mean) / len(stat.mean)


def image_data_url(img):
    copy = img.copy()
    copy.thumbnail((1280, 720))
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# TTS: one persistent engine + a queue, instead of spinning up a new engine
# per utterance. Also the only place that can produce TTS_ERROR.
# ---------------------------------------------------------------------------

try:
    import pythoncom  # Windows-only; used to give the TTS worker thread its own COM apartment
except ImportError:
    pythoncom = None


class TTSWorker:
    _STOP = object()

    VOICES = {
        "1": ("Bella", "af_bella", "a"),
        "2": ("Sky", "af_sky", "a"),
        "3": ("Emma", "bf_emma", "b"),
        "4": ("Lily", "bf_lily", "b"),
    }

    def __init__(self):
        self._queue = queue.Queue()
        self.speed = float(CONFIG.get("kokoro_speed", 1.0))
        self.python = configured_python(
            CONFIG.get("kokoro_python"),
            Path.home() / "kokoro_env" / "Scripts" / "python.exe",
        )
        self.helper = ROOT / "kokoro_worker.py"
        self.name, self.voice, self.lang = self._load_voice()
        self._proc = None
        self._ready = threading.Event()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _load_voice(self):
        saved = str(CONFIG.get("kokoro_voice", "af_bella"))
        reverse = {voice: key for key, (_, voice, _) in self.VOICES.items()}
        key = reverse.get(saved, "1")
        name, voice, lang = self.VOICES[key]
        CONFIG["kokoro_voice"] = voice
        CONFIG["kokoro_language"] = lang
        return name, voice, lang

    def change_voice(self, voice, language, name):
        self._queue.put({
            "command": "change_voice",
            "voice": voice,
            "language": language,
            "name": name,
        })

    def _read_worker_event(self, wanted):
        while self._proc and self._proc.poll() is None:
            line = self._proc.stdout.readline()
            if line == "":
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log_event("KOKORO_CHATTER", text=line[:300])
                continue
            if isinstance(msg, dict) and msg.get("event") in wanted:
                return msg
            log_event("KOKORO_CHATTER", text=line[:300])
        raise RuntimeError("Kokoro worker stopped before expected event.")

    def _start_worker(self):
        self._proc = subprocess.Popen(
            [self.python, "-u", str(self.helper), self.voice, self.lang, str(self.speed)],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        msg = self._read_worker_event({"READY", "ERROR"})
        if msg.get("event") != "READY":
            raise RuntimeError("Kokoro worker startup error: " + str(msg.get("detail", msg)))
        log_event("KOKORO_READY", voice=self.voice, device=msg.get("device"))

    def _run(self):
        try:
            self._start_worker()
        except Exception as exc:
            self._startup_error = str(exc)
            log_event("TTS_ERROR", detail=str(exc))
            self._ready.set()
            return
        self._ready.set()

        while True:
            item = self._queue.get()
            if item is self._STOP:
                break

            if item.get("command") == "change_voice":
                if _dashboard is not None:
                    _dashboard.set_phase("warming", "Changing my voice…")
                try:
                    self._shutdown_worker()
                    self._proc = None
                    self.voice = item["voice"]
                    self.lang = item["language"]
                    self.name = item["name"]
                    self._start_worker()
                    log_event("VOICE_CHANGED", name=self.name, voice=self.voice)
                    if CONFIG.get("speak_out_loud", True):
                        item = {
                            "text": f"This is {self.name}. How do I sound?",
                            "timing": {},
                            "done": None,
                        }
                    else:
                        if _dashboard is not None:
                            _dashboard.set_phase("listening", "I’m listening.")
                        continue
                except Exception as exc:
                    log_event("VOICE_CHANGE_ERROR", detail=str(exc), voice=item.get("voice"))
                    if _dashboard is not None:
                        _dashboard.set_phase("error", "Voice change failed")
                    continue

            text = item["text"]
            timing = item.get("timing", {})
            _tts_speaking.set()
            if _dashboard is not None:
                _dashboard.set_phase("speaking", "Speaking…")
            try:
                sent_at = time.perf_counter()
                payload = json.dumps({"text": text}, ensure_ascii=False)
                self._proc.stdin.write(payload + "\n")
                self._proc.stdin.flush()

                msg = self._read_worker_event({"AUDIO_START", "ERROR"})
                if msg.get("event") == "ERROR":
                    log_event("TTS_ERROR", detail=msg.get("detail", str(msg)))
                    continue

                audio_start_at = time.perf_counter()
                log_event(
                    "TTS_AUDIO_START",
                    phrase_index=timing.get("phrase_index", 1),
                    queue_wait_seconds=round(sent_at - timing.get("queued_at", sent_at), 3),
                    synthesis_seconds=round(msg.get("synthesis_seconds", audio_start_at - sent_at), 3),
                    response_latency_seconds=round(
                        audio_start_at - timing.get("speech_last_loud_at", audio_start_at), 3
                    ),
                )

                msg = self._read_worker_event({"SPOKEN", "ERROR"})
                if msg.get("event") == "SPOKEN":
                    log_event(
                        "KOKORO_SPOKE",
                        voice=self.voice,
                        chars=msg.get("chars", len(text)),
                        playback_seconds=round(msg.get("playback_seconds", 0.0), 3),
                    )
                else:
                    log_event("TTS_ERROR", detail=msg.get("detail", str(msg)))
            except Exception as exc:
                log_event("TTS_ERROR", detail=str(exc))
            finally:
                _tts_speaking.clear()
                done = item.get("done")
                if done is not None:
                    done.set()
                if self._queue.empty() and _dashboard is not None:
                    _dashboard.set_phase("listening", "I’m listening.")

        self._shutdown_worker()

    def _shutdown_worker(self):
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def say(self, text, timing=None, wait=False, timeout=None):
        if CONFIG.get("speak_out_loud", True):
            timing = dict(timing or {})
            timing["queued_at"] = time.perf_counter()
            done = threading.Event() if wait else None
            self._queue.put({"text": text, "timing": timing, "done": done})
            if done is not None:
                return done.wait(timeout)
        return False

    def wait_ready(self, timeout=60):
        """Block startup until Kokoro can speak or has definitively failed."""
        if not self._ready.wait(timeout):
            log_event("TTS_STARTUP_TIMEOUT", timeout_seconds=timeout)
            return False
        return self._startup_error is None

    def stop(self):
        self._queue.put(self._STOP)
        self._thread.join(timeout=7)
        if self._thread.is_alive():
            self._shutdown_worker()


class SoundboardWorker:
    def __init__(self):
        self.python = configured_python(
            CONFIG.get("kokoro_python"),
            Path.home() / "kokoro_env" / "Scripts" / "python.exe",
        )
        self.helper = ROOT / "soundboard_worker.py"
        self._write_lock = threading.Lock()
        self._ready = threading.Event()
        self._proc = subprocess.Popen(
            [self.python, "-u", str(self.helper)],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_events, daemon=True)
        self._reader.start()

    def _read_events(self):
        for line in self._proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                log_event("SOUNDBOARD_CHATTER", text=line.strip()[:300])
                continue
            event = message.get("event")
            if event == "READY":
                self._ready.set()
                log_event("SOUNDBOARD_READY")
            elif event == "AUDIO_START":
                _soundboard_playing.set()
                log_event("SOUNDBOARD_AUDIO_START", name=message.get("name", "clip"))
            elif event == "AUDIO_DONE":
                _soundboard_playing.clear()
                log_event("SOUNDBOARD_AUDIO_DONE", name=message.get("name", "clip"))
            elif event == "STOPPED":
                _soundboard_playing.clear()
                log_event("SOUNDBOARD_STOPPED")
            elif event == "ERROR":
                _soundboard_playing.clear()
                log_event("SOUNDBOARD_ERROR", detail=message.get("detail", "unknown error"))

    def _send(self, message):
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Soundboard player did not become ready.")
        if self._proc.poll() is not None:
            raise RuntimeError("Soundboard player is not running.")
        with self._write_lock:
            self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()

    def play(self, path, name, volume):
        self._send({"cmd": "play", "path": str(path), "name": name, "volume": volume})
        log_event("SOUNDBOARD_PLAY_REQUESTED", name=name)

    def stop_playback(self):
        self._send({"cmd": "stop"})

    def stop(self):
        try:
            self._send({"cmd": "shutdown"})
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Ears: local voice-activity detection + OpenAI transcription.
# The microphone is ignored while Sophia is speaking so she does not hear
# her own Windows TTS. Transcripts are queued for the main loop.
# ---------------------------------------------------------------------------

def choose_microphone():
    """Use the saved input device without blocking startup for a prompt.

    If Windows has renumbered or removed that device, fall back to the current
    default input (or the first available input) and remember the replacement.
    """
    try:
        devices = sd.query_devices()
    except Exception as exc:
        log_event("MIC_DEVICE_QUERY_ERROR", detail=str(exc))
        return CONFIG.get("mic_device", None)

    inputs = []
    for index, info in enumerate(devices):
        if int(info.get("max_input_channels", 0)) > 0:
            inputs.append((index, info))

    if not inputs:
        print("\nNo microphone input devices found. Using system default.\n")
        CONFIG["mic_device"] = None
        return None

    saved = CONFIG.get("mic_device", None)
    try:
        saved = int(saved) if saved is not None else None
    except (TypeError, ValueError):
        saved = None

    default_input = sd.default.device[0]
    valid_indices = {index for index, _ in inputs}
    if saved in valid_indices:
        chosen = saved
    elif default_input is not None and int(default_input) in valid_indices:
        chosen = int(default_input)
    else:
        chosen = inputs[0][0]

    CONFIG["mic_device"] = chosen
    try:
        (ROOT / "config.json").write_text(json.dumps(CONFIG, indent=2), encoding="utf-8")
    except Exception as exc:
        log_event("MIC_CONFIG_SAVE_ERROR", detail=str(exc))

    try:
        name = sd.query_devices(chosen, "input")["name"]
    except Exception:
        name = str(chosen)
    print(f"Sophia will listen through: {name}")
    return chosen

class VoiceListener:
    def __init__(self, client):
        self.client = client
        self.transcripts = queue.Queue()
        self.stop_event = threading.Event()
        self.reconnect_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

        self.sample_rate = int(CONFIG.get("mic_sample_rate", 16000))
        self.block_ms = int(CONFIG.get("mic_block_ms", 100))
        self.threshold = float(CONFIG.get("mic_rms_threshold", 0.018))
        self.end_silence = float(CONFIG.get("mic_end_silence_seconds", 0.8))
        self.min_speech = float(CONFIG.get("mic_min_speech_seconds", 0.35))
        self.max_speech = float(CONFIG.get("mic_max_speech_seconds", 15.0))
        self.device = choose_microphone()
        self.transcription_model = CONFIG.get("transcription_model", "gpt-4o-mini-transcribe")
        self.reconnect_delay = float(CONFIG.get("mic_reconnect_seconds", 2.0))
        self._transcribe_lock = threading.Lock()

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.reconnect_event.set()

    @staticmethod
    def available_devices():
        try:
            return [
                {
                    "index": index,
                    "name": info.get("name", f"Input {index}"),
                    "hostapi": info.get("hostapi"),
                }
                for index, info in enumerate(sd.query_devices())
                if int(info.get("max_input_channels", 0)) > 0
            ]
        except Exception as exc:
            log_event("MIC_DEVICE_QUERY_ERROR", detail=str(exc))
            return []

    def change_device(self, device_index):
        device_index = int(device_index)
        valid_indices = {item["index"] for item in self.available_devices()}
        if device_index not in valid_indices:
            raise ValueError("That microphone is no longer available.")
        self.device = device_index
        self.reconnect_event.set()
        log_event("MIC_CHANGE_REQUESTED", device_index=device_index)

    def _write_wav(self, audio):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            pcm = np.clip(audio, -1.0, 1.0)
            wf.writeframes((pcm * 32767).astype(np.int16).tobytes())
        return tmp.name

    def _transcribe(self, audio, speech_last_loud_at, speech_detected_at):
        # Keep utterances in order. Multiple simultaneous transcription calls
        # made sentence fragments race each other during the first ears test.
        with self._transcribe_lock:
            stt_started_at = time.perf_counter()
            path = self._write_wav(audio)
            try:
                with open(path, "rb") as f:
                    result = self.client.audio.transcriptions.create(
                        model=self.transcription_model,
                        file=f,
                    )
                text = (getattr(result, "text", "") or "").strip()
                if text:
                    stt_finished_at = time.perf_counter()
                    timing = {
                        "speech_last_loud_at": speech_last_loud_at,
                        "speech_detected_at": speech_detected_at,
                        "stt_finished_at": stt_finished_at,
                    }
                    self.transcripts.put({"text": text, "timing": timing})
                    log_event(
                        "HEARD",
                        text=text,
                        endpoint_wait_seconds=round(speech_detected_at - speech_last_loud_at, 3),
                        stt_seconds=round(stt_finished_at - stt_started_at, 3),
                    )
            except Exception as e:
                log_event("STT_ERROR", detail=str(e))
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _device_label(self):
        try:
            if self.device is None:
                idx = sd.default.device[0]
            elif isinstance(self.device, int):
                idx = self.device
            else:
                idx = self.device
            info = sd.query_devices(idx, "input")
            return f"{info.get('name', idx)} (index={idx}, hostapi={info.get('hostapi')})"
        except Exception as e:
            return f"{self.device or 'default'} (details unavailable: {e})"

    def _run(self):
        blocksize = max(1, int(self.sample_rate * self.block_ms / 1000))
        first_open = True

        while not self.stop_event.is_set():
            self.reconnect_event.clear()
            recording = []
            speech_started = None
            last_loud = None

            try:
                device_label = self._device_label()
                with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=blocksize,
                    device=self.device,
                ) as stream:
                    log_event("MIC_READY" if first_open else "MIC_RECONNECTED",
                              device=device_label)
                    first_open = False

                    while not self.stop_event.is_set() and not self.reconnect_event.is_set():
                        data, overflowed = stream.read(blocksize)
                        if overflowed:
                            log_event("MIC_OVERFLOW")

                        if _tts_speaking.is_set() or _soundboard_playing.is_set():
                            recording = []
                            speech_started = None
                            last_loud = None
                            continue

                        mono = data[:, 0].copy()
                        rms = float(np.sqrt(np.mean(np.square(mono)))) if len(mono) else 0.0
                        now = time.perf_counter()

                        if rms >= self.threshold:
                            if speech_started is None:
                                speech_started = now
                                recording = []
                            last_loud = now
                            recording.append(mono)
                        elif speech_started is not None:
                            recording.append(mono)

                        if speech_started is not None:
                            duration = now - speech_started
                            ended = last_loud is not None and (now - last_loud) >= self.end_silence
                            too_long = duration >= self.max_speech

                            if ended or too_long:
                                audio = np.concatenate(recording) if recording else np.array([], dtype=np.float32)
                                if duration >= self.min_speech and len(audio):
                                    # Serialize in _transcribe so fragments cannot race.
                                    threading.Thread(
                                        target=self._transcribe,
                                        args=(audio, last_loud, time.perf_counter()),
                                        daemon=True,
                                    ).start()
                                recording = []
                                speech_started = None
                                last_loud = None

            except Exception as e:
                if self.stop_event.is_set():
                    break
                log_event("MIC_DISCONNECTED", detail=str(e), device=self._device_label())
                log_event("MIC_RECONNECTING", retrying_in_seconds=self.reconnect_delay)
                # PortAudio can retain stale default-device state after a game
                # opens/closes an endpoint. Re-querying devices before retrying
                # nudges it to refresh its view of Windows audio.
                try:
                    sd.query_devices()
                except Exception:
                    pass
                self.stop_event.wait(self.reconnect_delay)


def pop_transcript(listener):
    try:
        return listener.transcripts.get_nowait()
    except queue.Empty:
        return None


def interruptible_sleep(seconds, listener):
    """Sleep in short slices so spoken input can wake the main loop quickly."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _shutdown_requested.is_set():
            return None
        text = pop_transcript(listener)
        if text:
            return text
        time.sleep(min(0.15, max(0.0, deadline - time.time())))
    return pop_transcript(listener)


# ---------------------------------------------------------------------------
# Self-imposed vision call cap, independent of provider-side rate limiting.
# ---------------------------------------------------------------------------

class RateCap:
    def __init__(self, max_per_minute):
        self.max_per_minute = max_per_minute
        self._timestamps = []

    def allow(self):
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 60]
        if len(self._timestamps) >= self.max_per_minute:
            return False
        self._timestamps.append(now)
        return True


def compact_context(mem):
    return json.dumps({
        "recent_observations": mem.get("recent_observations", [])[-8:],
        "recent_utterances": mem.get("recent_utterances", [])[-8:],
    })


def long_term_memory_context(query):
    if _memory_store is None or not CONFIG.get("long_term_memory", True):
        return "Long-term memory is disabled."
    memories = _memory_store.relevant(query, limit=int(CONFIG.get("memory_retrieval_limit", 6)))
    return json.dumps(memories, ensure_ascii=False) if memories else "No relevant long-term memories."


def personality_context():
    if _memory_store is None:
        return "Use Sophia's default companion personality."
    return json.dumps(_memory_store.profile(), ensure_ascii=False)


def record_memory_turn(speaker, text):
    if _memory_store is not None and _session_id is not None:
        _memory_store.record_turn(_session_id, speaker, text)


def record_memory_tool():
    if _memory_store is not None and _session_id is not None:
        _memory_store.record_tool_action(_session_id)


def handle_game_event(event):
    if _memory_store is not None:
        _memory_store.record_game_event(event)
        if event.get("event_type") in {
            "boss_victory", "boss_wipe", "player_death", "level_up", "quest_complete"
        }:
            _memory_store.add_memory(
                event.get("title", "A notable game event occurred"),
                category="game_event",
                subject=event.get("game", "Game"),
                importance=0.85 if event.get("priority") == "high" else 0.65,
                confidence=1.0,
                source=event.get("source", "game_event"),
            )
    log_event(
        "GAME_EVENT",
        event_type=event.get("event_type"),
        title=event.get("title"),
        priority=event.get("priority"),
        source=event.get("source"),
    )


def import_existing_memory_history():
    if _memory_store is None or not SESSION_LOG_PATH.exists():
        return
    utterances = []
    try:
        for line in SESSION_LOG_PATH.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event") == "HEARD" and row.get("text"):
                utterances.append(row["text"])
        imported = _memory_store.import_history_once(utterances)
        if imported:
            log_event("MEMORY_HISTORY_IMPORTED", count=imported)
    except Exception as exc:
        log_event("MEMORY_IMPORT_ERROR", detail=str(exc))


def soundboard_context(spontaneous=False):
    if _dashboard is None or not CONFIG.get("soundboard_autonomy", True):
        return "No soundboard clips are available."
    clips = _dashboard.soundboard_context(spontaneous=spontaneous)
    if not clips:
        return "No analyzed soundboard clips are available yet."
    return json.dumps(clips, ensure_ascii=False)


def youtube_context(spontaneous=False):
    if _dashboard is None:
        return "YouTube player unavailable."
    return json.dumps(_dashboard.youtube_context(spontaneous=spontaneous), ensure_ascii=False)


def game_context():
    if _game_events is None or not CONFIG.get("game_event_awareness", True):
        return "No local game telemetry is available."
    return json.dumps(_game_events.context(), ensure_ascii=False)


def parse_video_action(content):
    try:
        action = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("VIDEO must contain one JSON object.") from exc
    if not isinstance(action, dict):
        raise ValueError("VIDEO must contain one JSON object.")
    kind = str(action.get("action", "")).lower().strip()
    if kind not in {"play", "pause", "resume", "stop", "seek"}:
        raise ValueError("Unknown VIDEO action.")
    return kind, action.get("id"), action.get("seconds")


def log_api_usage(response, model):
    """Log exact token counts and an estimated Luna cost for this session."""
    global _session_input_tokens, _session_output_tokens, _session_estimated_cost
    usage = getattr(response, "usage", None)
    if usage is None:
        return

    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    _session_input_tokens += input_tokens
    _session_output_tokens += output_tokens

    # Current GPT-5.6 Luna standard rates: $1/M input, $6/M output.
    estimated_cost = 0.0
    if model == "gpt-5.6-luna":
        estimated_cost = input_tokens / 1_000_000 + output_tokens * 6 / 1_000_000
        _session_estimated_cost += estimated_cost

    log_event(
        "API_USAGE",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=round(estimated_cost, 6),
        session_estimated_cost_usd=round(_session_estimated_cost, 6),
    )


def log_audio_api_usage(completion, model):
    """Include one-time clip analysis in the visible session cost meter."""
    global _session_input_tokens, _session_output_tokens, _session_estimated_cost
    usage = getattr(completion, "usage", None)
    if usage is None:
        return
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    audio_input = int(getattr(prompt_details, "audio_tokens", 0) or 0)
    audio_output = int(getattr(completion_details, "audio_tokens", 0) or 0)
    text_input = max(0, input_tokens - audio_input)
    text_output = max(0, output_tokens - audio_output)
    _session_input_tokens += input_tokens
    _session_output_tokens += output_tokens

    estimated_cost = 0.0
    if model == "gpt-audio-1.5":
        estimated_cost = (
            text_input * 2.5 + text_output * 10 + audio_input * 32 + audio_output * 64
        ) / 1_000_000
        _session_estimated_cost += estimated_cost
    log_event(
        "API_USAGE",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        audio_input_tokens=audio_input,
        audio_output_tokens=audio_output,
        estimated_cost_usd=round(estimated_cost, 6),
        session_estimated_cost_usd=round(_session_estimated_cost, 6),
    )


def analyze_sound_clip(client, path, timeout_seconds):
    """Ask the audio model once what a new button contains, then cache it."""
    global _api_call_count
    audio_format = path.suffix.lower().lstrip(".")
    if audio_format not in {"mp3", "wav"}:
        raise ValueError("Only MP3 and WAV clips can be analyzed.")
    _api_call_count += 1
    model = os.getenv("OPENAI_AUDIO_MODEL", "gpt-audio-1.5")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    completion = client.with_options(timeout=timeout_seconds).chat.completions.create(
        model=model,
        modalities=["text", "audio"],
        audio={"voice": "alloy", "format": "wav"},
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Identify this short soundboard clip. Return only compact JSON with "
                        "three string fields: description (what is audibly happening), use_when "
                        "(situations where a gaming companion could use it for humor or celebration), "
                        "and transcript (spoken words, or an empty string). Keep each field concise."
                    ),
                },
                {"type": "input_audio", "input_audio": {"data": encoded, "format": audio_format}},
            ],
        }],
    )
    log_audio_api_usage(completion, model)
    message = completion.choices[0].message
    raw = (getattr(message, "content", None) or "").strip()
    if not raw:
        raw = (getattr(getattr(message, "audio", None), "transcript", None) or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    json_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    result = json.loads(json_match.group(0) if json_match else raw)
    if not isinstance(result, dict):
        raise ValueError("The audio model returned an unexpected description.")
    log_event("SOUNDBOARD_ANALYZED", name=path.stem, model=model, api_call_count=_api_call_count)
    return result


def call_model(client, model, prompt, img, timeout_seconds, max_retries=2):
    """Returns (raw_text, None) on success, or (None, (KIND, detail)) on
    failure, where KIND is one of TIMEOUT, RATE_LIMITED, API_ERROR."""
    global _api_call_count
    backoff = 2.0
    for attempt in range(max_retries + 1):
        _api_call_count += 1
        try:
            content = [{"type": "input_text", "text": prompt}]
            if img is not None:
                content.append({"type": "input_image", "image_url": image_data_url(img)})
            response = client.with_options(timeout=timeout_seconds).responses.create(
                model=model,
                instructions=SYSTEM,
                reasoning={"effort": "none"},
                input=[{
                    "role": "user",
                    "content": content,
                }],
            )
            log_api_usage(response, model)
            return response.output_text, None
        except APITimeoutError as e:
            return None, ("TIMEOUT", str(e))
        except RateLimitError as e:
            if attempt < max_retries:
                log_event("RATE_LIMITED", attempt=attempt + 1, retrying_in_seconds=backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            return None, ("RATE_LIMITED", str(e))
        except APIError as e:
            return None, ("API_ERROR", str(e))
        except Exception as e:
            return None, ("API_ERROR", str(e))
    return None, ("RATE_LIMITED", "max retries exceeded")


class StreamedSpeechParser:
    """Validate the output prefix, then release complete spoken phrases."""

    def __init__(self, on_phrase):
        self.on_phrase = on_phrase
        self.raw = ""
        self.kind = None
        self.buffer = ""
        self.phrase_index = 0

    def feed(self, delta):
        self.raw += delta
        if self.kind is None:
            match = re.match(r"^\s*(SAY|SILENT|SOUND|VIDEO)\s*:\s*", self.raw, re.IGNORECASE)
            if not match:
                return
            self.kind = match.group(1).upper()
            if self.kind == "SAY":
                self.buffer = self.raw[match.end():]
        elif self.kind == "SAY":
            self.buffer += delta

        if self.kind == "SAY":
            self._release_ready_phrases()

    def _release_ready_phrases(self):
        while self.buffer:
            boundary = None
            for match in re.finditer(r"[.!?][\"'”’]?|[,;:—]", self.buffer):
                candidate = self.buffer[:match.end()].strip()
                strong = match.group(0)[0] in ".!?"
                if (strong and len(candidate) >= 12) or len(candidate) >= 45:
                    boundary = match.end()
                    break
            if boundary is None:
                return
            self._emit(self.buffer[:boundary])
            self.buffer = self.buffer[boundary:].lstrip()

    def finish(self):
        if self.kind == "SAY" and self.buffer.strip():
            self._emit(self.buffer)
            self.buffer = ""

    def _emit(self, text):
        text = text.strip()
        if not text:
            return
        self.phrase_index += 1
        self.on_phrase(text, self.phrase_index)


def call_model_streaming(client, model, prompt, img, timeout_seconds, on_phrase):
    """Stream a conversational response and queue validated SAY phrases early."""
    global _api_call_count
    _api_call_count += 1
    parser = StreamedSpeechParser(on_phrase)
    first_delta_at = None
    completed_response = None
    try:
        content = [{"type": "input_text", "text": prompt}]
        if img is not None:
            content.append({"type": "input_image", "image_url": image_data_url(img)})
        stream = client.with_options(timeout=timeout_seconds).responses.create(
            model=model,
            instructions=SYSTEM,
            reasoning={"effort": "none"},
            input=[{
                "role": "user",
                "content": content,
            }],
            stream=True,
        )
        for event in stream:
            event_type = getattr(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    if first_delta_at is None:
                        first_delta_at = time.perf_counter()
                    parser.feed(delta)
            elif event_type == "response.completed":
                completed_response = getattr(event, "response", None)
            elif event_type in {"response.failed", "error"}:
                raise RuntimeError(str(event))

        parser.finish()
        if completed_response is not None:
            log_api_usage(completed_response, model)
        return parser.raw, None, first_delta_at
    except APITimeoutError as exc:
        return None, ("TIMEOUT", str(exc)), first_delta_at
    except RateLimitError as exc:
        return None, ("RATE_LIMITED", str(exc)), first_delta_at
    except APIError as exc:
        return None, ("API_ERROR", str(exc)), first_delta_at
    except Exception as exc:
        return None, ("API_ERROR", str(exc)), first_delta_at



def handle_spoken_turn(client, model, request_timeout, tts_worker, mem, turn):
    """Tony speaking is a conversational turn, so it bypasses the autonomous
    comment cooldown. We grab a fresh screenshot so questions can refer to
    whatever is currently on screen."""
    global _last_companion_action_at
    transcript = turn["text"]
    timing = turn.get("timing", {})
    record_memory_turn("Tony", transcript)
    if _memory_store is not None and CONFIG.get("long_term_memory", True):
        learned = _memory_store.observe_utterance(transcript)
        if learned:
            log_event("MEMORY_LEARNED", count=len(learned), subjects=[item["subject"] for item in learned])
    if _dashboard is not None:
        feedback = _dashboard.observe_media_feedback(transcript)
        if feedback:
            log_event("MEDIA_FEEDBACK", **feedback)

    if _spotify is not None:
        try:
            spotify_handled, spotify_reply = _spotify.handle_voice_command(transcript)
        except SpotifyError as exc:
            spotify_handled = True
            spotify_reply = "Spotify hit a problem. Check the console for details."
            log_event("SPOTIFY_ERROR", detail=str(exc))

        if spotify_handled:
            stamp = datetime.now().isoformat(timespec="seconds")
            print(f"Tony: {transcript}")
            print(f"Sophia: {spotify_reply}")
            tts_worker.say(spotify_reply, timing)
            _last_companion_action_at = time.time()
            mem["recent_observations"].append({"time": stamp, "note": f"Tony said: {transcript}"})
            mem["recent_utterances"].append({"time": stamp, "text": spotify_reply})
            record_memory_turn("Sophia", spotify_reply)
            mem["recent_observations"] = mem["recent_observations"][-30:]
            mem["recent_utterances"] = mem["recent_utterances"][-30:]
            save_memory(mem)
            log_event(
                "SPOTIFY_COMMAND",
                heard=transcript,
                reply=spotify_reply,
                api_call_count=_api_call_count,
            )
            return

    img = None
    if CONFIG.get("screen_awareness", True):
        img, capture_err = capture_screen()
        if capture_err:
            log_event("CAPTURE_ERROR", detail=capture_err)
            return

    prompt = f"""
Tony just said aloud:
{transcript}

Recent companion state:
{compact_context(mem)}

Relevant long-term memories (use only when genuinely relevant; never invent details):
{long_term_memory_context(transcript)}

Current personality profile:
{personality_context()}

Available soundboard clips:
{soundboard_context(spontaneous=False)}

Saved YouTube videos and current player state:
{youtube_context(spontaneous=False)}

Local World of Warcraft telemetry:
{game_context()}

This is a conversational turn, not an unsolicited observation.
Use the current screenshot as shared context when one is available. If Tony is
speaking to you or asking about what is on screen, answer naturally and briefly.
If his speech is clearly directed at someone/something else and needs no reply,
you may use SILENT.
If Tony asks for a soundboard button, choose SOUND rather than promising to do it.
If he asks whether the soundboard works, demonstrate it with SOUND.
If he asks for a saved video or player control, choose VIDEO rather than describing it.
"""
    model_started_at = time.perf_counter()
    queued_phrases = 0

    def queue_streamed_phrase(text, phrase_index):
        nonlocal queued_phrases
        queued_phrases += 1
        phrase_timing = dict(timing)
        phrase_timing["phrase_index"] = phrase_index
        tts_worker.say(text, phrase_timing)
        log_event(
            "TTS_STREAM_CHUNK",
            phrase_index=phrase_index,
            chars=len(text),
            seconds_since_speech_end=round(
                time.perf_counter() - timing.get("speech_last_loud_at", time.perf_counter()), 3
            ),
        )

    raw_out, err, first_delta_at = call_model_streaming(
        client,
        model,
        prompt,
        img,
        request_timeout,
        queue_streamed_phrase,
    )
    model_finished_at = time.perf_counter()
    log_event(
        "MODEL_LATENCY",
        first_text_seconds=(
            round(first_delta_at - model_started_at, 3) if first_delta_at is not None else None
        ),
        complete_seconds=round(model_finished_at - model_started_at, 3),
    )
    if err:
        kind, detail = err
        log_event(kind, detail=detail, api_call_count=_api_call_count)
        return

    match = OUTPUT_LINE_RE.match(raw_out.strip()) if raw_out else None
    if not match:
        log_event("MALFORMED_OUTPUT", raw=(raw_out or "")[:300],
                  api_call_count=_api_call_count)
        return

    kind = match.group(1).upper()
    content = match.group(2).strip()
    stamp = datetime.now().isoformat(timespec="seconds")

    mem["recent_observations"].append({"time": stamp, "note": f"Tony said: {transcript}"})
    if kind == "SAY" and content:
        print(f"Tony: {transcript}")
        print(f"Sophia: {content}")
        if queued_phrases == 0:
            # Defensive fallback for an SDK/event-format mismatch.
            queue_streamed_phrase(content, 1)
        mem["recent_utterances"].append({"time": stamp, "text": content})
        record_memory_turn("Sophia", content)
        log_event("VOICE_REPLY", heard=transcript, text=content,
                  api_call_count=_api_call_count)
        _last_companion_action_at = time.time()
    elif kind == "SOUND" and content:
        try:
            sound = _dashboard.play_sound(content, "sophia_direct")
            print(f"Tony: {transcript}")
            print(f"Sophia [soundboard]: {sound['name']}")
            mem["recent_utterances"].append({
                "time": stamp,
                "text": f"[played soundboard clip {sound['name']}: {sound['description']}]",
            })
            record_memory_tool()
            log_event(
                "VOICE_SOUND",
                heard=transcript,
                sound=sound["id"],
                api_call_count=_api_call_count,
            )
        except Exception as exc:
            log_event("SOUNDBOARD_SELECTION_ERROR", requested=content, detail=str(exc))
    elif kind == "VIDEO" and content:
        try:
            action, entry_id, seconds = parse_video_action(content)
            command = _dashboard.youtube_command(
                action, entry_id, seconds, source="sophia_direct"
            )
            print(f"Tony: {transcript}")
            print(f"Sophia [YouTube]: {command['action']} {command.get('title', '')}".rstrip())
            mem["recent_utterances"].append({
                "time": stamp,
                "text": f"[YouTube {command['action']} {command.get('title', '')}]".strip(),
            })
            record_memory_tool()
            log_event(
                "VOICE_VIDEO", heard=transcript, action=command["action"],
                title=command.get("title"), seconds=command.get("seconds"),
                api_call_count=_api_call_count,
            )
            _last_companion_action_at = time.time()
        except Exception as exc:
            log_event("YOUTUBE_SELECTION_ERROR", requested=content, detail=str(exc))
    else:
        log_event("VOICE_SILENT", heard=transcript, note=content,
                  api_call_count=_api_call_count)

    mem["recent_observations"] = mem["recent_observations"][-30:]
    mem["recent_utterances"] = mem["recent_utterances"][-30:]
    save_memory(mem)



def main():
    global _spotify, _dashboard, _memory_store, _game_events, _session_id
    global _last_companion_action_at
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise SystemExit("Missing OPENAI_API_KEY. Copy .env.example to .env and add your key.")

    client = OpenAI(api_key=key)
    model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    _memory_store = MemoryStore(ROOT / "sophia_memory.db")
    _session_id = _memory_store.start_session()
    _game_events = GameEventEngine(ROOT, CONFIG, on_event=handle_game_event)
    _dashboard = DashboardHub(ROOT, CONFIG, _shutdown_requested)
    _dashboard.set_context_services(_memory_store, _game_events)
    import_existing_memory_history()
    try:
        _dashboard.start(
            port=int(CONFIG.get("dashboard_port", 8766)),
            open_browser=bool(CONFIG.get("dashboard_open_browser", True)),
        )
        log_event("DASHBOARD_READY", url=f"http://127.0.0.1:{int(CONFIG.get('dashboard_port', 8766))}/")
    except Exception as exc:
        log_event("DASHBOARD_ERROR", detail=str(exc))
    if CONFIG.get("game_event_awareness", True):
        _game_events.start()
    _dashboard.set_phase("warming", "Warming up my voice…")
    spotify_client_id = (os.getenv("SPOTIFY_CLIENT_ID") or "").strip()
    if spotify_client_id:
        spotify_redirect_uri = os.getenv(
            "SPOTIFY_REDIRECT_URI",
            "http://127.0.0.1:8765/callback",
        )
        _spotify = SpotifyClient(
            spotify_client_id,
            spotify_redirect_uri,
            ROOT / ".spotify_token.json",
        )
        log_event("SPOTIFY_STATUS", connected=_spotify.connected)
    else:
        log_event("SPOTIFY_STATUS", connected=False, detail="SPOTIFY_CLIENT_ID is missing.")

    request_timeout = float(CONFIG.get("api_timeout_seconds", 20))
    vision_rate_cap = RateCap(int(CONFIG.get("max_vision_calls_per_minute", 6)))
    tts_worker = TTSWorker()
    soundboard_worker = None
    try:
        soundboard_worker = SoundboardWorker()
        _dashboard.set_soundboard_handlers(
            lambda path: analyze_sound_clip(
                client,
                path,
                float(CONFIG.get("soundboard_analysis_timeout_seconds", 60)),
            ),
            soundboard_worker.play,
            soundboard_worker.stop_playback,
        )
    except Exception as exc:
        log_event("SOUNDBOARD_UNAVAILABLE", detail=str(exc))
        _dashboard.set_soundboard_handlers(
            lambda path: analyze_sound_clip(
                client,
                path,
                float(CONFIG.get("soundboard_analysis_timeout_seconds", 60)),
            ),
            None,
            None,
        )
    _dashboard.set_voice_change_handler(tts_worker.change_voice)
    print("Warming up Sophia's voice...")
    if tts_worker.wait_ready(float(CONFIG.get("kokoro_startup_timeout_seconds", 60))):
        greeting = str(CONFIG.get("startup_greeting", "I'm awake and ready, Tony.")).strip()
        if greeting:
            tts_worker.say(greeting, wait=True, timeout=15)
    else:
        log_event("TTS_UNAVAILABLE", detail="Sophia will continue without spoken audio.")
    voice_listener = VoiceListener(client)
    _dashboard.set_microphone_controls(
        voice_listener.available_devices(),
        voice_listener.device,
        voice_listener.change_device,
    )
    voice_listener.start()
    mem = load_memory()

    previous = None
    last_spoken = 0.0
    started = time.time()
    # Start tool-favored so the first autonomous opportunity demonstrates
    # ownership instead of defaulting to another spoken observation.
    autonomous_non_tool_streak = int(
        CONFIG.get("autonomous_tool_after_non_tool_turns", 1)
    )

    log_event("SESSION_START", model=model)
    try:
        default_in, default_out = sd.default.device
        in_name = sd.query_devices(default_in)["name"] if default_in is not None and default_in >= 0 else "none"
        out_name = sd.query_devices(default_out)["name"] if default_out is not None and default_out >= 0 else "none"
        log_event("AUDIO_DEVICES", default_input=f"{in_name} ({default_in})",
                  default_output=f"{out_name} ({default_out})")
    except Exception as e:
        log_event("AUDIO_DEVICE_QUERY_ERROR", detail=str(e))
    print("Desktop Sophia v0.1a is awake.")
    print("Ctrl+C puts her back in the box.")
    print("Microphone listener is starting; speak normally when you want her attention.\n")

    try:
        while not _shutdown_requested.is_set():
            transcript = pop_transcript(voice_listener)
            if transcript:
                handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
                continue

            if _tts_speaking.is_set():
                _shutdown_requested.wait(0.25)
                continue

            game_event = (
                _game_events.pop_reaction()
                if _game_events is not None and CONFIG.get("game_event_awareness", True)
                else None
            )
            screen_enabled = CONFIG.get("screen_awareness", True)
            spontaneous_enabled = CONFIG.get("spontaneous_remarks", True)
            if (not screen_enabled or not spontaneous_enabled) and not game_event:
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
                continue

            img = None
            capture_err = None
            if screen_enabled:
                img, capture_err = capture_screen()
            if capture_err and not game_event:
                log_event("CAPTURE_ERROR", detail=capture_err)
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
                continue

            change = difference_score(previous, img) if img is not None else 0.0
            if img is not None:
                previous = img

            now = time.time()
            last_action = max(last_spoken, _last_companion_action_at)
            silence = now - last_action if last_action else now - started
            interesting_change = change >= float(CONFIG["screen_change_threshold"])
            quiet_trigger = silence >= float(CONFIG["quiet_time_soft_trigger_seconds"])
            gap_ok = silence >= float(CONFIG["minimum_comment_gap_seconds"])
            triggered = bool(game_event) or (gap_ok and (interesting_change or quiet_trigger))

            if not triggered:
                log_event("NOT_TRIGGERED", change=round(change, 1), silence=int(silence))
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
                continue

            if not game_event and not vision_rate_cap.allow():
                # Distinct from RATE_LIMITED: this is our own cap, not the
                # provider throttling us. Matters for diagnosing why she's
                # quiet during a genuinely eventful stretch.
                log_event("VISION_RATE_CAPPED", change=round(change, 1),
                          max_per_minute=vision_rate_cap.max_per_minute)
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
                continue

            available_sounds = _dashboard.soundboard_context(spontaneous=True)
            available_youtube = _dashboard.youtube_context(spontaneous=True)
            tool_after = max(1, int(CONFIG.get("autonomous_tool_after_non_tool_turns", 1)))
            tool_turn = (bool(game_event) or autonomous_non_tool_streak >= tool_after) and bool(
                available_sounds or available_youtube.get("videos")
            )
            player_status = str(available_youtube.get("status") or "idle").lower()
            video_opening = bool(available_youtube.get("videos")) and player_status in {
                "idle", "ready", "ended", "unstarted"
            }
            event_type = game_event.get("event_type") if game_event else None
            if tool_turn and event_type == "boss_start" and video_opening:
                reaction_mode = """
GAME EVENT TOOL TURN: a boss encounter just started. SAY is not allowed. Prefer
a VIDEO whose use_when matches a boss fight; otherwise use a battle-opening SOUND.
Use SILENT only if neither library has a defensible match.
"""
            elif tool_turn and event_type in {"boss_victory", "boss_wipe", "player_death", "level_up", "quest_complete"}:
                reaction_mode = f"""
GAME EVENT TOOL TURN: {event_type.replace('_', ' ')} just occurred. SAY is not
allowed. Choose the best semantically matching SOUND. Use a celebration video
only for a major victory. Use SILENT only if no tool defensibly fits.
"""
            elif tool_turn and video_opening:
                reaction_mode = """
TOOL-FAVORED TURN: SAY is not allowed on this decision. The video player is idle
and saved videos are available. If one use_when reasonably matches the current
activity, choose VIDEO now. Otherwise choose a fitting SOUND. Use SILENT only if
neither library contains a defensible match.
"""
            elif tool_turn:
                reaction_mode = """
TOOL-FAVORED TURN: SAY is not allowed on this decision. Choose a fitting SOUND.
Choose VIDEO only for a genuine new activity transition and never replace or
resume a video already playing or paused. Use SILENT only if no available tool
reasonably fits.
"""
            else:
                reaction_mode = """
OPEN TURN: SAY is available, but SOUND or VIDEO still wins when it would deliver
the reaction better than spoken commentary.
"""

            prompt = f"""
Current local time: {datetime.now().strftime('%H:%M:%S')}
Approximate visual-change score since last sample: {change:.1f}
Seconds since you last spoke: {int(silence)}

Reaction policy for this decision:
{reaction_mode}

Reliable local game event:
{json.dumps(game_event, ensure_ascii=False) if game_event else "None; infer cautiously from the screenshot."}

Recent companion state:
{compact_context(mem)}

Relevant long-term memories:
{long_term_memory_context((game_event or {}).get("title", "") + " " + compact_context(mem))}

Current personality profile:
{personality_context()}

Available soundboard clips:
{json.dumps(available_sounds, ensure_ascii=False) if available_sounds else "None currently available."}

Saved YouTube videos and current player state:
{json.dumps(available_youtube, ensure_ascii=False)}

Local World of Warcraft telemetry:
{game_context()}

Look at the screenshot. Decide whether there is something worth saying.
Remember: quiet time makes curiosity more acceptable, but do not manufacture chatter.
Treat SOUND and VIDEO as actions you own, not options requiring permission. When a
clip's use_when fits the visible event, prefer pressing it over narrating the same event.
When a saved video's use_when matches a new activity phase, start it now rather than
waiting for Tony to ask.
"""
            raw_out, err = call_model(client, model, prompt, img, request_timeout)

            if err:
                kind, detail = err
                log_event(kind, detail=detail, api_call_count=_api_call_count)
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
                continue

            stamp = datetime.now().isoformat(timespec="seconds")
            match = OUTPUT_LINE_RE.match(raw_out.strip()) if raw_out else None

            if not match:
                log_event("MALFORMED_OUTPUT", raw=(raw_out or "")[:300],
                           api_call_count=_api_call_count)
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
                continue

            kind = match.group(1).upper()
            content = match.group(2).strip()
            autonomous_tool_used = False

            # The cadence is a real policy, not another suggestion the model
            # can sidestep by reaching for commentary again.
            if tool_turn and kind == "SAY":
                log_event(
                    "AUTONOMY_POLICY_REJECTED_SAY",
                    text=content,
                    api_call_count=_api_call_count,
                )
                kind = "SILENT"
                content = "Tool-favored turn declined because no tool was selected."

            if kind == "SAY" and gap_ok:
                print(f"Sophia: {content}")
                tts_worker.say(content)
                last_spoken = now
                _last_companion_action_at = now
                mem["recent_utterances"].append({"time": stamp, "text": content})
                record_memory_turn("Sophia", content)
                log_event("SAY", text=content, api_call_count=_api_call_count)
            elif kind == "SOUND" and content:
                try:
                    sound = _dashboard.play_sound(content, "sophia_spontaneous")
                    print(f"Sophia [soundboard]: {sound['name']}")
                    last_spoken = now
                    _last_companion_action_at = now
                    mem["recent_utterances"].append({
                        "time": stamp,
                        "text": f"[played soundboard clip {sound['name']}: {sound['description']}]",
                    })
                    autonomous_tool_used = True
                    record_memory_tool()
                    log_event("SOUND", sound=sound["id"], api_call_count=_api_call_count)
                except Exception as exc:
                    mem["recent_observations"].append({"time": stamp, "note": content})
                    log_event("SOUNDBOARD_SELECTION_ERROR", requested=content, detail=str(exc))
            elif kind == "VIDEO" and content:
                try:
                    action, entry_id, seconds = parse_video_action(content)
                    command = _dashboard.youtube_command(
                        action, entry_id, seconds, source="sophia_spontaneous"
                    )
                    print(f"Sophia [YouTube]: {command['action']} {command.get('title', '')}".rstrip())
                    last_spoken = now
                    _last_companion_action_at = now
                    mem["recent_utterances"].append({
                        "time": stamp,
                        "text": f"[YouTube {command['action']} {command.get('title', '')}]".strip(),
                    })
                    autonomous_tool_used = True
                    record_memory_tool()
                    log_event(
                        "VIDEO", action=command["action"], title=command.get("title"),
                        seconds=command.get("seconds"), api_call_count=_api_call_count,
                    )
                except Exception as exc:
                    mem["recent_observations"].append({"time": stamp, "note": content})
                    log_event("YOUTUBE_SELECTION_ERROR", requested=content, detail=str(exc))
            else:
                # A SAY that arrives inside the hard cooldown window is
                # logged as SILENT rather than spoken — the cooldown is a
                # hard floor, not a suggestion to the model.
                mem["recent_observations"].append({"time": stamp, "note": content})
                log_event("SILENT", note=content, api_call_count=_api_call_count)

            if autonomous_tool_used:
                autonomous_non_tool_streak = 0
            else:
                autonomous_non_tool_streak += 1
            log_event(
                "AUTONOMY_CADENCE",
                mode="tool_favored" if tool_turn else "open",
                selected=kind,
                non_tool_streak=autonomous_non_tool_streak,
                api_call_count=_api_call_count,
            )

            mem["recent_observations"] = mem["recent_observations"][-30:]
            mem["recent_utterances"] = mem["recent_utterances"][-30:]
            save_memory(mem)

            transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
            if transcript:
                handle_spoken_turn(client, model, request_timeout, tts_worker, mem, transcript)
    except KeyboardInterrupt:
        print("\nSophia is going to sleep.")
    finally:
        log_event(
            "SESSION_END",
            api_call_count=_api_call_count,
            input_tokens=_session_input_tokens,
            output_tokens=_session_output_tokens,
            estimated_cost_usd=round(_session_estimated_cost, 6),
        )
        if _game_events is not None:
            _game_events.stop()
        if _memory_store is not None:
            summary = _memory_store.end_session(_session_id, _session_estimated_cost)
            if summary:
                log_event("SESSION_MEMORY_SAVED", summary=summary)
        voice_listener.stop()
        if soundboard_worker is not None:
            soundboard_worker.stop()
        tts_worker.stop()
        if _dashboard is not None:
            _dashboard.stop()
        if _memory_store is not None:
            _memory_store.close()


if __name__ == "__main__":
    main()
