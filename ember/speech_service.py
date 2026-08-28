"""Persistent speech scheduler above the Chatterbox process boundary."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .chatterbox_process import ChatterboxProcess
from .embodiment import BodyState, body_state_for_speech
from .performance import SpeechPerformance
from .tts_protocol import worker_command
from .tts_queue import SpeechQueue


class EmberSpeechService:
    def __init__(
        self, root, config, log, set_body_state, speaking_event,
        set_phase=None, process=None, autostart=True,
    ):
        self.root = Path(root)
        self.config = config
        self.log = log
        self.set_body_state = set_body_state
        self.speaking_event = speaking_event
        self.set_phase = set_phase or (lambda phase, label=None: None)
        python = Path(str(config.get("chatterbox_python") or ".chatterbox_venv/Scripts/python.exe"))
        if not python.is_absolute():
            python = self.root / python
        self.process = process or ChatterboxProcess(
            self.root, config, str(python.resolve()), self.root / "chatterbox_worker.py", log
        )
        self.engine = "chatterbox"
        self.voice = "chatterbox-turbo"
        self.name = "Chatterbox Turbo"
        self.queue = SpeechQueue()
        self._ready = threading.Event()
        self._startup_error = None
        self._thread = None
        if autostart:
            self._start_thread()

    def _start_thread(self):
        self._thread = threading.Thread(target=self._run, name="ember-speech", daemon=True)
        self._thread.start()

    def _start_process(self):
        message = self.process.start()
        self.log(
            "TTS_READY", engine=self.engine, voice=message.get("voice", self.voice),
            device=message.get("device"), output_device=message.get("output_device"),
            output_hostapi=message.get("output_hostapi"), output_rate=message.get("output_rate"),
            playback_backend=message.get("playback_backend"),
        )

    def _run(self):
        try:
            self._start_process()
        except Exception as exc:
            self._startup_error = str(exc)
            self.log("TTS_ERROR", detail=str(exc))
            self._ready.set()
            return
        self._ready.set()
        while True:
            item = self.queue.get()
            if item is self.queue.STOP:
                break
            command = item.get("command") if isinstance(item, dict) else None
            if command in {"restart", "change_output"}:
                self.process.shutdown()
                try:
                    self._start_process()
                    self._startup_error = None
                    self.log("TTS_RESTARTED" if command == "restart" else "TTS_OUTPUT_CHANGED")
                    self.set_phase("listening", "I’m listening.")
                except Exception as exc:
                    self._startup_error = str(exc)
                    self.log("TTS_ERROR", detail=str(exc))
                finally:
                    self._ready.set()
                continue
            if command:
                continue
            self._perform(item)
        self.process.shutdown()

    def _perform(self, performance):
        if not isinstance(performance, SpeechPerformance):
            performance = SpeechPerformance(**performance)
        text, timing = performance.text, performance.timing
        self.speaking_event.set()
        performance.begin(self.set_body_state)
        self.set_phase("speaking", "Speaking…")
        try:
            sent_at = time.perf_counter()
            self.process.send({"text": text})
            message = self.process.read_event({"AUDIO_START", "CANCELLED", "ERROR"})
            if message.get("event") != "AUDIO_START":
                self.log("TTS_CANCELLED" if message.get("event") == "CANCELLED" else "TTS_ERROR",
                         detail=message.get("detail", ""), phase=message.get("phase"))
                return
            started = time.perf_counter()
            performance.audio_started(self.set_body_state)
            self.log(
                "TTS_AUDIO_START", phrase_index=timing.get("phrase_index", 1),
                queue_wait_seconds=round(sent_at - timing.get("queued_at", sent_at), 3),
                synthesis_seconds=round(message.get("synthesis_seconds", started - sent_at), 3),
                response_latency_seconds=round(started - timing.get("speech_last_loud_at", started), 3),
            )
            message = self.process.read_event({"SPOKEN", "CANCELLED", "ERROR"})
            self.log(
                "TTS_SPOKE" if message.get("event") == "SPOKEN" else "TTS_CANCELLED",
                engine=self.engine, voice=self.voice, chars=message.get("chars", len(text)),
                playback_seconds=message.get("playback_seconds", 0), phase=message.get("phase"),
            )
        except Exception as exc:
            self.log("TTS_ERROR", detail=str(exc))
        finally:
            self.speaking_event.clear()
            performance.finish(self.set_body_state, return_to_idle=self.queue.empty())
            if self.queue.empty():
                self.set_phase("listening", "I’m listening.")

    def say(self, text, timing=None, wait=False, timeout=None, body_state=None):
        if not self.config.get("speak_out_loud", True):
            return False
        timing = dict(timing or {})
        timing["queued_at"] = time.perf_counter()
        done = threading.Event() if wait else None
        self.queue.put(SpeechPerformance(
            text=text, timing=timing, done=done,
            opening_state=body_state or body_state_for_speech(text),
        ))
        return done.wait(timeout) if done is not None else False

    def change_voice(self, voice, language=None, name=None):
        self.log("VOICE_CHANGE_IGNORED", engine=self.engine)

    def change_output_device(self, name, hostapi):
        self.config["tts_output_device"] = str(name)
        self.config["tts_output_hostapi"] = str(hostapi)
        self._ready.clear()
        self.queue.put({"command": "change_output"})

    def interrupt(self, reason="higher_priority_event"):
        pending = self.queue.drain_performances()
        for performance in pending:
            performance.finish(self.set_body_state, return_to_idle=False)
        if self.process.running and self.speaking_event.is_set():
            self.process.send(worker_command("cancel"))
        self.log("TTS_INTERRUPTED", reason=reason, discarded=len(pending))

    def wait_ready(self, timeout=60):
        if not self._ready.wait(timeout):
            self.log("TTS_STILL_WARMING", waited_seconds=timeout)
            return False
        return self._startup_error is None

    @property
    def startup_finished(self):
        return self._ready.is_set()

    @property
    def startup_error(self):
        return self._startup_error

    def stop(self):
        self.queue.stop()
        if self._thread:
            self._thread.join(timeout=7)
        if self._thread and self._thread.is_alive():
            self.process.shutdown()
