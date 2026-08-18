import json
import sys
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def main():
    playback_lock = threading.Lock()
    playback_number = 0

    def play(path, name, volume, number):
        try:
            audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
            audio = np.asarray(audio, dtype=np.float32) * float(volume)
            with playback_lock:
                if number != playback_number:
                    return
                sd.play(audio, int(sample_rate))
            emit("AUDIO_START", name=name)
            sd.wait()
            with playback_lock:
                if number == playback_number:
                    emit("AUDIO_DONE", name=name)
        except Exception as exc:
            emit("ERROR", name=name, detail=str(exc))

    emit("READY")
    for line in sys.stdin:
        try:
            message = json.loads(line)
            command = message.get("cmd")
            if command == "play":
                with playback_lock:
                    playback_number += 1
                    number = playback_number
                    sd.stop()
                threading.Thread(
                    target=play,
                    args=(message["path"], message.get("name", "clip"), message.get("volume", 0.8), number),
                    daemon=True,
                ).start()
            elif command == "stop":
                with playback_lock:
                    playback_number += 1
                    sd.stop()
                emit("STOPPED")
            elif command == "shutdown":
                with playback_lock:
                    playback_number += 1
                    sd.stop()
                break
        except Exception as exc:
            emit("ERROR", detail=str(exc))


if __name__ == "__main__":
    main()
