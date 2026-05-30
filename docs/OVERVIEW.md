# OVI Fork Overview

This repo is a small but useful prototype for a Groq-based push-to-talk dictation app. It already has the core speech loop working, but it is not yet a full WisprFlow alternative.

## What the app does

- System tray app built with PyQt6.
- Hold a hotkey, record mic audio, release, transcribe, then type the result into the active window.
- Uses OpenAI-compatible STT endpoints, so Groq fits cleanly.
- Optional LLM rewrite step cleans up spelling and grammar after transcription.

## What is good

- `audio.py` is a good base: 16 kHz, mono, int16 WAV capture is exactly the right shape for Groq STT.
- Silence filtering is smart and saves tokens by skipping empty or near-empty recordings.
- `hotkey.py` is simple and practical: global keyboard and mouse hotkeys, plus typing suppression.
- `transcriber.py` has the right abstraction: primary + fallback STT, custom headers, optional AI rewrite, and text injection.
- `main.py` has a clean state flow: idle -> recording -> processing -> idle.
- The Qt signal bridge is a solid pattern for updating tray UI from worker threads.
- The rewrite feature is genuinely useful for a dictation app, especially for names, foreign words, and punctuation cleanup.

## What is not that good

- No persistent settings. Hotkey, enabled state, rewrite toggle, and post-key behavior reset on restart.
- `.env` is okay for a prototype, but too rough for a desktop product.
- No microphone selector or device management.
- Error handling is mostly `print()` based and not user-facing enough.
- `Transcriber` does too much in one class: STT, rewrite, typing, config, and fallback logic are all mixed together.
- No packaging, installer, autostart, or real logging.
- No proper settings UI yet, only tray-menu controls.
- No streaming or continuous dictation flow; it records a whole utterance, then sends it.
- `pynput` will have platform issues on Wayland, and permissions friction on macOS.

## What to fork

- Keep the push-to-talk flow as the MVP.
- Keep `audio.py` almost as-is, then add device selection and VAD later.
- Keep the STT client shape in `transcriber.py`, especially the Groq-compatible client, fallback model support, and rewrite layer.
- Keep the tray state machine and Qt signal bridge from `main.py`.
- Keep the typing suppression idea and post-type key feature.

## What to replace or rewrite

- Replace `.env`-only config with persistent app settings.
- Replace the inline tray menu as the main settings surface with a real settings window.
- Replace `print()` debugging with structured logging.
- Split `Transcriber` into smaller modules for STT, rewrite, and text injection.
- Replace generated tiny tray icons with proper app assets.
- Add packaging/build tooling before trying to treat it like a real product.

## Best fork direction

- Default STT model: `whisper-large-v3-turbo`.
- Quality fallback: `whisper-large-v3`.
- Add persistent config, device selection, logging, and packaging first.
- Then add VAD, better UX, and optional always-listening / streaming behavior.
- Make recording UX flexible: keep hold-to-talk for the MVP, but add a toggle mode where one shortcut press starts recording and a second press stops it and transcribes.
- Prefer supporting both modes in settings so users can choose the flow that fits their workflow.

## Important note

- The project is AGPLv3, so check the license implications before building a closed-source fork.
