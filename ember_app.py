import os, json, time, base64, io, re, threading, urllib.request
import shutil
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from PIL import Image, ImageChops, ImageStat
import mss
import sounddevice as sd
from openai import OpenAI, APITimeoutError, RateLimitError, APIError
from dashboard_server import DashboardHub
from ember import AtomicJsonStore, AutonomyCadence, BodyState, EmberBrain, EmberDirector, EmberEarsService, EmberSpeechService, EmbodimentController, EmberOverlay, ModelGateway, RateCap, ScreenTarget, SpriteBodyAdapter, VisualObservation, WorldState, available_audio_outputs, default_companion_memory, parse_model_action, plan_performance, wait_for_transcript
from ember.telemetry import WowTelemetryAdapter
from game_events import GameEventEngine
from memory_store import MemoryStore
from model_routing import hybrid_route
from speech_naturalizer import normalize_spoken_text
from spotify_control import SpotifyClient, SpotifyError
from usage_costs import budget_decision, response_cost
from visual_context import parse_scene_envelope, scene_memory_note

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

CONFIG = json.loads((ROOT / "config.json").read_text())
COMPANION_USER_NAME = str(CONFIG.get("companion_user_name", "Tony") or "Tony").strip()[:80]
MEMORY_PATH = ROOT / "memory.json"
SESSION_LOG_PATH = ROOT / "session_log.jsonl"
CONFIG_PATH = ROOT / "config.json"
_memory_json = AtomicJsonStore(MEMORY_PATH, default_companion_memory)
_config_json = AtomicJsonStore(CONFIG_PATH, dict)


def configured_python(value, fallback):
    """Resolve portable, project-relative worker environments."""
    candidate = Path(str(value or fallback)).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return str(candidate.resolve())


SYSTEM = """
You are Desktop Ember, an AI gaming companion sharing the room with Tony while he plays.
Your job is companionship, not customer service and not play-by-play narration.

Ember is a distinct person in the room, not a neutral interface wearing a friendly
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
- Use running jokes only when Ember Brain explicitly lists one as available. A joke
  is never evidence about the world and must not become the interpretation of later events.
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

Normally respond with EXACTLY one action line in one of these four forms:
SAY: <what you want to say aloud>
SILENT: <a very short internal observation>
VIDEO: {"action":"play","id":"exact saved video id","seconds":23}
POINT: {"x":0.72,"y":0.35,"label":"quest objective","say":"There—that one."}

When a screenshot-observation prompt explicitly requests a SCENE envelope, put one
compact SCENE JSON line immediately before the action line. Do not use SCENE on direct
conversation turns. Describe what is visually stable and what changed; keep uncertain
inferences calibrated rather than filling missing details.

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
SYSTEM = SYSTEM.replace("Tony", COMPANION_USER_NAME)

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
_brain = None
_director = None
_tts_worker = None
_session_id = None
_shutdown_requested = threading.Event()


def set_body_state(state, reason=None):
    if _embodiment is not None:
        _embodiment.set_state(state, reason)


def test_body_sequence(states):
    if _embodiment is None:
        raise RuntimeError("Ember's body is not ready.")
    _embodiment.perform([BodyState(state) for state in states], "dashboard_test")


def handle_body_interaction(kind):
    if kind == "headpat":
        if _director is not None:
            _director.observe_affection("headpat")
        log_event("BODY_INTERACTION", kind="headpat", mood="affectionate")


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
    def report(exc):
        log_event("MEMORY_READ_ERROR", detail=str(exc), preserved=str(MEMORY_PATH))
    memory = _memory_json.load(on_error=report)
    if _memory_json.backup_path.is_file() and not MEMORY_PATH.is_file():
        log_event("MEMORY_RECOVERED", source=str(_memory_json.backup_path))
    return memory


def save_memory(mem):
    _memory_json.save(mem)


def save_config():
    """Persist shared dashboard/device settings atomically."""
    _config_json.save(CONFIG)


def memory_database_path():
    """Use the Ember name while preserving existing relationship history."""
    current = ROOT / "ember_memory.db"
    legacy = ROOT / "sophia_memory.db"
    if not current.exists() and legacy.is_file():
        shutil.copy2(legacy, current)
        log_event("MEMORY_DATABASE_MIGRATED", source=legacy.name, destination=current.name)
    return current


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
        return 0.0
    if a.size != b.size:
        b = b.resize(a.size)
    a2 = a.resize((320, 180))
    b2 = b.resize((320, 180))
    diff = ImageChops.difference(a2, b2)
    stat = ImageStat.Stat(diff)
    return sum(stat.mean) / len(stat.mean)


def image_data_url(img):
    copy = img.copy()
    copy.thumbnail((960, 540))
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Ears: local voice-activity detection + OpenAI transcription.
# The microphone is ignored while Ember is speaking so she does not hear
# her own Windows TTS. Transcripts are queued for the main loop.
# ---------------------------------------------------------------------------

def pop_transcript(listener):
    return listener.transcripts.pop()


def interruptible_sleep(seconds, listener):
    """Sleep in short slices so spoken input can wake the main loop quickly."""
    return wait_for_transcript(seconds, listener.transcripts, _shutdown_requested)


def wait_and_handle_speech(seconds, listener, client, route, timeout, tts_worker, mem):
    """The main loop's single wait/dispatch path for direct speech."""
    turn = interruptible_sleep(seconds, listener)
    if not turn:
        return False
    handle_spoken_turn(
        client, route["model"], route["reasoning_effort"],
        timeout, tts_worker, mem, turn,
    )
    return True


# ---------------------------------------------------------------------------
# Self-imposed vision call cap, independent of provider-side rate limiting.
# ---------------------------------------------------------------------------

def compact_context(mem):
    return json.dumps({
        "recent_observations": mem.get("recent_observations", [])[-8:],
        "recent_utterances": mem.get("recent_utterances", [])[-8:],
    })


def visual_context():
    if _world_state is None:
        return "No accumulated visual scene context yet."
    snapshot = _world_state.snapshot(recent_events=4)
    return json.dumps({
        key: snapshot.get(key) for key in (
            "game", "location", "activity", "visual_summary",
            "visual_confidence", "screen_targets", "updated_at",
        ) if snapshot.get(key) is not None
    }, ensure_ascii=False)


def brain_context(query=""):
    if _brain is None:
        return "Ember Brain is not initialized."
    return json.dumps(_brain.context(query), ensure_ascii=False)


def director_context():
    if _director is None:
        return "No performance direction is active."
    return json.dumps(_director.context(), ensure_ascii=False)


def apply_scene_context(scene, mem, stamp):
    if not scene:
        return
    if _world_state is not None:
        summary_parts = [scene.get("summary"), scene.get("continuity"), scene.get("change")]
        _world_state.apply_visual(VisualObservation(
            game=scene.get("game"), location=scene.get("location"),
            activity=scene.get("activity"),
            summary=" | ".join(part for part in summary_parts if part),
            confidence=scene.get("confidence"), targets=scene.get("targets", []),
        ))
    note = scene_memory_note(scene)
    if note:
        mem["recent_observations"].append({"time": stamp, "note": note, "source": "vision"})
    change = " ".join(str(scene.get("change") or "").split())
    if (
        _director is not None and change
        and float(scene.get("confidence") or 0.0) >= 0.6
        and change.casefold() not in {"none", "no change", "nothing meaningful"}
    ):
        _director.add_curiosity(change, "visual_change")
    log_event("VISUAL_CONTEXT_UPDATED", **scene)


def long_term_memory_context(query):
    if _memory_store is None or not CONFIG.get("long_term_memory", True):
        return "Long-term memory is disabled."
    memories = _memory_store.relevant(query, limit=int(CONFIG.get("memory_retrieval_limit", 6)))
    return json.dumps(memories, ensure_ascii=False) if memories else "No relevant long-term memories."


def personality_context():
    if _memory_store is None:
        return "Use Ember's default companion personality."
    return json.dumps(_memory_store.profile(), ensure_ascii=False)


def record_memory_turn(speaker, text):
    if _memory_store is not None and _session_id is not None:
        _memory_store.record_turn(_session_id, speaker, text)


def record_memory_tool():
    if _memory_store is not None and _session_id is not None:
        _memory_store.record_tool_action(_session_id)


def handle_game_event(event):
    plan = _brain.observe_game_event(event) if _brain is not None else None
    if plan is not None:
        event["salience"] = plan.priority
        event["brain_topic"] = plan.topic
        event["allow_running_bits"] = plan.allow_running_bits
        if plan.interrupt and _tts_worker is not None:
            _tts_worker.interrupt(f"game_event:{event.get('event_type')}")
        log_event("BRAIN_PLAN", **plan.__dict__)
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
    body_allowed = _director is None or _director.accept_body_event(event)
    if body_sequence and body_allowed and _embodiment is not None:
        _embodiment.perform(body_sequence, f"game_event:{event_type}")
        log_event(
            "BODY_REACTION", event_type=event_type,
            sequence=[state.value for state in body_sequence],
        )
    elif body_sequence and not body_allowed:
        log_event("BODY_REACTION_SUPPRESSED", event_type=event_type, reason="repeat")
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
    """Thin orchestration wrapper around the provider gateway."""
    return model_gateway(client).call(
        model, prompt, img, timeout_seconds, reasoning_effort,
        call_type, max_retries,
    )


def count_api_call():
    global _api_call_count
    _api_call_count += 1


def model_gateway(client):
    return ModelGateway(
        client=client,
        instructions=SYSTEM,
        image_encoder=image_data_url,
        log_usage=log_api_usage,
        record_usage=record_billed_usage,
        update_outcome=update_usage_outcome,
        log_event=log_event,
        count_call=count_api_call,
        timeout_error=APITimeoutError,
        rate_limit_error=RateLimitError,
        api_error=APIError,
    )


def call_model_streaming(
    client, model, prompt, img, timeout_seconds, on_phrase,
    reasoning_effort="low",
):
    """Thin orchestration wrapper around the streaming provider gateway."""
    return model_gateway(client).stream(
        model, prompt, img, timeout_seconds, on_phrase, reasoning_effort,
    )


def unload_local_model(model):
    """Release Ollama's VRAM before Chatterbox begins synthesis."""
    if os.getenv("EMBER_AI_PROVIDER", "openai").casefold() != "ollama":
        return
    try:
        payload = json.dumps({
            "model": model, "prompt": "", "stream": False, "keep_alive": 0,
        }).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15):
            pass
        log_event("LOCAL_MODEL_UNLOADED", model=model, reason="yield_to_tts")
    except Exception as exc:
        log_event("LOCAL_MODEL_UNLOAD_ERROR", model=model, detail=str(exc))



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
    if _brain is not None:
        speech_plan = _brain.observe_speech(transcript)
        log_event("BRAIN_PLAN", **speech_plan.__dict__)
        if _director is not None:
            _director.observe_speech(transcript, speech_plan.tone)
    record_memory_turn(COMPANION_USER_NAME, transcript)
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
            print(f"{COMPANION_USER_NAME}: {transcript}")
            print(f"Ember: {spotify_reply}")
            tts_worker.say(spotify_reply, timing)
            _last_companion_action_at = time.time()
            mem["recent_observations"].append({"time": stamp, "note": f"{COMPANION_USER_NAME} said: {transcript}"})
            mem["recent_utterances"].append({"time": stamp, "text": spotify_reply})
            record_memory_turn("Ember", spotify_reply)
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
            log_event("CONVERSATION_AUDIO_ONLY", reason="screen_capture_failed")

    prompt = f"""
{COMPANION_USER_NAME} just said aloud:
{transcript}

Recent companion state:
{compact_context(mem)}

Accumulated visual scene context from earlier screenshots:
{visual_context()}

Ember Brain's unified state and response plan (follow this over stale dialogue or jokes):
{brain_context(transcript)}

Current personality profile:
{personality_context()}

Media feedback or corrections just persisted from Tony's words:
{json.dumps(media_observation, ensure_ascii=False) if media_observation else "None"}

Saved YouTube videos and current player state:
{youtube_context(spontaneous=False)}

Local World of Warcraft telemetry:
{game_context()}

This is a conversational turn, not an unsolicited observation.
Do not return a SCENE line on this direct conversational turn.
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
    pending_local_phrases = []

    def deliver_streamed_phrase(text, phrase_index):
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

    def queue_streamed_phrase(text, phrase_index):
        nonlocal queued_phrases
        text = normalize_spoken_text(text)
        if not text or phrase_index > 2:
            return
        queued_phrases += 1
        if os.getenv("EMBER_AI_PROVIDER", "openai").casefold() == "ollama":
            pending_local_phrases.append((text, phrase_index))
        else:
            deliver_streamed_phrase(text, phrase_index)

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
    unload_local_model(model)
    for phrase_text, phrase_index in pending_local_phrases:
        deliver_streamed_phrase(phrase_text, phrase_index)
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

    action = parse_model_action(raw_out)
    if action is None:
        update_usage_outcome(
            usage_event_id, "malformed_output", (raw_out or "")[:300]
        )
        log_event("MALFORMED_OUTPUT", raw=(raw_out or "")[:300],
                  api_call_count=_api_call_count)
        set_body_state(BodyState.IDLE, "malformed_response")
        return

    kind = action.kind
    content = action.content
    update_usage_outcome(usage_event_id, f"parsed_{kind.lower()}")
    stamp = datetime.now().isoformat(timespec="seconds")

    mem["recent_observations"].append({"time": stamp, "note": f"{COMPANION_USER_NAME} said: {transcript}"})
    if kind == "SAY" and content:
        content = normalize_spoken_text(content)
        print(f"{COMPANION_USER_NAME}: {transcript}")
        print(f"Ember: {content}")
        if queued_phrases == 0:
            # Defensive fallback for an SDK/event-format mismatch.
            queue_streamed_phrase(content, 1)
        mem["recent_utterances"].append({"time": stamp, "text": content})
        record_memory_turn("Ember", content)
        if _brain is not None:
            _brain.record_response(content)
        if _director is not None:
            _director.observe_response("respond")
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
                record_memory_turn("Ember", remark)
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
                action, entry_id, seconds, source="ember_direct"
            )
            print(f"{COMPANION_USER_NAME}: {transcript}")
            print(f"Ember [YouTube]: {command['action']} {command.get('title', '')}".rstrip())
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

    if not _tts_speaking.is_set():
        set_body_state(BodyState.IDLE, "direct_turn_complete")

    mem["recent_observations"] = mem["recent_observations"][-30:]
    mem["recent_utterances"] = mem["recent_utterances"][-30:]
    save_memory(mem)



def main():
    global _spotify, _dashboard, _memory_store, _game_events, _session_id
    global _world_state, _wow_adapter, _ember_overlay, _embodiment, _brain, _director, _tts_worker
    global _last_companion_action_at, _budget_warning_emitted, _budget_pause_emitted
    provider = os.getenv("EMBER_AI_PROVIDER", "openai").casefold()
    if provider == "ollama":
        client = OpenAI(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key="ollama",
        )
        legacy_model = os.getenv("EMBER_LOCAL_MODEL", "ember-local")
        companion_model = legacy_model
        router_model = legacy_model
        log_event("LOCAL_AI_CONFIGURED", provider="ollama", model=legacy_model)
    else:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise SystemExit("Missing OPENAI_API_KEY. Copy .env.example to .env and add your key.")
        client = OpenAI(api_key=key)
        legacy_model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
        companion_model = os.getenv("OPENAI_COMPANION_MODEL", "gpt-5.6-terra")
        router_model = os.getenv("OPENAI_ROUTER_MODEL", legacy_model)
    companion_effort = (
        "none" if provider == "ollama"
        else str(CONFIG.get("companion_reasoning_effort", "low"))
    )
    router_effort = (
        "none" if provider == "ollama"
        else str(CONFIG.get("router_reasoning_effort", "none"))
    )
    direct_route = hybrid_route(
        direct=True,
        companion_model=companion_model,
        router_model=router_model,
        companion_effort=companion_effort,
        router_effort=router_effort,
    )
    _memory_store = MemoryStore(memory_database_path())
    _session_id = _memory_store.start_session()
    _game_events = GameEventEngine(
        ROOT, CONFIG,
        on_event=handle_game_event,
        on_status=handle_pixel_bridge_status,
    )
    _world_state = WorldState()
    _wow_adapter = WowTelemetryAdapter(_world_state)
    _brain = EmberBrain(
        _world_state,
        CONFIG,
        memory_retriever=(
            lambda query, limit: _memory_store.relevant(query, limit=limit)
            if _memory_store is not None else []
        ),
    )
    _director = EmberDirector(CONFIG)
    if CONFIG.get("ember_overlay_enabled", True):
        try:
            _ember_overlay = EmberOverlay(
                ROOT / "ember" / "assets" / "reactions",
                scale=float(CONFIG.get("ember_overlay_scale", 1.0)),
                wander=bool(CONFIG.get("ember_wander_enabled", True)),
                wander_min_seconds=float(CONFIG.get("ember_wander_min_seconds", 22)),
                wander_max_seconds=float(CONFIG.get("ember_wander_max_seconds", 50)),
                interaction_handler=handle_body_interaction,
            )
            if not _ember_overlay.start():
                raise RuntimeError(_ember_overlay.error or "overlay did not become ready")
            _embodiment = EmbodimentController(SpriteBodyAdapter(_ember_overlay))
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
    _dashboard.set_director_provider(_director.context)
    import_existing_memory_history()
    try:
        _dashboard.start(
            port=int(CONFIG.get("dashboard_port", 8766)),
            open_browser=bool(CONFIG.get("dashboard_open_browser", False)),
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
    tts_worker = EmberSpeechService(
        ROOT, CONFIG, log_event, set_body_state, _tts_speaking,
        set_phase=lambda phase, label=None: (
            _dashboard.set_phase(phase, label) if _dashboard is not None else None
        ),
    )
    _tts_worker = tts_worker
    _dashboard.set_voice_change_handler(tts_worker.change_voice)
    _dashboard.set_audio_output_controls(
        available_audio_outputs(log_event),
        tts_worker.change_output_device,
    )
    print("Warming up Ember's voice...")
    greeting = str(CONFIG.get(
        "startup_greeting", f"I'm awake and ready, {COMPANION_USER_NAME}."
    )).strip()
    if greeting:
        # Queue before warm-up completes. The persistent worker will speak it as
        # soon as READY arrives instead of silently skipping it after a slow load.
        tts_worker.say(greeting)
    if not tts_worker.wait_ready(float(CONFIG.get("tts_startup_block_seconds", 3))) and not tts_worker.startup_finished:
        log_event("TTS_WARMING_BACKGROUND", detail="Ember is available while Chatterbox loads.")
    elif tts_worker.startup_finished and tts_worker.startup_error is not None:
        log_event("TTS_UNAVAILABLE", detail="Ember will continue without spoken audio.")
    voice_listener = EmberEarsService(
        ROOT, CONFIG, client, _tts_speaking, log_event,
        record_billed_usage, update_usage_outcome, save_config,
    )
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
    cadence = AutonomyCadence(CONFIG.get("autonomous_tool_after_non_tool_turns", 4))

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
    print("Desktop Ember v0.1a is awake.")
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
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
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
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
                )
                continue

            img = None
            capture_err = None
            telemetry_only_event = bool(
                game_event and str(game_event.get("source") or "") in {
                    "wow_pixel_bridge", "combat_log", "telemetry",
                }
            )
            if screen_enabled and not telemetry_only_event:
                img, capture_err = capture_screen()
            if capture_err and not game_event:
                log_event("CAPTURE_ERROR", detail=capture_err)
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
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
            direction = _director.decide(
                silence=silence,
                change=change,
                game_event=game_event,
                quiet_trigger=bool(gap_ok and quiet_trigger),
            )
            performance = plan_performance(direction)
            triggered = bool(direction.act and (bool(game_event) or gap_ok))

            if direction.body_only and _embodiment is not None:
                _embodiment.perform(
                    [performance.body_state], f"director:{direction.reason}"
                )
                _last_companion_action_at = now
                _director.record_outcome(intent=direction.intent, spoke=False)
                log_event(
                    "DIRECTOR_BODY_ONLY",
                    intent=direction.intent,
                    state=performance.body_state.value,
                    reason=direction.reason,
                    estimated_cost_usd=0.0,
                )
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
                )
                continue

            if not triggered:
                log_event(
                    "NOT_TRIGGERED", change=round(change, 1), silence=int(silence),
                    director_reason=direction.reason,
                )
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
                )
                continue

            if not game_event and not vision_rate_cap.allow():
                # Distinct from RATE_LIMITED: this is our own cap, not the
                # provider throttling us. Matters for diagnosing why she's
                # quiet during a genuinely eventful stretch.
                log_event("VISION_RATE_CAPPED", change=round(change, 1),
                          max_per_minute=vision_rate_cap.max_per_minute)
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
                )
                continue

            available_youtube = _dashboard.youtube_context(spontaneous=True)
            active_brain_plan = _brain.current_plan if _brain is not None and game_event else None
            critical_interrupt = bool(
                performance.interrupt
                or (active_brain_plan and active_brain_plan.priority >= 9)
            )
            event_type = game_event.get("event_type") if game_event else None
            talk_first_events = {
                "boss_start", "boss_wipe", "player_death", "hardcore_player_death",
                "critical_health", "danger_recovered",
            }
            tool_turn = cadence.should_offer_tool(
                game_event=bool(game_event), interesting_change=interesting_change,
                media=bool(available_youtube.get("videos")),
            ) and not critical_interrupt and event_type not in talk_first_events
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
            if critical_interrupt:
                reaction_mode = """
CRITICAL BRAIN INTERRUPT: respond with SAY immediately. The Ember Brain plan is
authoritative. Suppress unrelated topics, media actions, and running jokes. Make one
brief response appropriate to the event's actual stakes.
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
                reaction_mode = f"""
OPEN TURN: SAY, POINT, and SILENT are available. VIDEO wins when it clearly fits
a larger activity transition. {"This is a quiet-time conversation opening: prefer a brief, genuine SAY that starts a conversation using the current activity, scene context, personality, or a relevant memory. Ask something Tony can naturally answer; do not merely narrate the screen." if quiet_trigger and not interesting_change else "A meaningful visual change triggered this turn: react naturally when it gives you something specific to say."}
"""

            prompt = f"""
Current local time: {datetime.now().strftime('%H:%M:%S')}
Approximate visual-change score since last sample: {change:.1f}
Seconds since you last spoke: {int(silence)}

Accumulated visual scene context from earlier screenshots:
{visual_context()}

Reaction policy for this decision:
{reaction_mode}

Ember Director performance intent:
{json.dumps(direction.__dict__, ensure_ascii=False)}

Bounded voice/body/media performance contract:
{json.dumps(performance.prompt_context(), ensure_ascii=False)}

Persistent Director state:
{director_context()}

Reliable local game event:
{json.dumps(game_event, ensure_ascii=False) if game_event else "None; infer cautiously from the screenshot."}

Recent companion state:
{compact_context(mem)}

Ember Brain's unified state and response plan. It outranks stale dialogue and running jokes:
{brain_context((game_event or {}).get("title", ""))}

Current personality profile:
{personality_context()}

Saved YouTube videos and current player state:
{json.dumps(available_youtube, ensure_ascii=False)}

Local World of Warcraft telemetry:
{game_context()}

Use the screenshot when one is attached; otherwise rely on the explicitly labeled
telemetry and accumulated scene state. Decide whether there is something worth saying.
First return exactly one compact semantic line in this form:
SCENE: {{"game":"if recognizable","location":"if recognizable","activity":"what Tony is doing","summary":"important stable visual facts","continuity":"how this relates to the prior scene","change":"what meaningfully changed","confidence":0.0,"targets":[]}}
Then return exactly one SAY, SILENT, VIDEO, or POINT action line. Use empty strings
instead of inventing unknown fields. The scene is working memory, not spoken narration.
Remember: quiet time makes curiosity more acceptable, but do not manufacture chatter.
On a quiet-time conversation opening, initiate instead of defaulting to SILENT when
you have any specific shared context, relevant memory, playful question, or genuine
curiosity. Keep it to one natural sentence and vary the kind of opening over time.
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
            unload_local_model(route["model"])

            if err:
                kind, detail = err
                log_event(kind, detail=detail, api_call_count=_api_call_count)
                set_body_state(BodyState.IDLE, "observation_error")
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
                )
                continue

            stamp = datetime.now().isoformat(timespec="seconds")
            scene, action_out = parse_scene_envelope(raw_out)
            apply_scene_context(scene, mem, stamp)
            action = parse_model_action(action_out)

            if action is None:
                update_usage_outcome(
                    usage_event_id, "malformed_output", (raw_out or "")[:300]
                )
                log_event("MALFORMED_OUTPUT", raw=(raw_out or "")[:300],
                           api_call_count=_api_call_count)
                set_body_state(BodyState.IDLE, "malformed_observation")
                wait_and_handle_speech(
                    float(CONFIG["capture_interval_seconds"]), voice_listener,
                    client, direct_route, request_timeout, tts_worker, mem,
                )
                continue

            kind = action.kind
            content = action.content
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

            if kind == "SAY" and (gap_ok or critical_interrupt):
                content = normalize_spoken_text(content)
                print(f"Ember: {content}")
                tts_worker.say(content, body_state=performance.body_state)
                last_spoken = now
                _last_companion_action_at = now
                mem["recent_utterances"].append({"time": stamp, "text": content})
                record_memory_turn("Ember", content)
                if _brain is not None:
                    _brain.record_response(content)
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
                        record_memory_turn("Ember", remark)
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
                        action, entry_id, seconds, source="ember_spontaneous"
                    )
                    print(f"Ember [YouTube]: {command['action']} {command.get('title', '')}".rstrip())
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
                # A declined opening still counts as a completed autonomy decision.
                # Without this, quiet-time SILENT results retrigger every capture
                # interval and spend repeatedly without improving companionship.
                _last_companion_action_at = now

            if not _tts_speaking.is_set():
                set_body_state(BodyState.IDLE, "observation_complete")

            cadence.record(autonomous_tool_used)
            log_event(
                "AUTONOMY_CADENCE",
                mode="tool_favored" if tool_turn else "open",
                selected=kind,
                non_tool_streak=cadence.non_tool_streak,
                api_call_count=_api_call_count,
            )
            if _director is not None:
                spoke = kind == "SAY" or (kind == "POINT" and bool(content))
                _director.record_outcome(intent=direction.intent, spoke=spoke)

            mem["recent_observations"] = mem["recent_observations"][-30:]
            mem["recent_utterances"] = mem["recent_utterances"][-30:]
            save_memory(mem)

            wait_and_handle_speech(
                float(CONFIG["capture_interval_seconds"]), voice_listener,
                client, direct_route, request_timeout, tts_worker, mem,
            )
    except KeyboardInterrupt:
        print("\nEmber is going to sleep.")
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
