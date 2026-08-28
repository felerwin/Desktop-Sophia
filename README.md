# Desktop Ember

`ember_app.py` is the canonical application entry point. A few lowercase
`sophia_*` database fields and the installed `SophiaInsight` WoW addon name are
retained solely for compatibility with existing memories and SavedVariables;
they do not name the current persona.

Desktop Ember is a Windows gaming companion that can watch the selected screen,
listen through a microphone, converse through an OpenAI model, and speak locally
with Chatterbox Turbo. She has a local dashboard, long-term memory, a saved
YouTube shelf, optional Spotify controls, and experimental World of Warcraft
telemetry.

Ember also has an optional transparent, always-on-top, click-through body. Idle and
walking use lightweight frame animation; listening, thinking, speaking, and emotional
states switch between independent transparent PNG reaction images without extra model calls.

The dashboard and companion data stay on the computer. OpenAI receives the
screenshots and transcripts needed for enabled model features.

## Quick installation

1. Download or clone this repository.
2. Double-click `setup_windows.bat`.
3. Let the installer download Python 3.12 and the required application, audio,
   PyTorch, and Chatterbox packages. The initial installation can take a while.
4. Add an OpenAI API key to the `.env` file that opens when setup finishes.
5. Double-click `run_ember.bat`.

The installer is idempotent: running it again repairs or updates the local
environments without replacing personal configuration or memory.

## Safe updates

After the first GitHub installation, double-click `update_ember.bat` to update the
program and refresh its dependencies. The updater only accepts a safe fast-forward
from the official `main` branch. It stops if program files have local edits, and it
does not replace `.env`, `config.json`, memory, logs, caches, or Python environments.

### Requirements

- Windows 10 or 11
- Internet access during installation and for OpenAI features
- A microphone for voice conversation
- An OpenAI API key
- An NVIDIA GPU with about 6 GB VRAM is recommended for Chatterbox Turbo.
- Ember starts listening immediately while Chatterbox warms in the background.

No Node.js installation is required to run Ember; the compiled dashboard is
included. Frontend developers can rebuild the source under `dashboard/`.

## Everyday use

Starting Ember opens a private local dashboard at:

`http://127.0.0.1:8766/`

The dashboard controls the microphone, Chatterbox voice, screen awareness,
spontaneous remarks, YouTube shelf, memories, personality, and game
telemetry. The console can remain in the background; use the dashboard's sleep
button to stop the session cleanly.

Ember uses a hybrid model route. Terra handles direct conversation, open-ended
screen understanding, and reliable game events with low reasoning effort. Luna
handles routine video-only decisions at no reasoning effort. Set
`OPENAI_COMPANION_MODEL` and `OPENAI_ROUTER_MODEL` in `.env` to change either
role independently.

### Cost governor

The dashboard shows both the raw session estimate and a safety-adjusted guarded
amount. OpenAI usage is written to the local SQLite ledger as soon as a response
returns, before Ember parses, filters, or rejects its content. A malformed model
reply or rejected transcription therefore still counts toward the total.

At the configured ceiling, only autonomous screen and game-reaction calls pause;
direct microphone conversation remains available. The dashboard can resume
autonomy for the rest of the current session. Daily totals are calculated from
the same usage-event ledger rather than maintained as a second counter.

These values are estimates, not an OpenAI invoice. The default 1.25 safety
multiplier leaves room for estimation drift, and should be reconciled against the
OpenAI usage dashboard periodically. Limits and the multiplier live in
`config.json` under `autonomy_budget_*` and `cost_safety_multiplier`.

## Optional integrations

The microphone listener requests English transcription confidence and rejects
low-confidence, non-English-script, and extremely short ambient fragments. Rejected
audio is recorded as `TRANSCRIPT_REJECTED` in the session log for tuning.

### YouTube

Save YouTube links, cue times, and usage notes in the dashboard. The optional
unpacked extension in `chrome_extension/` can send the current YouTube video and
timestamp to Ember's shelf. No YouTube API key is required.

### Spotify

Add a Spotify application client ID to `.env`, register
`http://127.0.0.1:8765/callback`, and run:

```powershell
.venv\Scripts\python.exe setup_spotify.py
```

Spotify's authorization cache is local and excluded from Git.

### World of Warcraft / ChromieCraft

Copy `chromiecraft_addon/SophiaInsight` into the game's `Interface/AddOns`
folder and enable the addon. Its visible pixel bridge gives Ember read-only
state, target, equipment, loot, and zone data. Combat-log events can additionally
be configured from the dashboard.

Game Sense keeps a short temporal model over those signals. It recognizes stable
activity changes, combat boundaries, minimum health during a fight, danger and
recovery, probable hard-fought victories, zone changes, and equipment upgrades.
The decoder accepts fractionally scaled grids from maximized or GPU-scaled game
windows. Bridge transitions are written as `PIXEL_BRIDGE_STATUS`; only `live`
status exposes exact grid values to Ember, preventing stale or screenshot-derived
details from being described as addon telemetry.
Dashboard events identify both their evidence source and confidence. Exact addon
values are labeled `telemetry`; conclusions formed across several exact states are
labeled `telemetry_derived`; screenshot-only interpretations remain visual
inferences and must not be presented as addon facts.

## Local and private files

The repository intentionally does not include:

- `.env` or Spotify authorization tokens
- `config.json`, which may contain device names and local game paths
- conversation logs, memories, and the SQLite memory database
- the personal YouTube shelf
- Python environments and generated frontend output

Fresh copies are created from `.env.example` and `config.example.json` during
installation.

## Development checks

```powershell
.venv\Scripts\python.exe -m unittest test_game_events.py test_reliability.py test_spotify_control.py test_usage_costs.py test_behavior_fixtures.py test_model_routing.py
.venv\Scripts\python.exe -m py_compile ember_app.py dashboard_server.py game_events.py memory_store.py model_routing.py speech_filter.py spotify_control.py usage_costs.py wow_pixel_bridge.py
cd dashboard
npm install
npm test
```

## Status

Desktop Ember is an evolving personal prototype. Screen interpretation can be
wrong, and autonomous reactions should be treated as companion behavior rather
than reliable gameplay advice. The WoW bridge is read-only and never injects
keyboard or mouse input.
