import sys
import json
import time
import numpy as np
import sounddevice as sd
import torch
from kokoro import KPipeline

SAMPLE_RATE = 24000

def emit(kind, **fields):
    print(json.dumps({"event": kind, **fields}, ensure_ascii=False), flush=True)

def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: kokoro_worker.py VOICE LANG SPEED")

    voice = sys.argv[1]
    lang = sys.argv[2]
    speed = float(sys.argv[3])
    device = "cuda" if torch.cuda.is_available() else "cpu"

    pipeline = KPipeline(
        lang_code=lang,
        repo_id="hexgrad/Kokoro-82M",
        device=device,
    )
    emit("READY", voice=voice, device=device)

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

            synthesis_started_at = time.perf_counter()
            chunks = []
            for result in pipeline(text, voice=voice, speed=speed):
                audio = getattr(result, "audio", None)
                if audio is None:
                    _, _, audio = result
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                chunks.append(np.asarray(audio, dtype=np.float32))

            if not chunks:
                emit("ERROR", detail="Kokoro generated no audio.")
                continue

            audio = np.concatenate(chunks)
            synthesis_seconds = time.perf_counter() - synthesis_started_at
            playback_started_at = time.perf_counter()
            sd.play(audio, SAMPLE_RATE)
            emit("AUDIO_START", synthesis_seconds=round(synthesis_seconds, 3))
            sd.wait()
            emit(
                "SPOKEN",
                chars=len(text),
                playback_seconds=round(time.perf_counter() - playback_started_at, 3),
            )

        except Exception as exc:
            emit("ERROR", detail=str(exc))

if __name__ == "__main__":
    main()
