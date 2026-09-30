
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
- **On-screen recording indicator** - a click-through pill shows the opened microphone and live input level while recording, then processing status.
- **System tray app** - enable/disable, switch STT language, change hotkey, toggle rewrite, open settings, or exit from the tray.
- **Session-safe settings** - tray changes during dictation apply to the next recording; Settings temporarily locks other tray configuration controls.
- **Microphone selection and recovery** - pick your input device, refresh the list after device changes, and calibrate silence detection. An unavailable explicit selection never silently switches to another microphone.
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

For Groq, prefer:

```text
Base URL: https://api.groq.com/openai/v1
Model: whisper-large-v3-turbo
Active language: English (en)
```

If accuracy matters more than speed, use `whisper-large-v3` instead.

For another OpenAI-compatible provider, use its base URL and model name.

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
- Active language (Auto, Czech, English, or an added favorite)
- Ordered favorite language codes: add, edit, or remove them here, then switch from the tray Language menu. Language hints depend on provider support and do not translate or automatically handle mixed-language speech.
- Custom headers

### LLM

- Optional AI rewrite
- Primary LLM provider
- Optional fallback LLM provider
- Editable system prompt
- Reset to Current Default explicitly selects the conservative cleanup prompt. Upgrades preserve every saved prompt, including an old default; unrelated Apply/OK preserves its whitespace and line endings. Rewriting remains off by default. Prompt rules guide the model, not guarantee unchanged meaning, so review important dictation.
- Custom headers

### Audio

- Input device selection
- Refresh the input-device list without closing Settings. Unavailable selections stay saved until you choose another input; System Default deliberately follows the current default on the next recording.
- Silence threshold calibration

The recording meter measures the latest captured block's RMS on a fixed full-scale
range; low-level microphones can show a small bar. "Waiting for samples" differs
from captured quiet input. Neither a moving meter nor a quiet signal establishes
recognition quality or a hardware fault. Capture errors let you refresh/reselect
in **Settings > Audio** and try the next hotkey without restarting Screamer.
Device IDs are enumeration indexes, not permanent hardware identities: a missing
ID can remap only by a unique exact name, while conflicts or duplicate names require
reselection (or an intentional System Default choice).

## Hotkeys

Quick-pick presets:

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

Or set a **custom hotkey**: in Settings, click **Record** and press any key
combination, a function key, or a mouse side/middle button. Bare everyday keys
need a modifier (Ctrl/Alt/Shift); function keys, lock/pause keys, and mouse
side/middle buttons may be bound on their own. The matched trigger is swallowed
so it won't reach the app underneath.

## For developers

See [implementation and API contracts](docs/IMPLEMENTATION.md),
[planned features](docs/FEATURES.md), [their implementation plan](docs/IMPLEMENTATION-PLAN.md),
and [release operations](docs/RELEASES.md) for maintained technical documentation.

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

CI also builds the Windows executable on pull requests to catch packaging failures before merge.

## Releases

After a reviewed merge to `main`, successful CI and CodeQL, the release workflow
chooses a version relative to the latest published stable release. It builds and tests
on Windows, then publishes a GitHub Release only once both assets are attached:

```text
Screamer-vX.Y.Z-windows-x64.zip
Screamer-vX.Y.Z-windows-x64.zip.sha256
```

Version bumps follow the commits since that release: breaking changes cause a major
bump, `feat:` a minor bump, and other changes a patch bump. No second release PR,
personal token or manual tag is needed. For permissions, recovery and limitations,
see [release operations](docs/RELEASES.md).

## Platform

Screamer is built for Windows.

It depends on Windows-specific features including:

- global hotkeys (keyboard or mouse) via low-level hooks (`WH_KEYBOARD_LL`/`WH_MOUSE_LL`)
- text injection via `SendInput`
- tray integration
- DPAPI key storage
- startup registration through the current user Run key

## License

MIT
