# Screamer

A Windows desktop dictation tool. Hold a hotkey, speak, and polished text appears wherever your cursor is. Lives in the system tray with a clean settings window. Open source, MIT licensed.

## How it works

Press Scroll Lock (configurable), speak your sentence, release. Screamer records 16 kHz mono audio, sends it to a Whisper-compatible STT endpoint, optionally cleans up grammar and spelling via an LLM rewrite, and types the result into the active window. Toggle mode is also supported.

## Setup

```bash
pip install -r requirements.txt
```

Configure your STT provider (Groq, OpenAI, or any OpenAI-compatible endpoint) via the settings dialog. API keys are stored encrypted with Windows DPAPI.

## Features

- Push-to-talk or toggle recording modes
- Primary and fallback STT provider support
- Optional AI rewrite for spelling and grammar
- Persistent settings and secure key storage
- Microphone device selection with auto-calibration
- Post-type key support (Enter, Tab, Space, Backspace)
