"""Microphone hardware, utterance segmentation, and transcription service."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from speech_filter import transcript_rejection_reason
from usage_costs import transcription_cost
from .ears import TranscriptInbox, UtteranceSegmenter
from .transcription import normalize_local_transcription, normalize_provider_transcription
from .vad import SileroVoiceActivityDetector


def available_audio_outputs(log_event=lambda *args, **kwargs: None):
    try:
        devices, hostapis = sd.query_devices(), sd.query_hostapis()
        return [{
            "id": str(index), "index": index,
            "name": str(info.get("name", f"Output {index}")),
            "hostapi": str(hostapis[int(info["hostapi"])]["name"]),
            "label": f"{info.get('name', f'Output {index}')} · "
                     f"{hostapis[int(info['hostapi'])]['name']}",
        } for index, info in enumerate(devices)
          if int(info.get("max_output_channels", 0)) > 0]
    except Exception as exc:
        log_event("AUDIO_OUTPUT_QUERY_ERROR", detail=str(exc))
        return []


class EmberEarsService:
    def __init__(
        self, root, config, client, speaking_event, log_event,
        record_usage, update_outcome, save_config,
    ):
        self.root, self.config, self.client = root, config, client
        self.speaking_event, self.log_event = speaking_event, log_event
        self.record_usage, self.update_outcome = record_usage, update_outcome
        self.save_config = save_config
        self.transcription_provider = os.getenv(
            "EMBER_TRANSCRIPTION_PROVIDER", "openai"
        ).casefold()
        self.local_transcriber = None
        self.transcripts = TranscriptInbox()
        self.stop_event, self.reconnect_event = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.sample_rate = int(config.get("mic_sample_rate", 16000))
        self.block_ms = int(config.get("mic_block_ms", 100))
        self.threshold = float(config.get("mic_rms_threshold", 0.018))
        self.vad_threshold = float(config.get("mic_vad_threshold", 0.5))
        self.vad = None
        if str(config.get("mic_vad_engine", "silero")).casefold() == "silero":
            try:
                self.vad = SileroVoiceActivityDetector(
                    root / "ember" / "models" / "silero_vad.onnx", self.sample_rate
                )
                self.block_ms = 1000 * self.vad.FRAME_SAMPLES / self.sample_rate
                log_event("MIC_VAD_READY", engine="silero", threshold=self.vad_threshold)
            except Exception as exc:
                log_event("MIC_VAD_FALLBACK", engine="rms", detail=str(exc))
        self.end_silence = float(config.get("mic_end_silence_seconds", 0.8))
        self.min_speech = float(config.get("mic_min_speech_seconds", 0.35))
        self.max_speech = float(config.get("mic_max_speech_seconds", 15.0))
        self.device = self._choose_microphone()
        self.transcription_model = config.get(
            "transcription_model", "gpt-4o-mini-transcribe"
        )
        if self.transcription_provider == "local":
            from faster_whisper import WhisperModel
            local_model = os.getenv("EMBER_LOCAL_TRANSCRIPTION_MODEL", "base.en")
            self.local_transcriber = WhisperModel(
                local_model, device="cpu", compute_type="int8"
            )
            self.transcription_model = local_model
            log_event("LOCAL_STT_READY", model=local_model, device="cpu")
        self.reconnect_delay = float(config.get("mic_reconnect_seconds", 2.0))
        self._transcribe_lock = threading.Lock()

    @staticmethod
    def available_devices():
        return [{"index": i, "name": d.get("name", f"Input {i}"),
                 "hostapi": d.get("hostapi")}
                for i, d in enumerate(sd.query_devices())
                if int(d.get("max_input_channels", 0)) > 0]

    def _choose_microphone(self):
        try:
            inputs = self.available_devices()
        except Exception as exc:
            self.log_event("MIC_DEVICE_QUERY_ERROR", detail=str(exc))
            return self.config.get("mic_device")
        if not inputs:
            self.config["mic_device"] = None
            return None
        valid = {item["index"] for item in inputs}
        try:
            saved = int(self.config.get("mic_device"))
        except (TypeError, ValueError):
            saved = None
        default = sd.default.device[0]
        chosen = saved if saved in valid else (
            int(default) if default is not None and int(default) in valid else inputs[0]["index"]
        )
        self.config["mic_device"] = chosen
        try:
            self.save_config()
        except Exception as exc:
            self.log_event("MIC_CONFIG_SAVE_ERROR", detail=str(exc))
        return chosen

    def start(self): self.thread.start()
    def stop(self):
        self.stop_event.set()
        self.reconnect_event.set()

    def change_device(self, device_index):
        device_index = int(device_index)
        if device_index not in {x["index"] for x in self.available_devices()}:
            raise ValueError("That microphone is no longer available.")
        self.device = device_index
        self.reconnect_event.set()
        self.log_event("MIC_CHANGE_REQUESTED", device_index=device_index)

    def _write_wav(self, audio):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        with wave.open(tmp.name, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(self.sample_rate)
            wav.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        return tmp.name

    def _transcribe(self, audio, last_loud_at, detected_at):
        with self._transcribe_lock:
            started, path, usage_id = time.perf_counter(), self._write_wav(audio), None
            try:
                duration = len(audio) / self.sample_rate
                if self.local_transcriber is not None:
                    segments, _ = self.local_transcriber.transcribe(
                        path, language=str(self.config.get("transcription_language", "en")),
                        beam_size=1, vad_filter=False,
                    )
                    result = normalize_local_transcription(segments, duration)
                    usage_id = self.record_usage(
                        "transcription", self.transcription_model, 0, "local"
                    )
                else:
                    with open(path, "rb") as audio_file:
                        response = self.client.audio.transcriptions.create(
                            model=self.transcription_model, file=audio_file,
                            language=str(self.config.get("transcription_language", "en")),
                            response_format="json", include=["logprobs"],
                        )
                    result = normalize_provider_transcription(response, duration)
                    cost = transcription_cost(self.transcription_model, result.audio_seconds)
                    usage_id = self.record_usage(
                        "transcription", self.transcription_model, cost or 0,
                        "duration_estimate" if cost is not None else "usage_returned_unpriced",
                        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                        audio_seconds=result.audio_seconds,
                    )
                if not result.text:
                    self.update_outcome(usage_id, "empty_transcript"); return
                voiced_seconds = max(0.0, float(last_loud_at or detected_at) -
                                     float(detected_at - duration))
                rejection = transcript_rejection_reason(
                    result.text, average_logprob=result.average_logprob,
                    voiced_seconds=voiced_seconds,
                    minimum_logprob=self.config.get(
                        "mic_local_minimum_transcript_logprob", -1.0
                    ) if self.local_transcriber else self.config.get(
                        "mic_minimum_transcript_logprob", -0.7
                    ),
                    short_fragment_seconds=self.config.get("mic_short_fragment_seconds", .45),
                )
                if self.config.get("mic_filter_ambient_speech", True) and rejection:
                    self.update_outcome(usage_id, "transcript_rejected", rejection)
                    self.log_event("TRANSCRIPT_REJECTED", text=result.text, reason=rejection)
                    return
                self.update_outcome(usage_id, "transcript_accepted")
                finished = time.perf_counter()
                self.transcripts.put({"text": result.text, "timing": {
                    "speech_last_loud_at": last_loud_at,
                    "speech_detected_at": detected_at, "stt_finished_at": finished,
                }})
                self.log_event(
                    "HEARD", text=result.text,
                    endpoint_wait_seconds=round(detected_at - last_loud_at, 3),
                    stt_seconds=round(finished - started, 3),
                    average_logprob=result.average_logprob,
                )
            except Exception as exc:
                if usage_id is None:
                    usage_id = self.record_usage(
                        "transcription", self.transcription_model, 0, "unknown",
                        audio_seconds=len(audio) / self.sample_rate,
                    )
                self.update_outcome(usage_id, "api_error", str(exc))
                self.log_event("STT_ERROR", detail=str(exc))
            finally:
                try: os.unlink(path)
                except OSError: pass

    def _device_label(self):
        try:
            index = sd.default.device[0] if self.device is None else self.device
            info = sd.query_devices(index, "input")
            return f"{info.get('name', index)} (index={index}, hostapi={info.get('hostapi')})"
        except Exception as exc:
            return f"{self.device or 'default'} (details unavailable: {exc})"

    def _run(self):
        blocksize = max(1, int(self.sample_rate * self.block_ms / 1000))
        first_open = True
        segmenter = UtteranceSegmenter(self.end_silence, self.min_speech, self.max_speech)
        while not self.stop_event.is_set():
            self.reconnect_event.clear(); segmenter.reset()
            if self.vad is not None: self.vad.reset()
            try:
                label = self._device_label()
                with sd.InputStream(samplerate=self.sample_rate, channels=1,
                                    dtype="float32", blocksize=blocksize,
                                    device=self.device) as stream:
                    self.log_event("MIC_READY" if first_open else "MIC_RECONNECTED", device=label)
                    first_open = False
                    while not self.stop_event.is_set() and not self.reconnect_event.is_set():
                        data, overflowed = stream.read(blocksize)
                        if overflowed: self.log_event("MIC_OVERFLOW")
                        if self.speaking_event.is_set():
                            segmenter.reset()
                            if self.vad is not None: self.vad.reset()
                            continue
                        mono = data[:, 0].copy()
                        rms = float(np.sqrt(np.mean(np.square(mono)))) if len(mono) else 0
                        now = time.perf_counter()
                        voiced, probability = self.vad.is_speech(mono, self.vad_threshold) \
                            if self.vad is not None else (rms >= self.threshold, None)
                        utterance = segmenter.feed(mono, voiced, now)
                        if utterance is not None:
                            threading.Thread(
                                target=self._transcribe,
                                args=(np.concatenate(utterance.frames),
                                      utterance.last_loud_at, utterance.detected_at),
                                daemon=True,
                            ).start()
                            self.log_event("MIC_UTTERANCE_DETECTED",
                                           vad="silero" if self.vad else "rms",
                                           last_probability=probability)
            except Exception as exc:
                if self.stop_event.is_set(): break
                self.log_event("MIC_DISCONNECTED", detail=str(exc), device=self._device_label())
                self.log_event("MIC_RECONNECTING", retrying_in_seconds=self.reconnect_delay)
                try: sd.query_devices()
                except Exception: pass
                self.stop_event.wait(self.reconnect_delay)
