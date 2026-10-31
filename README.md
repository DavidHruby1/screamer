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

1. A tray icon (three bars) appears in the notification area, bottom right. Right-click it and choose **Settings...**
2. On the **Speech** page, fill in your transcription provider (**Base URL**, **Model**, **API Key**) and click **OK**.
3. Click into any text field, hold `Ctrl+Alt+Space`, say something, and release.

Example: OpenAI

```text
Base URL: https://api.openai.com/v1
Model:    whisper-1
API Key:  your key
```

Example: Groq (fast)

```text
Base URL: https://api.groq.com/openai/v1
Model:    whisper-large-v3-turbo     (more accurate: whisper-large-v3)
```

Any server compatible with OpenAI `/audio/transcriptions` works; Screamer appends that path to the base URL. For a local server, the API key may be left blank. See the [local STT setup](docs/LOCAL-STT.md).

## Dictating

- **Hold to talk** (default): records while you hold the hotkey.
- **Toggle**: first press starts recording, second press stops it.

While recording, a small pill appears near the bottom of the screen:

- **Orange**: recording. The wave shows your microphone level, and the microphone name is shown.
- **Blue**: processing (transcription and optional AI cleanup).

Text is typed into the window that was active when recording started. If the window or application changed in the meantime, Screamer holds the text back instead of typing it somewhere else. You'll find it in **Recovery**.

## Tray menu

Right-click the tray icon for quick toggles without opening Settings.

| Item | What it does |
| --- | --- |
| Enabled | Turns hotkey handling on/off. |
| Record Mode | Hold to talk or Toggle. |
| Hotkey | Pick a hotkey preset. |
| Language | Transcription language (Auto, Czech, English, favorites). |
| Post-type Key | Key pressed after typing (Enter, Tab, ...). |
| AI Rewrite | Turns AI cleanup on/off. Off = always raw transcript. |
| Rewrite Profile | Cleanup profile (Raw, Clean, custom, Automatic). |
| Settings... | Opens Settings. |
| Recovery... | Latest results: copy, retry, delete. |
| Exit | Quits the app. |

Changes made during dictation apply to the next recording.

## Settings

**Apply** saves, **OK** saves and closes, **Cancel** discards unsaved changes. **Import from .env** loads keys and URLs from a `.env` file; **Reset to Defaults** restores default values.

### General

How dictation starts and where the text goes.

**Shortcut**

- **Hotkey**: a preset (`Ctrl+Alt+Space`, `Ctrl+Shift+Space`, `Ctrl+Alt+D`, `Ctrl+Alt+S`, `Ctrl+Alt+V`, `Scroll Lock`, `Pause`) or a custom one: click **Register Hotkey** and press any combination, a function key, or a side/middle mouse button. Ordinary keys need a modifier (Ctrl/Alt/Shift). The hotkey press is not passed through to the app underneath.
- **Recording mode**: Hold to talk / Toggle.

**Output**

- **Post-type key**: presses `Enter`, `Tab`, `Space`, or `Backspace` after typing (e.g. to send a chat message right away).
- **Output mode**: type into the app (default), copy to clipboard only, or both. Copying replaces your clipboard and does not restore it. Copy-only never sends a post-type key.
- **Recovery shortcut**: shortcut for inserting text from Recovery (default `Ctrl+Alt+Shift+V`).

**History**

- **Save local text history**: optionally keeps the last 1–100 texts, encrypted under your Windows account. Off by default. Audio is never written to disk.
- **Retain latest results**: how many entries to keep.
- **Clear History...**: deletes saved history. Turning history off does not delete anything by itself.

**System**

- **Start Screamer with Windows**: launch on login.

### Speech

Speech-to-text.

**Primary provider**

- **API Key**, **Base URL**, **Model**: provider connection. Keys are stored encrypted (Windows DPAPI). The eye icon reveals the key.
- **Custom Headers**: optional HTTP headers as JSON, e.g. `{"X-Custom": "value"}`.
- **Send vocabulary prompt to primary STT**: also sends your vocabulary to transcription. Enable only if your server supports the `prompt` field.

**Language**

- **Active language**: Auto, or a specific language (often more accurate than Auto). Language hints don't translate and don't handle mixed-language speech.
- **Favorite languages**: your own list of codes (ISO 639-1, e.g. `de`) that then show up in the tray **Language** menu. Add / edit / remove with the buttons below the field.

**Fallback**

- **Enable fallback STT provider**: a backup provider used when the primary one fails.

### Rewrite

Optional AI cleanup of the transcript: punctuation, grammar, capitalization.

- **Enable AI rewrite**: main switch. Off = raw transcript.
- **API Key**, **Base URL**, **Model**, **Custom Headers**: LLM connection (OpenAI-compatible).
- **System Prompt**: instructions for the model for the selected profile. **Reset to Current Default** restores the current default cleanup prompt.
- **Enable fallback LLM provider**: a backup model.

> AI can occasionally change the meaning, so double-check important dictation. If cleanup fails, the raw transcript is typed.

### Audio

Microphone and silence detection.

- **Input Device**: your microphone. **System Default** follows the current Windows default device. Avoid devices like Stereo Mix, Monitor, or HDMI.
- **Refresh**: reloads the list after plugging/unplugging a microphone. An unavailable microphone is never silently swapped for another one.
- **Recalibrate RMS Threshold**: measures background noise and sets the silence threshold. **RMS Threshold** can also be set manually; recordings below it count as silence and are not sent.

### Vocabulary

Preferred spellings of names and terms.

- Add words that should be written exactly that way (e.g. `PySide6`, colleagues' names, project names).
- Vocabulary guides AI cleanup; it goes to transcription only for providers where you explicitly enable it on the **Speech** page.
- Limits: 128 terms, 128 characters per term. It is not a replacement table and does not guarantee recognition.

### Profiles

Which prompt cleans up the text, and where.

- **Raw**: no AI, plain transcript. **Clean**: conservative cleanup. You can add your own profiles (**Add custom** / **Rename** / **Delete**) and edit their prompt with **Edit selected prompt on LLM tab**.
- **Selection mode: Manual**: always uses the chosen **Manual profile**.
- **Selection mode: Automatic**: picks a profile based on the app you're dictating into. In **Application mappings**, map a full `.exe` path (e.g. `C:\Program Files\Slack\slack.exe`) to a profile; other apps get the **Default profile**. Moving an app to another path requires remapping.

Example: Clean for Slack, Raw for the terminal, a custom "formal" profile for email.

## Recovery

**Tray → Recovery...** shows the latest results, even with history turned off.

| Button | What it does |
| --- | --- |
| Copy raw / Copy final | Copies the raw / cleaned-up text to the clipboard. |
| Arm raw / Arm final | The window hides; click into the target app and press the recovery shortcut to insert the text. `Esc` cancels. |
| Rerun cleanup | Runs AI cleanup again with current settings (the original is kept). |
| Retry STT | After a failed transcription, sends the same recording again. |
| Discard audio | Drops the pending recording from memory. |
| Delete / Clear all | Deletes the selected entry / all entries. |

## Tips and troubleshooting

- **Nothing was typed**: check **Recovery**, the text will be there. Most often the active window changed in the meantime.
- **Inaccurate transcription**: set a specific language instead of Auto, try a more accurate model, add terms to **Vocabulary**.
- **Recording wasn't sent**: it may have been below the silence threshold; run **Recalibrate RMS Threshold**.
- **Microphone doesn't work**: **Settings → Audio → Refresh** and select the device again. No restart needed.
- **Offline dictation**: use a local STT server and turn off AI rewrite and both fallbacks.
- Logs are in `%LOCALAPPDATA%\Screamer\screamer.log`. Only one instance can run at a time.

### Privacy notes

History stores dictated text (including anything sensitive you say), never audio, API keys, or system prompts. DPAPI protects it at rest under your Windows account, not against that account itself, backups, clipboard readers, or other apps. Clearing history doesn't guarantee secure erasure from backups and doesn't clear the clipboard. See [the storage and delivery contracts](docs/IMPLEMENTATION.md).

## For developers

See [implementation and API contracts](docs/IMPLEMENTATION.md),
[planned features](docs/FEATURES.md), [their implementation plan](docs/IMPLEMENTATION-PLAN.md),
and [release operations](docs/RELEASES.md).

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
