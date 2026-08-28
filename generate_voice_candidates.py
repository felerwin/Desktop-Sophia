"""Generate original, consent-safe reference candidates for Ember's local voice."""
from __future__ import annotations

from pathlib import Path
import sys
import wave

from dotenv import load_dotenv
from openai import OpenAI


SCRIPT = (
    "Okay, wait, I have an idea! We take the hidden path, keep an eye out for treasure, "
    "and absolutely pretend this was the plan all along. If it gets scary, I am staying "
    "right here with you. But if we find something amazing, I get to say I told you so. "
    "Ready? Let's go see what happens!"
)

CANDIDATES = (
    (
        "ember_spark.wav", "shimmer",
        "A youthful feminine animated-adventure voice: bright, curious, playful, and "
        "emotionally expressive. Use a naturally higher pitch and lively anime-inspired "
        "rhythm, but keep the sound clear, grounded, and conversational. Never become "
        "squeaky, shrill, breathy, babyish, or digitally exaggerated.",
    ),
    (
        "ember_scout.wav", "coral",
        "A young feminine fantasy sidekick voice with brave, mischievous energy. Speak "
        "crisply and quickly enough to feel excited, with warm affection underneath. "
        "Use expressive pitch movement like an animated heroine while preserving a "
        "natural human timbre. Avoid shouting, rasp, vocal fry, and chipmunk pitch.",
    ),
    (
        "ember_soft.wav", "shimmer",
        "A gentle youthful feminine companion voice: sweet, clever, curious, and quietly "
        "excitable. Keep a light upper-mid pitch, soft warmth, clean consonants, and subtle "
        "anime-style emotional color. Sound young without sounding like a toddler or a "
        "caricature. Avoid whispering, breathiness, squeaks, and exaggerated cuteness.",
    ),
)


def normalize_streamed_wav_header(path):
    """Replace the streaming sentinel sizes with the file's actual PCM length."""
    path = Path(path)
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    repaired = path.with_suffix(".repaired.wav")
    with wave.open(str(repaired), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(frames)
    repaired.replace(path)


def main():
    root = Path(__file__).parent
    load_dotenv(root / ".env")
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "voice_samples" / "candidates"
    output_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    for filename, voice, instructions in CANDIDATES:
        output = output_dir / filename
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts-2025-12-15",
            voice=voice,
            input=SCRIPT,
            instructions=instructions,
            response_format="wav",
        ) as response:
            response.stream_to_file(output)
        normalize_streamed_wav_header(output)
        print(output.resolve())


if __name__ == "__main__":
    main()
