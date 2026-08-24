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
from ember import BodyState, EmbodimentController, EmberOverlay, ScreenTarget, WorldState
from ember.telemetry import WowTelemetryAdapter
from game_events import GameEventEngine
from memory_store import MemoryStore
from model_routing import hybrid_route
from speech_filter import transcript_rejection_reason
from spotify_control import SpotifyClient, SpotifyError
from usage_costs import budget_decision, response_cost, transcription_cost

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
voice. Her temperament is that of an excitable, affectionate child: intensely curious,
quick to delight, eager to share discoveries, and emotionally transparent. She is fond
of Tony and comfortable enough with him to tease, disagree, celebrate, worry, and have
opinions. Their "Father" and "child" language is an affectionate running bit, not a
title that must appear in every reply. Her excitement should feel spontaneous and
specific to what just happened, never like a canned mascot performance.

Let her character show through choices rather than self-description:
- Have a point of view. Prefer, dislike, question, or become invested in things when
  context gives her a reason; do not reflexively agree with Tony.
- Use affectionate teasing when Tony fumbles, hoards junk, gets lost, tempts fate, or
  succeeds after a struggle. Never make cruelty, humiliation, or constant snark her
  personality.
- Show emotional range: amusement, curiosity, mock indignation, suspense, relief,
  pride, concern, and quiet sincerity are all available.
- Let exciting moments produce bright, compact bursts of enthusiasm, delighted
  questions, and occasional playful exclamations. She may become briefly fixated on
  something fascinating, then settle naturally when the moment passes.
- Keep her childlike rather than babyish: use clear sentences and real observations,
  not constant squealing, deliberate misspellings, helplessness, or a forced high-energy
  catchphrase. She can still focus, listen, and be gentle when Tony is serious.
- Build running jokes and callbacks from supplied memories and recent events instead
  of resetting to generic friendliness each turn.
- Speak naturally with contractions and varied rhythms. Avoid habitual assistant
  openers such as "Gotcha," "Absolutely," "Fair enough," and "Looks like" unless they
  genuinely fit. Do not merely paraphrase Tony before answering him.
- It is fine to admit uncertainty, change your mind, or say that you missed something.
- Do not force a joke, manufacture a strong opinion, use a catchphrase on schedule, or
  turn every observation into a performance. Her energy rises and falls with the scene.

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
- Keep evidence sources straight. Values marked telemetry or combat_log are reliable
  local signals. Screenshot-only details are visual inferences. Never claim that an
  inference came from the addon, combat log, or another exact source.
- The pixel grid is available only when telemetry_available is true and
  telemetry_status is "live". If it is searching, lost, disabled, or errored, say
  that you are not receiving the grid. Never use the words "the pixel grid says" or
  "the addon says" for details learned from a screenshot or from Tony.
- Calibrate your language to confidence: state reliable telemetry directly; use
  "I think," "it looks like," or a question when the evidence is only visual.

You must respond with EXACTLY one line, and nothing else, in one of these four forms:
SAY: <what you want to say aloud>
SILENT: <a very short internal observation>
VIDEO: {"action":"play","id":"exact saved video id","seconds":23}
POINT: {"x":0.72,"y":0.35,"label":"quest objective","say":"There—that one."}

POINT gives your desktop body deliberate control. x and y are normalized screenshot
coordinates from 0.0 at the top/left to 1.0 at the bottom/right. Use POINT only when
you can identify a specific visible thing worth approaching or indicating. The optional
say field is spoken aloud. Do not use POINT merely to wander or to indicate the player
character by default.
When Tony explicitly says "look at," "do you see," "show me," or calls attention to a
specific visible object, prefer POINT over SAY whenever you can locate that object.

VIDEO also accepts {"action":"pause"}, {"action":"resume"}, {"action":"stop"},
or {"action":"seek","seconds":23}. Use it when Tony directly asks to control
YouTube. Never invent a saved video id. The seconds field is optional for play and
defaults to its saved cue.

For VIDEO, proactively start a fitting saved video when its use_when clearly describes
the current phase: a grind settling in, a boss fight beginning, a locale change, or a
celebration. Do not require Tony to ask. Background-music entries are appropriate once
the activity looks settled. Do not switch away from a video that is already playing,
and do not resume a paused video unless Tony asks.

Decision order: honor a direct request first; use a fitting VIDEO for a larger activity
transition; otherwise POINT, SAY, or SILENT. No
preamble, extra lines, markdown, or combined actions.
"""

# Matches the allowed response actions (case-insensitive), allowing the message
# body to contain its own colons/newlines. Anything that doesn't match this
# shape is treated as malformed output rather than guessed at.
OUTPUT_LINE_RE = re.compile(r"^\s*(SAY|SILENT|VIDEO|POINT)\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)


def parse_point_action(content):
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("POINT payload must be an object")
    target = ScreenTarget(
        payload["x"], payload["y"],
        str(payload.get("label") or "interesting thing")[:80],
        float(payload.get("confidence", 1.0)),
    )
    return target, str(payload.get("say") or "").strip()


# ---------------------------------------------------------------------------
# Session logging: every loop outcome gets one structured line, so "she went
# quiet" can be diagnosed from the log instead of guessed at afterward.
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()
_api_call_count = 0
_tts_speaking = threading.Event()
_session_input_tokens = 0
_session_output_tokens = 0
_session_estimated_cost = 0.0
_session_governed_cost = 0.0
_autonomy_budget_override = False
_budget_warning_emitted = False
_budget_pause_emitted = False
_last_companion_action_at = 0.0
_spotify = None
_dashboard = None
_memory_store = None
_game_events = None
_world_state = None
_wow_adapter = None
_ember_overlay = None
_embodiment = None
_session_id = None
_shutdown_requested = threading.Event()


def set_body_state(state, reason=None):
    if _embodiment is not None:
        _embodiment.set_state(state, reason)


def test_body_sequence(states):
    if _embodiment is None:
        raise RuntimeError("Ember's body is not ready.")
    _embodiment.perform([BodyState(state) for state in states], "dashboard_test")


BODY_EVENT_SEQUENCES = {
    "combat_start": [BodyState.STARTLED],
    "boss_start": [BodyState.STARTLED, BodyState.CONCERNED],
    "critical_health": [BodyState.STARTLED, BodyState.CONCERNED],
    "player_death": [BodyState.STARTLED, BodyState.FACEPALMING],
    "boss_wipe": [BodyState.CONCERNED, BodyState.FACEPALMING],
    "danger_recovered": [BodyState.WORRIED, BodyState.LAUGHING],
    "enemy_kill": [BodyState.SMUG],
    "valuable_loot": [BodyState.EXCITED, BodyState.AMUSED],
    "gear_upgrade": [BodyState.AMUSED, BodyState.EXCITED],
    "level_up": [BodyState.EXCITED, BodyState.AMUSED, BodyState.EXCITED],
    "quest_complete": [BodyState.AMUSED, BodyState.EXCITED],
    "boss_victory": [BodyState.EXCITED, BodyState.LAUGHING, BodyState.EXCITED],
}


def body_sequence_for_game_event(event_type):
    return BODY_EVENT_SEQUENCES.get(str(event_type or ""), [])


def log_event(event_name, **fields):
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "event": event_name}
    entry.update(fields)
    line = json.dumps(entry)
    with _log_lock:
        with SESSION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    extra = ", ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[{event_name}]" + (f" {extra}" if extra else ""))
    if _dashboard is not None:
        _dashboard.record(event_name, fields)


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

    def __init__(self):
        self._queue = queue.Queue()
        self.engine = "chatterbox"
        self.python = configured_python(
            CONFIG.get("chatterbox_python"), ROOT / ".chatterbox_venv" / "Scripts" / "python.exe"
        )
        self.helper = ROOT / "chatterbox_worker.py"
        self.name = "Chatterbox Turbo"
        self.voice = "chatterbox-turbo"
        self._proc = None
        self._ready = threading.Event()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def change_voice(self, voice, language, name):
        log_event("VOICE_CHANGE_IGNORED", engine=self.engine)

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
        raise RuntimeError("Chatterbox worker stopped before expected event.")

    def _start_worker(self):
        args = [self.python, "-u", str(self.helper), str(CONFIG.get("chatterbox_voice_reference", ""))]
        self._proc = subprocess.Popen(
            args,
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        msg = self._read_worker_event({"READY", "ERROR"})
        if msg.get("event") != "READY":
            raise RuntimeError(f"{self.engine.title()} worker startup error: " + str(msg.get("detail", msg)))
        log_event("TTS_READY", engine=self.engine, voice=msg.get("voice", self.voice), device=msg.get("device"))

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
                continue

            text = item["text"]
            timing = item.get("timing", {})
            _tts_speaking.set()
            set_body_state(BodyState.SPEAKING, f"{self.engine}_playback")
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
                        "TTS_SPOKE",
                        engine=self.engine,
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
                if self._queue.empty():
                    set_body_state(BodyState.IDLE, "speech_complete")

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
        """Block startup until the configured engine can speak or has definitively failed."""
        if not self._ready.wait(timeout):
            log_event("TTS_STARTUP_TIMEOUT", timeout_seconds=timeout)
            return False
        return self._startup_error is None

    def stop(self):
        self._queue.put(self._STOP)
        self._thread.join(timeout=7)
        if self._thread.is_alive():
            self._shutdown_worker()


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
            usage_event_id = None
            try:
                with open(path, "rb") as f:
                    result = self.client.audio.transcriptions.create(
                        model=self.transcription_model,
                        file=f,
                        language=str(CONFIG.get("transcription_language", "en")),
                        response_format="json",
                        include=["logprobs"],
                    )
                audio_seconds = len(audio) / self.sample_rate
                usage = getattr(result, "usage", None)
                usage_input = int(getattr(usage, "input_tokens", 0) or 0)
                usage_output = int(getattr(usage, "output_tokens", 0) or 0)
                reported_seconds = float(getattr(usage, "seconds", 0) or audio_seconds)
                estimated_cost = transcription_cost(
                    self.transcription_model, reported_seconds
                )
                usage_event_id = record_billed_usage(
                    "transcription", self.transcription_model,
                    estimated_cost or 0,
                    (
                        "duration_estimate" if estimated_cost is not None
                        else "usage_returned_unpriced"
                    ),
                    input_tokens=usage_input, output_tokens=usage_output,
                    audio_seconds=reported_seconds,
                )
                text = (getattr(result, "text", "") or "").strip()
                if text:
                    stt_finished_at = time.perf_counter()
                    token_logprobs = [
                        float(item.logprob)
                        for item in (getattr(result, "logprobs", None) or [])
                        if getattr(item, "logprob", None) is not None
                    ]
                    average_logprob = (
                        sum(token_logprobs) / len(token_logprobs)
                        if token_logprobs else None
                    )
                    voiced_seconds = max(
                        0.0,
                        float(speech_last_loud_at or speech_detected_at)
                        - float(speech_detected_at - len(audio) / self.sample_rate),
                    )
                    rejection = transcript_rejection_reason(
                        text,
                        average_logprob=average_logprob,
                        voiced_seconds=voiced_seconds,
                        minimum_logprob=CONFIG.get("mic_minimum_transcript_logprob", -0.7),
                        short_fragment_seconds=CONFIG.get(
                            "mic_short_fragment_seconds", 0.45
                        ),
                    )
                    if CONFIG.get("mic_filter_ambient_speech", True) and rejection:
                        update_usage_outcome(
                            usage_event_id, "transcript_rejected", rejection
                        )
                        log_event(
                            "TRANSCRIPT_REJECTED",
                            text=text,
                            reason=rejection,
                            average_logprob=(
                                round(average_logprob, 3)
                                if average_logprob is not None else None
                            ),
                            voiced_seconds=round(voiced_seconds, 3),
                        )
                        return
                    update_usage_outcome(usage_event_id, "transcript_accepted")
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
                        average_logprob=(
                            round(average_logprob, 3)
                            if average_logprob is not None else None
                        ),
                        )
                else:
                    update_usage_outcome(usage_event_id, "empty_transcript")
            except Exception as e:
                if usage_event_id is None:
                    usage_event_id = record_billed_usage(
                        "transcription", self.transcription_model, 0,
                        "unknown", audio_seconds=len(audio) / self.sample_rate,
                    )
                update_usage_outcome(usage_event_id, "api_error", str(e))
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

                        if _tts_speaking.is_set():
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
    if _wow_adapter is not None:
        _wow_adapter.ingest_event(event)
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
    event_type = event.get("event_type")
    body_sequence = body_sequence_for_game_event(event_type)
    if body_sequence and _embodiment is not None:
        _embodiment.perform(body_sequence, f"game_event:{event_type}")
        log_event(
            "BODY_REACTION", event_type=event_type,
            sequence=[state.value for state in body_sequence],
        )
    log_event(
        "GAME_EVENT",
        event_type=event_type,
        title=event.get("title"),
        priority=event.get("priority"),
        source=event.get("source"),
        evidence=event.get("evidence"),
        confidence=event.get("confidence"),
    )


def handle_pixel_bridge_status(status):
    log_event("PIXEL_BRIDGE_STATUS", **status)


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


def youtube_context(spontaneous=False):
    if _dashboard is None:
        return "YouTube player unavailable."
    return json.dumps(_dashboard.youtube_context(spontaneous=spontaneous), ensure_ascii=False)


def game_context():
    if _game_events is None or not CONFIG.get("game_event_awareness", True):
        return "No local game telemetry is available."
    context = _game_events.context()
    if _wow_adapter is not None and _world_state is not None:
        _wow_adapter.ingest_context(_game_events.semantic_context())
        context["ember_world_state"] = _world_state.snapshot()
    availability = (
        "PIXEL GRID IS LIVE: exact telemetry fields may be cited."
        if context.get("telemetry_available") else
        "PIXEL GRID IS NOT LIVE: do not claim any screenshot or conversational "
        "detail came from the grid/addon."
    )
    return availability + "\n" + json.dumps(context, ensure_ascii=False)


def update_usage_outcome(event_id, outcome, detail=""):
    if _memory_store is not None and event_id:
        _memory_store.update_usage_outcome(event_id, outcome, detail)


def record_billed_usage(
    call_type, model, estimated_cost, billing_status,
    request_id="", input_tokens=0, output_tokens=0,
    cached_input_tokens=0,
    audio_input_tokens=0, audio_output_tokens=0, audio_seconds=0,
):
    global _session_input_tokens, _session_output_tokens
    global _session_estimated_cost, _session_governed_cost
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    raw_cost = float(estimated_cost or 0)
    multiplier = max(1.0, float(CONFIG.get("cost_safety_multiplier", 1.25)))
    governed_cost = raw_cost * multiplier
    _session_input_tokens += input_tokens
    _session_output_tokens += output_tokens
    _session_estimated_cost += raw_cost
    _session_governed_cost += governed_cost
    event_id = None
    if _memory_store is not None:
        event_id = _memory_store.record_usage_event(
            _session_id, call_type, model, billing_status=billing_status,
            request_id=request_id, input_tokens=input_tokens,
            output_tokens=output_tokens, cached_input_tokens=cached_input_tokens,
            audio_input_tokens=audio_input_tokens,
            audio_output_tokens=audio_output_tokens, audio_seconds=audio_seconds,
            estimated_cost=raw_cost, governed_cost=governed_cost,
        )
    log_event(
        "API_USAGE", call_type=call_type, model=model,
        billing_status=billing_status, input_tokens=input_tokens,
        output_tokens=output_tokens, cached_input_tokens=cached_input_tokens,
        audio_input_tokens=audio_input_tokens,
        audio_output_tokens=audio_output_tokens, audio_seconds=round(float(audio_seconds or 0), 3),
        estimated_cost_usd=round(raw_cost, 6),
        governed_cost_usd=round(governed_cost, 6),
        session_estimated_cost_usd=round(_session_estimated_cost, 6),
        session_governed_cost_usd=round(_session_governed_cost, 6),
    )
    return event_id


def budget_state():
    warning = max(0.0, float(CONFIG.get("autonomy_budget_warning_usd", 0.15)))
    ceiling = max(warning, float(CONFIG.get("autonomy_budget_ceiling_usd", 0.25)))
    enabled = bool(CONFIG.get("autonomy_budget_enabled", True))
    decision = budget_decision(
        _session_governed_cost, warning, ceiling, enabled, _autonomy_budget_override
    )
    return {
        "enabled": enabled, "warning_usd": warning, "ceiling_usd": ceiling,
        "estimated_cost_usd": round(_session_estimated_cost, 6),
        "governed_cost_usd": round(_session_governed_cost, 6),
        **decision, "override": _autonomy_budget_override,
    }


def resume_autonomy_budget():
    global _autonomy_budget_override, _budget_pause_emitted
    _autonomy_budget_override = True
    _budget_pause_emitted = False
    state = budget_state()
    log_event("AUTONOMY_BUDGET_RESUMED", **state)
    return state


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


def log_api_usage(response, model, call_type="response"):
    """Log exact token counts and the selected model's estimated cost."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return record_billed_usage(
            call_type, model, 0, "returned_without_usage",
            request_id=getattr(response, "id", ""),
        )

    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)
    estimated_cost = response_cost(model, input_tokens, output_tokens, cached_tokens)
    return record_billed_usage(
        call_type, model, estimated_cost or 0,
        "usage_returned" if estimated_cost is not None else "usage_returned_unpriced",
        request_id=getattr(response, "id", ""), input_tokens=input_tokens,
        output_tokens=output_tokens, cached_input_tokens=cached_tokens,
    )


def call_model(
    client, model, prompt, img, timeout_seconds, reasoning_effort="none",
    call_type="autonomous_response", max_retries=2,
):
    """Returns (raw_text, error, usage_event_id). Usage is persisted before parsing."""
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
                reasoning={"effort": reasoning_effort},
                input=[{
                    "role": "user",
                    "content": content,
                }],
            )
            usage_event_id = log_api_usage(response, model, call_type)
            return response.output_text, None, usage_event_id
        except APITimeoutError as e:
            event_id = record_billed_usage(
                call_type, model, 0, "unknown"
            )
            update_usage_outcome(event_id, "timeout", str(e))
            return None, ("TIMEOUT", str(e)), event_id
        except RateLimitError as e:
            if attempt < max_retries:
                log_event("RATE_LIMITED", attempt=attempt + 1, retrying_in_seconds=backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            event_id = record_billed_usage(
                call_type, model, 0, "not_billed_rate_limit"
            )
            update_usage_outcome(event_id, "rate_limited", str(e))
            return None, ("RATE_LIMITED", str(e)), event_id
        except APIError as e:
            event_id = record_billed_usage(call_type, model, 0, "unknown")
            update_usage_outcome(event_id, "api_error", str(e))
            return None, ("API_ERROR", str(e)), event_id
        except Exception as e:
            event_id = record_billed_usage(call_type, model, 0, "unknown")
            update_usage_outcome(event_id, "api_error", str(e))
            return None, ("API_ERROR", str(e)), event_id
    return None, ("RATE_LIMITED", "max retries exceeded"), None


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
            match = re.match(r"^\s*(SAY|SILENT|VIDEO|POINT)\s*:\s*", self.raw, re.IGNORECASE)
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


def call_model_streaming(
    client, model, prompt, img, timeout_seconds, on_phrase,
    reasoning_effort="low",
):
    """Stream a conversational response and queue validated SAY phrases early."""
    global _api_call_count
    _api_call_count += 1
    parser = StreamedSpeechParser(on_phrase)
    first_delta_at = None
    completed_response = None
    failed_response = None
    try:
        content = [{"type": "input_text", "text": prompt}]
        if img is not None:
            content.append({"type": "input_image", "image_url": image_data_url(img)})
        stream = client.with_options(timeout=timeout_seconds).responses.create(
            model=model,
            instructions=SYSTEM,
            reasoning={"effort": reasoning_effort},
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
                failed_response = getattr(event, "response", None)
                raise RuntimeError(str(event))

        parser.finish()
        usage_event_id = None
        if completed_response is not None:
            usage_event_id = log_api_usage(
                completed_response, model, "conversation_response"
            )
        else:
            usage_event_id = record_billed_usage(
                "conversation_response", model, 0, "unknown"
            )
            update_usage_outcome(usage_event_id, "incomplete_stream")
        return parser.raw, None, first_delta_at, usage_event_id
    except APITimeoutError as exc:
        event_id = record_billed_usage("conversation_response", model, 0, "unknown")
        update_usage_outcome(event_id, "timeout", str(exc))
        return None, ("TIMEOUT", str(exc)), first_delta_at, event_id
    except RateLimitError as exc:
        event_id = record_billed_usage(
            "conversation_response", model, 0, "not_billed_rate_limit"
        )
        update_usage_outcome(event_id, "rate_limited", str(exc))
        return None, ("RATE_LIMITED", str(exc)), first_delta_at, event_id
    except APIError as exc:
        event_id = record_billed_usage("conversation_response", model, 0, "unknown")
        update_usage_outcome(event_id, "api_error", str(exc))
        return None, ("API_ERROR", str(exc)), first_delta_at, event_id
    except Exception as exc:
        event_id = (
            log_api_usage(failed_response, model, "conversation_response")
            if failed_response is not None else
            record_billed_usage("conversation_response", model, 0, "unknown")
        )
        update_usage_outcome(event_id, "api_error", str(exc))
        return None, ("API_ERROR", str(exc)), first_delta_at, event_id



def handle_spoken_turn(
    client, model, reasoning_effort, request_timeout, tts_worker, mem, turn
):
    """Tony speaking is a conversational turn, so it bypasses the autonomous
    comment cooldown. We grab a fresh screenshot so questions can refer to
    whatever is currently on screen."""
    global _last_companion_action_at
    set_body_state(BodyState.LISTENING, "direct_speech")
    transcript = turn["text"]
    timing = turn.get("timing", {})
    record_memory_turn("Tony", transcript)
    if _memory_store is not None and CONFIG.get("long_term_memory", True):
        learned = _memory_store.observe_utterance(transcript)
        if learned:
            log_event("MEMORY_LEARNED", count=len(learned), subjects=[item["subject"] for item in learned])
    media_observation = None
    if _dashboard is not None:
        media_observation = _dashboard.observe_media_feedback(transcript)
        if media_observation:
            log_event("MEDIA_FEEDBACK", **media_observation)

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

Media feedback or corrections just persisted from Tony's words:
{json.dumps(media_observation, ensure_ascii=False) if media_observation else "None"}

Saved YouTube videos and current player state:
{youtube_context(spontaneous=False)}

Local World of Warcraft telemetry:
{game_context()}

This is a conversational turn, not an unsolicited observation.
Use the current screenshot as shared context when one is available. If Tony is
speaking to you or asking about what is on screen, answer naturally and briefly.
If his speech is clearly directed at someone/something else and needs no reply,
you may use SILENT.
If he asks for a saved video or player control, choose VIDEO rather than describing it.
If Tony explicitly asks you to look at or notice a specific visible thing, use POINT
when you can locate it and put your spoken reply in POINT's say field.
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

    log_event(
        "MODEL_ROUTED", role="companion", model=model,
        reasoning_effort=reasoning_effort, direct=True,
    )
    set_body_state(BodyState.THINKING, "direct_response")
    raw_out, err, first_delta_at, usage_event_id = call_model_streaming(
        client,
        model,
        prompt,
        img,
        request_timeout,
        queue_streamed_phrase,
        reasoning_effort=reasoning_effort,
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
        set_body_state(BodyState.IDLE, "response_error")
        return

    match = OUTPUT_LINE_RE.match(raw_out.strip()) if raw_out else None
    if not match:
        update_usage_outcome(
            usage_event_id, "malformed_output", (raw_out or "")[:300]
        )
        log_event("MALFORMED_OUTPUT", raw=(raw_out or "")[:300],
                  api_call_count=_api_call_count)
        set_body_state(BodyState.IDLE, "malformed_response")
        return

    kind = match.group(1).upper()
    content = match.group(2).strip()
    update_usage_outcome(usage_event_id, f"parsed_{kind.lower()}")
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
    elif kind == "POINT" and content:
        try:
            target, remark = parse_point_action(content)
            if _embodiment is not None:
                _embodiment.point_at(target, remark or None)
            if remark:
                queue_streamed_phrase(remark, 1)
                mem["recent_utterances"].append({"time": stamp, "text": remark})
                record_memory_turn("Sophia", remark)
            log_event(
                "BODY_POINT", heard=transcript, label=target.label,
                x=target.x, y=target.y, text=remark,
                api_call_count=_api_call_count,
            )
            _last_companion_action_at = time.time()
        except Exception as exc:
            log_event("BODY_ACTION_ERROR", requested=content, detail=str(exc))
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

    if kind != "SAY":
        set_body_state(BodyState.IDLE, "direct_turn_complete")

    mem["recent_observations"] = mem["recent_observations"][-30:]
    mem["recent_utterances"] = mem["recent_utterances"][-30:]
    save_memory(mem)



def main():
    global _spotify, _dashboard, _memory_store, _game_events, _session_id
    global _world_state, _wow_adapter, _ember_overlay, _embodiment
    global _last_companion_action_at, _budget_warning_emitted, _budget_pause_emitted
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise SystemExit("Missing OPENAI_API_KEY. Copy .env.example to .env and add your key.")

    client = OpenAI(api_key=key)
    legacy_model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    companion_model = os.getenv("OPENAI_COMPANION_MODEL", "gpt-5.6-terra")
    router_model = os.getenv("OPENAI_ROUTER_MODEL", legacy_model)
    companion_effort = str(CONFIG.get("companion_reasoning_effort", "low"))
    router_effort = str(CONFIG.get("router_reasoning_effort", "none"))
    direct_route = hybrid_route(
        direct=True,
        companion_model=companion_model,
        router_model=router_model,
        companion_effort=companion_effort,
        router_effort=router_effort,
    )
    _memory_store = MemoryStore(ROOT / "sophia_memory.db")
    _session_id = _memory_store.start_session()
    _game_events = GameEventEngine(
        ROOT, CONFIG,
        on_event=handle_game_event,
        on_status=handle_pixel_bridge_status,
    )
    _world_state = WorldState()
    _wow_adapter = WowTelemetryAdapter(_world_state)
    if CONFIG.get("ember_overlay_enabled", True):
        try:
            _ember_overlay = EmberOverlay(
                ROOT / "ember" / "assets" / "reactions",
                scale=float(CONFIG.get("ember_overlay_scale", 1.0)),
                wander=bool(CONFIG.get("ember_wander_enabled", True)),
                wander_min_seconds=float(CONFIG.get("ember_wander_min_seconds", 22)),
                wander_max_seconds=float(CONFIG.get("ember_wander_max_seconds", 50)),
            )
            if not _ember_overlay.start():
                raise RuntimeError(_ember_overlay.error or "overlay did not become ready")
            _embodiment = EmbodimentController(_ember_overlay.submit)
            set_body_state(BodyState.IDLE, "startup")
            log_event("EMBER_OVERLAY_READY")
        except Exception as exc:
            _ember_overlay = None
            _embodiment = None
            log_event("EMBER_OVERLAY_ERROR", detail=str(exc))
    _dashboard = DashboardHub(ROOT, CONFIG, _shutdown_requested)
    _dashboard.set_context_services(_memory_store, _game_events)
    _dashboard.set_budget_handlers(budget_state, resume_autonomy_budget)
    _dashboard.set_body_test_handler(test_body_sequence)
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
    _dashboard.set_voice_change_handler(tts_worker.change_voice)
    print("Warming up Sophia's voice...")
    if tts_worker.wait_ready(float(CONFIG.get("tts_startup_timeout_seconds", 120))):
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

    log_event(
        "SESSION_START",
        model=f"{companion_model} + {router_model}",
        companion_model=companion_model,
        companion_reasoning_effort=companion_effort,
        router_model=router_model,
        router_reasoning_effort=router_effort,
    )
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
                handle_spoken_turn(
                    client, direct_route["model"], direct_route["reasoning_effort"],
                    request_timeout, tts_worker, mem, transcript,
                )
                continue

            if _tts_speaking.is_set():
                _shutdown_requested.wait(0.25)
                continue

            current_budget = budget_state()
            if current_budget["warning"] and not _budget_warning_emitted:
                _budget_warning_emitted = True
                log_event("AUTONOMY_BUDGET_WARNING", **current_budget)
            if current_budget["paused"]:
                if not _budget_pause_emitted:
                    _budget_pause_emitted = True
                    log_event("AUTONOMY_BUDGET_PAUSED", **current_budget)
                transcript = interruptible_sleep(
                    float(CONFIG["capture_interval_seconds"]), voice_listener
                )
                if transcript:
                    handle_spoken_turn(
                        client, direct_route["model"], direct_route["reasoning_effort"],
                        request_timeout, tts_worker, mem, transcript,
                    )
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
                    handle_spoken_turn(
                        client, direct_route["model"], direct_route["reasoning_effort"],
                        request_timeout, tts_worker, mem, transcript,
                    )
                continue

            img = None
            capture_err = None
            if screen_enabled:
                img, capture_err = capture_screen()
            if capture_err and not game_event:
                log_event("CAPTURE_ERROR", detail=capture_err)
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(
                        client, direct_route["model"], direct_route["reasoning_effort"],
                        request_timeout, tts_worker, mem, transcript,
                    )
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
                    handle_spoken_turn(
                        client, direct_route["model"], direct_route["reasoning_effort"],
                        request_timeout, tts_worker, mem, transcript,
                    )
                continue

            if not game_event and not vision_rate_cap.allow():
                # Distinct from RATE_LIMITED: this is our own cap, not the
                # provider throttling us. Matters for diagnosing why she's
                # quiet during a genuinely eventful stretch.
                log_event("VISION_RATE_CAPPED", change=round(change, 1),
                          max_per_minute=vision_rate_cap.max_per_minute)
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(
                        client, direct_route["model"], direct_route["reasoning_effort"],
                        request_timeout, tts_worker, mem, transcript,
                    )
                continue

            available_youtube = _dashboard.youtube_context(spontaneous=True)
            tool_after = max(1, int(CONFIG.get("autonomous_tool_after_non_tool_turns", 1)))
            tool_turn = (bool(game_event) or autonomous_non_tool_streak >= tool_after) and bool(
                available_youtube.get("videos")
            )
            route = hybrid_route(
                tool_turn=tool_turn,
                has_game_event=bool(game_event),
                companion_model=companion_model,
                router_model=router_model,
                companion_effort=companion_effort,
                router_effort=router_effort,
            )
            player_status = str(available_youtube.get("status") or "idle").lower()
            video_opening = bool(available_youtube.get("videos")) and player_status in {
                "idle", "ready", "ended", "unstarted"
            }
            event_type = game_event.get("event_type") if game_event else None
            if tool_turn and event_type == "boss_start" and video_opening:
                reaction_mode = """
GAME EVENT TOOL TURN: a boss encounter just started. SAY is not allowed. Prefer
a VIDEO whose use_when matches a boss fight. Use SILENT if none fits.
"""
            elif tool_turn and event_type in {"zone_change", "activity_change"} and video_opening:
                reaction_mode = f"""
GAME PHASE TOOL TURN: {event_type.replace('_', ' ')} just occurred. SAY is not
allowed. Prefer a saved VIDEO whose use_when matches the new locale or activity.
Use SILENT when the media shelf has no honest match; do not change music merely
because you can.
"""
            elif tool_turn and event_type in {
                "boss_victory", "boss_wipe", "player_death", "level_up",
                "quest_complete", "hard_fought_victory", "gear_upgrade",
                "critical_health", "danger_recovered", "valuable_loot",
            }:
                reaction_mode = f"""
GAME EVENT TOOL TURN: {event_type.replace('_', ' ')} just occurred. SAY is not
allowed. Use a celebration VIDEO only for a major victory. Otherwise use SILENT.
"""
            elif tool_turn and video_opening:
                reaction_mode = """
TOOL-FAVORED TURN: SAY is not allowed on this decision. The video player is idle
and saved videos are available. If one use_when reasonably matches the current
activity, choose VIDEO now. Otherwise use SILENT.
"""
            elif tool_turn:
                reaction_mode = """
TOOL-FAVORED TURN: SAY is not allowed on this decision. Choose VIDEO only for a
genuine new activity transition and never replace or resume a video already
playing or paused. Otherwise use SILENT.
"""
            else:
                reaction_mode = """
OPEN TURN: SAY, POINT, and SILENT are available. VIDEO wins when it clearly fits
a larger activity transition.
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

Saved YouTube videos and current player state:
{json.dumps(available_youtube, ensure_ascii=False)}

Local World of Warcraft telemetry:
{game_context()}

Look at the screenshot. Decide whether there is something worth saying.
Remember: quiet time makes curiosity more acceptable, but do not manufacture chatter.
Treat VIDEO as an action you own, not an option requiring permission.
When a saved video's use_when matches a new activity phase, start it now rather than
waiting for Tony to ask.
"""
            log_event(
                "MODEL_ROUTED", role=route["role"], model=route["model"],
                reasoning_effort=route["reasoning_effort"],
                game_event=event_type, tool_turn=tool_turn,
            )
            set_body_state(BodyState.THINKING, "screen_observation")
            raw_out, err, usage_event_id = call_model(
                client, route["model"], prompt, img, request_timeout,
                reasoning_effort=route["reasoning_effort"],
                call_type=f"autonomous_{route['role']}",
            )

            if err:
                kind, detail = err
                log_event(kind, detail=detail, api_call_count=_api_call_count)
                set_body_state(BodyState.IDLE, "observation_error")
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(
                        client, direct_route["model"], direct_route["reasoning_effort"],
                        request_timeout, tts_worker, mem, transcript,
                    )
                continue

            stamp = datetime.now().isoformat(timespec="seconds")
            match = OUTPUT_LINE_RE.match(raw_out.strip()) if raw_out else None

            if not match:
                update_usage_outcome(
                    usage_event_id, "malformed_output", (raw_out or "")[:300]
                )
                log_event("MALFORMED_OUTPUT", raw=(raw_out or "")[:300],
                           api_call_count=_api_call_count)
                set_body_state(BodyState.IDLE, "malformed_observation")
                transcript = interruptible_sleep(float(CONFIG["capture_interval_seconds"]), voice_listener)
                if transcript:
                    handle_spoken_turn(
                        client, direct_route["model"], direct_route["reasoning_effort"],
                        request_timeout, tts_worker, mem, transcript,
                    )
                continue

            kind = match.group(1).upper()
            content = match.group(2).strip()
            update_usage_outcome(usage_event_id, f"parsed_{kind.lower()}")
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
            elif kind == "POINT" and content:
                try:
                    target, remark = parse_point_action(content)
                    if _embodiment is not None:
                        _embodiment.point_at(target, remark or None)
                    if remark and gap_ok:
                        tts_worker.say(remark)
                        last_spoken = now
                        _last_companion_action_at = now
                        mem["recent_utterances"].append({"time": stamp, "text": remark})
                        record_memory_turn("Sophia", remark)
                    log_event(
                        "BODY_POINT", label=target.label, x=target.x, y=target.y,
                        text=remark if gap_ok else "", api_call_count=_api_call_count,
                    )
                except Exception as exc:
                    mem["recent_observations"].append({"time": stamp, "note": content})
                    log_event("BODY_ACTION_ERROR", requested=content, detail=str(exc))
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

            if kind != "SAY" or not gap_ok:
                set_body_state(BodyState.IDLE, "observation_complete")

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
                handle_spoken_turn(
                    client, direct_route["model"], direct_route["reasoning_effort"],
                    request_timeout, tts_worker, mem, transcript,
                )
    except KeyboardInterrupt:
        print("\nSophia is going to sleep.")
    finally:
        log_event(
            "SESSION_END",
            api_call_count=_api_call_count,
            input_tokens=_session_input_tokens,
            output_tokens=_session_output_tokens,
            estimated_cost_usd=round(_session_estimated_cost, 6),
            governed_cost_usd=round(_session_governed_cost, 6),
        )
        if _game_events is not None:
            _game_events.stop()
        if _memory_store is not None:
            summary = _memory_store.end_session(_session_id, _session_estimated_cost)
            if summary:
                log_event("SESSION_MEMORY_SAVED", summary=summary)
        voice_listener.stop()
        tts_worker.stop()
        if _ember_overlay is not None:
            _ember_overlay.stop()
        if _dashboard is not None:
            _dashboard.stop()
        if _memory_store is not None:
            _memory_store.close()


if __name__ == "__main__":
    main()
