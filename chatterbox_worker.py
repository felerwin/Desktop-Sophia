"""Persistent local Chatterbox Turbo synthesis worker for Ember."""

import json
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from chatterbox.tts_turbo import ChatterboxTurboTTS


def emit(kind, **fields):
    print(json.dumps({"event": kind, **fields}, ensure_ascii=False), flush=True)


def main():
    prompt_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 and sys.argv[1] else None
    if prompt_path and not prompt_path.is_file():
        raise SystemExit(f"Chatterbox voice reference does not exist: {prompt_path}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        model = ChatterboxTurboTTS.from_pretrained(device=device)
    except Exception as exc:
        emit("ERROR", detail=f"Chatterbox startup failed: {exc}")
        return
    emit("READY", voice="chatterbox-turbo", device=device)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("cmd") == "stop":
                emit("STOPPED")
                break

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

            audio = np.asarray(audio, dtype=np.float32)
            synthesis_seconds = time.perf_counter() - started
            playback_started = time.perf_counter()
            sd.play(audio, model.sr)
            emit("AUDIO_START", synthesis_seconds=round(synthesis_seconds, 3))
            sd.wait()
            emit(
                "SPOKEN",
                chars=len(text),
                playback_seconds=round(time.perf_counter() - playback_started, 3),
            )
        except Exception as exc:
            emit("ERROR", detail=str(exc))


if __name__ == "__main__":
    main()
