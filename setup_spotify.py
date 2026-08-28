import os
from pathlib import Path

from dotenv import load_dotenv

from spotify_control import SpotifyClient, SpotifyError


ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")


def main():
    client_id = (os.getenv("SPOTIFY_CLIENT_ID") or "").strip()
    if not client_id:
        raise SystemExit("SPOTIFY_CLIENT_ID is missing from .env")

    redirect_uri = os.getenv(
        "SPOTIFY_REDIRECT_URI",
        "http://127.0.0.1:8765/callback",
    )
    spotify = SpotifyClient(client_id, redirect_uri, ROOT / ".spotify_token.json")
    try:
        spotify.authorize_interactively()
    except SpotifyError as exc:
        raise SystemExit("Spotify setup failed: " + str(exc)) from exc
    print("Spotify is connected. You can start Ember normally now.")


if __name__ == "__main__":
    main()
