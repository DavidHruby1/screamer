# Screamer

Fast Windows dictation that types wherever your cursor is.

Hold a hotkey, speak, release. Screamer records your voice, sends it to a speech-to-text provider, optionally cleans up the result with an LLM, and types the text into the window you were in when you started recording. It can also copy the text, or do both.

```text
Hotkey -> Record -> Transcribe (STT) -> AI cleanup (optional) -> Type / copy
```

Works with OpenAI, Groq, or any OpenAI-compatible `/audio/transcriptions` endpoint, including local servers. API keys are stored locally with Windows DPAPI.

## Install

1. Download `Screamer-vX.Y.Z-windows-x64.zip` from the **Releases** page.
2. Extract the zip and open the `Screamer` folder.
3. Run `Screamer.exe`.

## Quick start

1. On first launch, the **Setup guide** opens. (Later: right-click the tray icon, three bars at the bottom right, → **Setup guide...**)
2. Pick your microphone and check that the level bar moves when you talk.
3. Pick a speech provider (OpenAI, Groq or a local server), paste your API key, click **Test connection**, then **Save**.
4. With the guide still focused, hold `Ctrl+Alt+Space`, say a sentence and release. The text appears in the guide's practice box.
5. Close the guide. From now on the text is typed into whatever app you dictate into.

Provider presets fill in the base URL and model; you can still edit both:

```text
OpenAI:  https://api.openai.com/v1        whisper-1
Groq:    https://api.groq.com/openai/v1   whisper-large-v3-turbo
Local:   http://127.0.0.1:18000/v1        (Speaches, no API key)
```

Any server compatible with OpenAI `/audio/transcriptions` works; Screamer appends that path to the base URL. See the [local STT setup](docs/LOCAL-STT.md).

## Dictating

- **Hold to talk** (default): records while you hold the hotkey.
- **Toggle**: first press starts recording, second press stops it.
- **Esc** cancels the recording; nothing is sent or typed.
- **Hold Shift** as you finish (when releasing the hotkey, or on the stopping press in Toggle mode) to press **Enter** after the text, e.g. to send a chat message.

While recording, a small pill appears near the bottom of the screen:

- **Orange**: recording. The wave shows your microphone level, and the microphone name is shown.
- **Blue**: processing (transcription and optional AI cleanup).

Short sounds mark start, stop, cancel and errors (Settings → General can turn them off).

Text goes into the window that was active when recording started. If the window or application changed in the meantime, Screamer holds the text back instead of typing it somewhere else. Use **Insert last** (`Ctrl+Alt+Shift+V` by default) in the right app, or open **History**.

You can keep dictating while Settings is open, for example to try a new prompt right after **Apply**. Dictation uses the last saved settings.

## Tray menu

| Item | What it does |
| --- | --- |
| Enabled | Turns the hotkey on/off. |
| Language | Transcription language: Auto, Czech, English and your favorites. |
| Profile | How text is cleaned up: Auto (by app), Raw, Clean, Polished or your own. |
| Copy last | Copies the latest result (or the one selected in History). |
| History... | Latest results: copy, insert, rerun cleanup, delete. |
| Settings... | Opens Settings. |
| Setup guide... | Microphone check, provider setup and a practice dictation. |
| Exit | Quits the app. |

Changes made during dictation apply to the next recording.

## Settings

**Apply** saves, **OK** saves and closes, **Cancel** discards unsaved changes. **Import from .env** loads keys and URLs from a `.env` file; **Reset to Defaults** restores default values.

### General

- **Hotkey**: a preset (`Ctrl+Alt+Space`, `Ctrl+Shift+Space`, `Ctrl+Alt+D`, `Ctrl+Alt+S`, `Ctrl+Alt+V`, `Scroll Lock`, `Pause`) or a custom one: click **Register Hotkey** and press any combination, a function key, or a side/middle mouse button. Ordinary keys need a modifier.
- **Recording mode**: Hold to talk / Toggle.
- **Output**:
  - **Type into the app** (default) types the text key by key.
  - **Paste via clipboard** is faster for long texts; your previous clipboard is put back afterwards.
  - **Copy to clipboard only**, or **Copy and type**.
- **Then press**: a key pressed after every result (Enter, Tab, ...). Shift-at-the-end works without it.
- **Insert last shortcut**: types the latest result into the current app.
- **Sounds**, **History** (opt-in, encrypted, 1–100 entries, never audio) and **Start with Windows**.

### Speech

- **Provider**: a preset or Custom, then **API key** (optional for local servers), **Base URL** and **Model**. **Test connection** checks the server and whether it lists the model.
- **Language**: Auto, or a specific language (often more accurate). Tick the languages you want in the tray menu under **Also show in tray**.
- **Advanced**: custom HTTP headers, sending your vocabulary to the provider, and a fallback provider used when the main one fails.

### Rewrite

The language model used by the Clean, Polished and custom profiles: preset, **API key** (optional for local servers such as Ollama or LM Studio), **Base URL**, **Model**, **Test connection**. Custom headers are under **Advanced**.

> AI can occasionally change the meaning, so double-check important dictation. If cleanup fails, the raw transcript is typed.

### Audio

- **Microphone**: **System Default** follows the Windows default. **Refresh** after plugging a device in or out; an unavailable microphone is never silently swapped for another.
- **Mute speakers while recording**: silences music and calls while you talk, then restores them.
- **Cut silence at the start and end**: sends less audio, which is faster and avoids made-up words in silent parts.
- **Advanced: silence threshold**: recordings quieter than the threshold are dropped. **Calibrate** measures your room.

### Vocabulary

- **Preferred spellings**: names and terms (e.g. `PySide6`). The AI rewrite uses them as hints; transcription gets them only if you enable it under Speech → Advanced.
- **Replacements**: exact fixes applied to every result, whole words only, ignoring case. Example: heard `pajsajd` → write `PySide6`. They work even with the Raw profile.

### Profiles

- **Use**: Raw (no AI), **Clean** (conservative fixes), **Polished** (also removes filler words and keeps only the final version when you correct yourself), your own profiles, or **Auto**.
- **Auto** picks a profile by the app you dictate into; otherwise the **When no app matches** profile.
- **Edit profiles**: built-ins are read-only; **Duplicate** one or click **New profile**, then edit its name and prompt in place.
- **Apps**: type an `.exe` name (e.g. `slack.exe`) or **Choose running app...**, pick a profile, **Add / update**.

Example: Polished for Slack, Raw for the terminal, a custom "formal" profile for email.

## History

**Tray → History...** shows the latest results, even with history turned off.

| Button | What it does |
| --- | --- |
| Copy raw / Copy final | Copies the raw / cleaned-up text. |
| Use raw / Use result for insertion | Picks the text for **Insert last**. Switch to the target app and press the shortcut. |
| Rerun cleanup | Runs AI cleanup again with current settings (the original is kept). |
| Retry STT | Shown only after a failed transcription: sends the same recording again. |
| Discard audio | Drops that pending recording from memory. |
| Delete / Clear all | Deletes the selected entry / all entries. |

## Tips and troubleshooting

- **Nothing was typed**: press **Insert last** in the right app, or check **History**. Most often the active window changed in the meantime.
- **Inaccurate transcription**: set a specific language instead of Auto, try a more accurate model, add **Replacements** for words it keeps getting wrong.
- **Recording wasn't sent**: it may have been below the silence threshold; use **Calibrate** under Audio → Advanced.
- **Microphone doesn't work**: **Settings → Audio → Refresh** and select the device again. No restart needed.
- **Offline dictation**: use a local STT server, and either the Raw profile or a local LLM server.
- **"Some saved settings were damaged and reset"**: the damaged value is kept in `%LOCALAPPDATA%\Screamer\settings-damaged.txt`.
- Logs are in `%LOCALAPPDATA%\Screamer\screamer.log`, including a per-dictation timing line (transcription, rewrite, output). Only one instance can run at a time.

### Privacy notes

History stores dictated text (including anything sensitive you say), never audio, API keys, or system prompts. DPAPI protects it at rest under your Windows account, not against that account itself, backups, clipboard readers, or other apps. Clearing history doesn't guarantee secure erasure from backups and doesn't clear the clipboard. See [the storage and delivery contracts](docs/IMPLEMENTATION.md).

## For developers

See [implementation and API contracts](docs/IMPLEMENTATION.md),
[the product review and roadmap status](docs/PRODUCT-REVIEW-2026-10.md),
[the implementation plan](docs/IMPLEMENTATION-PLAN.md),
and [release operations](docs/RELEASES.md).

Measure transcription latency (median and p95) on the same recordings:

```bash
python -m src.stt --repeat 5 cs.wav en.wav mixed.wav
```

Run from source:

```bash
pip install -r requirements.txt
python -m src.main
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

Build the Windows executable:

```bash
pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean screamer.spec
```

CI builds the Windows executable on pull requests to catch packaging failures before merge.

### Releases

After a reviewed merge to `main` with successful CI and CodeQL, the release workflow picks a version relative to the latest stable release, builds and tests on Windows, and publishes a GitHub Release with:

```text
Screamer-vX.Y.Z-windows-x64.zip
Screamer-vX.Y.Z-windows-x64.zip.sha256
```

Breaking changes cause a major bump, `feat:` a minor bump, anything else a patch bump. See [release operations](docs/RELEASES.md).

## Platform

Windows only. Screamer relies on low-level keyboard/mouse hooks, `SendInput` text injection, the system tray, DPAPI key storage, and the current-user Run key for startup.

## License

MIT
