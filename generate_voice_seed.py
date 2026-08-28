"""Create a one-time clean reference clip for local Chatterbox cloning."""
from pathlib import Path
import sys

from dotenv import load_dotenv
from openai import OpenAI


def main():
    root = Path(__file__).parent
    load_dotenv(root / ".env")
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "voice_samples" / "ember_seed.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "Okay, I have a plan. We take this one step at a time, keep our eyes open, "
        "and try not to cause too much chaos. I am curious about what happens next, "
        "but I am right here with you. Let's see what we can discover together."
    )
    with OpenAI().audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="coral",
        input=text,
        instructions=(
            "Speak as a warm, youthful adult gaming companion. Bright, curious, and "
            "affectionate, with a natural medium pitch. Avoid squeakiness, breathiness, "
            "vocal fry, caricature, shouting, and exaggerated childlike delivery."
        ),
        response_format="wav",
    ) as response:
        response.stream_to_file(output)
    print(output.resolve())


if __name__ == "__main__":
    main()
