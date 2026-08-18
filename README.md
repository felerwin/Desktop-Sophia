# Desktop Sophia

Desktop Sophia is a Windows gaming companion that can watch the selected screen,
listen through a microphone, converse through an OpenAI model, and speak locally
with Kokoro. She has a local dashboard, long-term memory, a soundboard, a saved
YouTube shelf, optional Spotify controls, and experimental World of Warcraft
telemetry.

The dashboard and companion data stay on the computer. OpenAI receives the
screenshots, transcripts, and sound clips needed for enabled model features.

## Quick installation

1. Download or clone this repository.
2. Double-click `setup_windows.bat`.
3. Let the installer download Python 3.12 and the required application, audio,
   PyTorch, and Kokoro packages. The initial installation can take a while.
4. Add an OpenAI API key to the `.env` file that opens when setup finishes.
5. Double-click `run_sophia.bat`.

The installer is idempotent: running it again repairs or updates the local
environments without replacing personal configuration or memory.

### Requirements

- Windows 10 or 11
- Internet access during installation and for OpenAI features
- A microphone for voice conversation
- An OpenAI API key
- An NVIDIA GPU is recommended for faster local speech, but CPU Kokoro is
  supported

No Node.js installation is required to run Sophia; the compiled dashboard is
included. Frontend developers can rebuild the source under `dashboard/`.

## Everyday use

Starting Sophia opens a private local dashboard at:

`http://127.0.0.1:8766/`

The dashboard controls the microphone, Kokoro voice, screen awareness,
spontaneous remarks, soundboard, YouTube shelf, memories, personality, and game
telemetry. The console can remain in the background; use the dashboard's sleep
button to stop the session cleanly.

## Optional integrations

### Soundboard

Add MP3 or WAV clips from the dashboard. Clips and their generated descriptions
remain local and are deliberately excluded from Git. Sophia can press understood
buttons autonomously when the moment fits.

### YouTube

Save YouTube links, cue times, and usage notes in the dashboard. The optional
unpacked extension in `chrome_extension/` can send the current YouTube video and
timestamp to Sophia's shelf. No YouTube API key is required.

### Spotify

Add a Spotify application client ID to `.env`, register
`http://127.0.0.1:8765/callback`, and run:

```powershell
.venv\Scripts\python.exe setup_spotify.py
```

Spotify's authorization cache is local and excluded from Git.

### World of Warcraft / ChromieCraft

Copy `chromiecraft_addon/SophiaInsight` into the game's `Interface/AddOns`
folder and enable the addon. Its visible pixel bridge gives Sophia read-only
state, target, equipment, loot, and zone data. Combat-log events can additionally
be configured from the dashboard.

## Local and private files

The repository intentionally does not include:

- `.env` or Spotify authorization tokens
- `config.json`, which may contain device names and local game paths
- conversation logs, memories, and the SQLite memory database
- the personal soundboard and its analyzed library
- the personal YouTube shelf
- Python environments and generated frontend output

Fresh copies are created from `.env.example` and `config.example.json` during
installation.

## Development checks

```powershell
.venv\Scripts\python.exe -m unittest test_spotify_control.py
.venv\Scripts\python.exe -m py_compile sophia.py dashboard_server.py game_events.py memory_store.py spotify_control.py wow_pixel_bridge.py
cd dashboard
npm install
npm test
```

## Status

Desktop Sophia is an evolving personal prototype. Screen interpretation can be
wrong, and autonomous reactions should be treated as companion behavior rather
than reliable gameplay advice. The WoW bridge is read-only and never injects
keyboard or mouse input.
