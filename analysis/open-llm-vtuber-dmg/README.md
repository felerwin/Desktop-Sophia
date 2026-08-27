# Open LLM Vtuber 1.2.1 DMG inspection

This directory contains metadata and analysis extracted from the user-provided macOS disk image. The application was not executed.

- Source: `C:\Users\klein\Downloads\open-llm-vtuber-1.2.1.dmg`
- SHA-256: `5D6B601B3065585D0AE8D3A863A4FA483573630A104D650930AB8346A03244A2`
- Disk image: Apple APFS, ARM64 macOS application

## Verdict

This is useful as an architectural reference, but it is not a drop-in body for Ember. It is the macOS ARM64 Electron frontend for Open LLM Vtuber 1.2.1. Its actual AI/STT/TTS backend is expected to run separately and communicate over WebSocket.

The strongest ideas to adapt are:

1. A serialized speech queue that keeps audio, subtitle text, expression, and talk motion together.
2. Explicit conversation-chain lifecycle messages (`start`, `audio`, synth complete, playback complete, `end`).
3. Interruption that atomically stops audio, lip sync, queued speech, and the active conversation state.
4. Silero voice-activity detection in the client, with microphone restart after the response finishes.
5. Pet-mode window behavior: transparent, frameless, always-on-top, click-through outside the character, draggable on the character, and a context menu.
6. A model adapter API for motions, expressions, hit areas, scaling, positioning, and drag/tap interaction.

## How it works

- UI shell: Electron + React + TypeScript bundle.
- Body: Live2D Cubism renderer supporting `.model3.json`, motion groups, expressions, physics, hit areas, dragging, tap-triggered weighted motions, and idle motion.
- Speech transport: WebSocket messages carry WAV audio, display text, and expression actions.
- Lip sync: audio is passed to the Live2D WAV handler and RMS is amplified before driving the mouth parameter.
- Listening: browser-side Silero VAD via ONNX Runtime and an AudioWorklet.
- Backend boundary: model configuration, transcripts, generated audio, actions, history, and tool statuses come from an external server.

## Fit with Ember

Ember already has the more important half: persistent identity, world state, autonomous conversation logic, Chatterbox, and a body-control protocol. Replacing that with this application would be a regression and create a second control stack.

The right integration is to borrow the orchestration pattern:

`Ember utterance -> one queued performance object -> pose/expression + talk animation + Chatterbox audio -> completion acknowledgement -> idle/default pose`

For the current sprite body, the Live2D engine itself adds little. If Ember later gets a rigged Live2D model, the model adapter and RMS lip-sync design become directly relevant.

## Cautions

- The application bundle contains no top-level license file, so do not copy its bundled implementation into Ember until the upstream repository and license are verified.
- The Electron window enables `nodeIntegration` and disables the renderer sandbox. That is not a security posture to copy.
- The macOS bundle requests camera, microphone, Documents, Downloads, Bluetooth, and arbitrary network-load permissions. Ember should keep narrower capabilities.
- Metadata still contains template values (`com.electron.app`, `example.com`), suggesting rough packaging rather than a polished distributable.
- This DMG is ARM64 macOS-only and cannot run natively on the current Windows desktop.

## Inspection notes

- The application was never launched.
- `app.asar` and `Info.plist` were extracted for static inspection.
- `app.asar` integrity hash declared by the bundle: `a2e9834cea0b7b0affb4a232c3442de06c07a9e5872ca01becaf552152f3899e`.
