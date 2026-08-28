"""Persistent local Chatterbox Turbo synthesis worker for Ember."""

import json
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from chatterbox.tts_turbo import ChatterboxTurboTTS
from ember.audio_output import prepare_playback_audio
from scipy.io import wavfile

if os.name == "nt":
    import winsound


def emit(kind, **fields):
    print(json.dumps({"event": kind, **fields}, ensure_ascii=False), flush=True)


def resolve_output_device(name_hint, hostapi_hint):
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    candidates = []
    for index, device in enumerate(devices):
        if int(device.get("max_output_channels", 0)) < 1:
            continue
        hostapi_name = str(hostapis[device["hostapi"]]["name"])
        if name_hint and name_hint.casefold() not in str(device["name"]).casefold():
            continue
        if hostapi_hint and hostapi_hint.casefold() not in hostapi_name.casefold():
            continue
        candidates.append((index, device, hostapi_name))
    if not candidates:
        raise RuntimeError(
            f"No output device matched name={name_hint!r}, hostapi={hostapi_hint!r}."
        )
    if not name_hint and not hostapi_hint:
        default_output = sd.default.device[1]
        for candidate in candidates:
            if candidate[0] == default_output:
                return candidate
    return candidates[0]


def main():
    prompt_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 and sys.argv[1] else None
    output_name = str(sys.argv[2]).strip() if len(sys.argv) > 2 else ""
    output_hostapi = str(sys.argv[3]).strip() if len(sys.argv) > 3 else ""
    model_path = Path(sys.argv[4]).resolve() if len(sys.argv) > 4 and sys.argv[4] else None
    debug_capture = len(sys.argv) > 5 and str(sys.argv[5]).casefold() in {"1", "true", "yes"}
    if prompt_path and not prompt_path.is_file():
        raise SystemExit(f"Chatterbox voice reference does not exist: {prompt_path}")
    if model_path and not model_path.is_dir():
        raise SystemExit(f"Chatterbox model snapshot does not exist: {model_path}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        emit("STARTUP_STAGE", stage="loading_model", local=bool(model_path))
        if model_path:
            # from_pretrained() contacts Hugging Face even when every model file
            # is cached. Loading the verified snapshot directly makes offline
            # startup deterministic and avoids an indefinite warm-up.
            model = ChatterboxTurboTTS.from_local(model_path, device=device)
        else:
            model = ChatterboxTurboTTS.from_pretrained(device=device)
    except Exception as exc:
        emit("ERROR", detail=f"Chatterbox startup failed: {exc}")
        return
    try:
        output_index, output_info, output_api = resolve_output_device(output_name, output_hostapi)
        output_rate = int(round(float(output_info["default_samplerate"])))
        sd.check_output_settings(
            device=output_index, channels=1, dtype="float32", samplerate=output_rate
        )
    except Exception as exc:
        emit("ERROR", detail=f"Chatterbox output setup failed: {exc}")
        return
    emit(
        "READY", voice="chatterbox-turbo", device=device,
        output_device=output_info["name"], output_hostapi=output_api,
        output_rate=output_rate,
        playback_backend="windows_wav" if os.name == "nt" else "portaudio",
    )

    commands = queue.Queue()
    cache_root = Path(__file__).resolve().parent / ".cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    debug_root = cache_root / "tts_debug"
    if debug_capture:
        debug_root.mkdir(parents=True, exist_ok=True)

    def read_commands():
        for line in sys.stdin:
            line = line.strip()
            if line:
                commands.put(line)
        commands.put(None)

    threading.Thread(target=read_commands, daemon=True).start()
    while True:
        line = commands.get()
        if line is None:
            break
        try:
            msg = json.loads(line)
            if msg.get("cmd") == "stop":
                emit("STOPPED")
                break
            if msg.get("cmd") == "cancel":
                emit("CANCELLED", phase="idle")
                continue

            text = str(msg.get("text", "")).strip()
            if not text:
                continue

            started = time.perf_counter()
            kwargs = {"audio_prompt_path": str(prompt_path)} if prompt_path else {}
            wav = model.generate(text, **kwargs)
            audio = wav.detach().float().cpu().numpy().squeeze()
            if not len(audio):
                emit("ERROR", detail="Chatterbox generated no audio.")
                continue

            raw_audio = np.asarray(audio, dtype=np.float32).reshape(-1)
            audio = prepare_playback_audio(raw_audio, model.sr, output_rate)
            if debug_capture:
                wavfile.write(debug_root / "latest_native_24k.wav", int(model.sr), raw_audio)
                wavfile.write(debug_root / "latest_output_48k.wav", output_rate, audio)
            emit(
                "AUDIO_PREPARED", native_rate=int(model.sr), output_rate=output_rate,
                native_samples=len(raw_audio), output_samples=len(audio),
                native_peak=round(float(np.max(np.abs(raw_audio))), 4),
                output_peak=round(float(np.max(np.abs(audio))), 4),
            )
            cancelled = False
            while not commands.empty():
                pending = commands.get_nowait()
                if pending is None:
                    return
                pending_msg = json.loads(pending)
                if pending_msg.get("cmd") == "stop":
                    emit("STOPPED")
                    return
                if pending_msg.get("cmd") == "cancel":
                    cancelled = True
            if cancelled:
                emit("CANCELLED", phase="synthesis")
                continue
            synthesis_seconds = time.perf_counter() - started
            playback_started = time.perf_counter()
            emit("AUDIO_START", synthesis_seconds=round(synthesis_seconds, 3))
            if os.name == "nt":
                handle = tempfile.NamedTemporaryFile(
                    prefix="ember-tts-", suffix=".wav", dir=cache_root, delete=False
                )
                playback_path = Path(handle.name)
                handle.close()
                wavfile.write(playback_path, output_rate, audio)
                try:
                    winsound.PlaySound(
                        str(playback_path),
                        winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                    )
                    playback_deadline = playback_started + len(audio) / output_rate
                    stop_requested = False
                    while time.perf_counter() < playback_deadline:
                        if not commands.empty():
                            pending = commands.get_nowait()
                            if pending is None:
                                stop_requested = True
                                break
                            pending_msg = json.loads(pending)
                            if pending_msg.get("cmd") == "stop":
                                stop_requested = True
                                break
                            if pending_msg.get("cmd") == "cancel":
                                cancelled = True
                                break
                        time.sleep(0.04)
                finally:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    try:
                        playback_path.unlink()
                    except OSError:
                        pass
                if stop_requested:
                    emit("STOPPED")
                    return
            else:
                chunk_size = max(256, output_rate // 20)
                with sd.OutputStream(
                    samplerate=output_rate, channels=1, dtype="float32", device=output_index
                ) as stream:
                    for offset in range(0, len(audio), chunk_size):
                        if not commands.empty():
                            pending = commands.get_nowait()
                            if pending is None:
                                return
                            pending_msg = json.loads(pending)
                            if pending_msg.get("cmd") == "stop":
                                emit("STOPPED")
                                return
                            if pending_msg.get("cmd") == "cancel":
                                cancelled = True
                                break
                        stream.write(audio[offset:offset + chunk_size].reshape(-1, 1))
            if cancelled:
                emit("CANCELLED", phase="playback")
                continue
            emit(
                "SPOKEN",
                chars=len(text),
                playback_seconds=round(time.perf_counter() - playback_started, 3),
            )
        except Exception as exc:
            emit("ERROR", detail=str(exc))


if __name__ == "__main__":
    main()
