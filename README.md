
# Screamer

Fast Windows dictation that types wherever your cursor is.

Press a hotkey, speak, release - Screamer records your voice, sends it to a speech-to-text provider, optionally cleans up the result with an LLM, and types the final text into the active window.

No browser tab. No copy-paste. Just talk and keep moving.

## Install

Download the latest release from the **Releases** page.

Grab the versioned Windows zip:

```text
Screamer-vX.Y.Z-windows-x64.zip
```

Then:

1. Extract the zip.
2. Open the `Screamer` folder.
3. Run `Screamer.exe`.
4. Configure your provider in Settings.

That's it.

## What Screamer does

- **Global hotkey dictation** - speak from anywhere on Windows.
- **Hold-to-talk or toggle mode** - choose how recording should behave.
- **Types into the focused app** - works in editors, browsers, chats, notes, docs, and more.
- **OpenAI-compatible speech-to-text** - use OpenAI, Groq, or another compatible `/audio/transcriptions` endpoint.
- **Optional AI cleanup** - fix punctuation, grammar, spelling, and capitalization after transcription.
- **Fallback providers** - configure backup STT and LLM providers if the primary one fails.
- **System tray app** - enable/disable, change hotkey, toggle rewrite, open settings, or exit from the tray.
- **Microphone selection** - pick your input device and calibrate silence detection.
- **Post-type key** - optionally press `Enter`, `Tab`, `Space`, or `Backspace` after typing.
- **Windows startup support** - launch Screamer automatically when you log in.
- **Secure API key storage** - API keys are stored locally with Windows DPAPI.

## How it works

```text
Hotkey -> Record audio -> Transcribe -> Optional cleanup -> Type into active window
```

Screamer records 16 kHz mono WAV audio, sends it to your configured STT provider, optionally runs the text through an LLM cleanup step, then injects the final text with Windows `SendInput`.

## Setup

On first launch, open **Settings** from the tray icon.

You need at least one speech-to-text provider.

Example OpenAI-compatible STT config:

```text
Base URL: https://api.openai.com/v1
Model: whisper-1
API key: your_api_key
```

For Groq or another provider, use their OpenAI-compatible base URL and model name.

The LLM rewrite step is optional. Leave it off if you want raw transcription.

## Settings

### General

- Recording mode: `Hold to talk` or `Toggle`
- Hotkey selection
- Post-type key
- Start with Windows

### STT

- Primary speech-to-text provider
- Optional fallback STT provider
- Optional transcription language
- Custom headers

### LLM

- Optional AI rewrite
- Primary LLM provider
- Optional fallback LLM provider
- Editable system prompt
- Custom headers

### Audio

- Input device selection
- Silence threshold calibration

## Hotkeys

Available hotkey options:

```text
Ctrl+Alt+Space
Ctrl+Shift+Space
Ctrl+Alt+D
Ctrl+Alt+S
Ctrl+Alt+V
Scroll Lock
Pause
```

Default: `Ctrl+Alt+Space`

## For developers

Run from source:

```bash
pip install -r requirements.txt
python -m src.main
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

Build dependencies:

```bash
pip install -r requirements-build.txt
```

Build the Windows executable:

```bash
python -m PyInstaller --noconfirm --clean screamer.spec
```

## Releases

Pushing a version tag builds and publishes a Windows release automatically:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The release workflow:

1. Installs dependencies.
2. Runs the test suite.
3. Builds Screamer with PyInstaller.
4. Packages the app as:

```text
Screamer-v1.0.0-windows-x64.zip
Screamer-v1.0.0-windows-x64.zip.sha256
```

Hyphenated tags like `v1.0.0-rc1` are published as pre-releases.

## Platform

Screamer is built for Windows.

It depends on Windows-specific features including:

- global hotkeys via `RegisterHotKey`
- text injection via `SendInput`
- tray integration
- DPAPI key storage
- startup registration through the current user Run key

## License

MIT
