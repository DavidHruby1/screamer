# OVI Project Summary

> **Status:** This document describes the original OVI codebase that Screamer was forked from.
> All recommended improvements listed below have been implemented in Screamer.
> See `PLAN.md` for the final architecture and `IMPLEMENTATION.md` for the build sequence.

This document is a complete architecture and implementation summary of the current OVI codebase. It is intended to give another AI agent enough context to understand, modify, extend, or fork the project without first reverse-engineering the whole repository.

OVI stands for Open Voice Interface. The current project is a small desktop prototype for system-wide push-to-talk dictation. The user holds a global hotkey, speaks into the microphone, releases the hotkey, and the app transcribes the captured audio using an OpenAI-compatible speech-to-text API. The resulting text is typed into whichever application is currently focused. The primary intended STT provider is Groq, but the implementation is deliberately provider-agnostic as long as the provider exposes an OpenAI-compatible `/audio/transcriptions` endpoint.

The application is currently a working MVP rather than a polished desktop product. It has a tray app, audio capture, global hotkeys, STT fallback, optional LLM cleanup, and keyboard text injection. It does not yet have persistent settings, a dedicated settings window, device selection, structured logging, packaging, streaming, or production-grade platform integration.

## Repository Layout

Root files:

- `main.py`: PyQt6 system tray application and top-level runtime state machine.
- `audio.py`: Microphone capture module that records 16 kHz mono int16 WAV bytes and filters short or silent recordings.
- `hotkey.py`: Global keyboard and mouse push-to-talk listener built on `pynput`.
- `transcriber.py`: OpenAI-compatible STT client, fallback STT logic, optional LLM rewrite, hallucination filtering, and keyboard text injection.
- `generate_icons.py`: Utility that generates simple tray icon PNG files.
- `README.md`: User-facing setup and usage documentation.
- `.env.example`: Example environment configuration for STT, fallback STT, LLM rewrite, fallback LLM, and custom headers.
- `requirements.txt`: Python package dependencies.
- `assets/icon_idle.png`: Grey microphone tray icon for idle state.
- `assets/icon_recording.png`: Red microphone tray icon for recording state.
- `assets/icon_processing.png`: Yellow microphone tray icon for processing/transcribing state.
- `LICENSE`: GNU AGPLv3 license.

There is no package directory yet. All application modules live directly in the repository root and import each other by filename.

## Purpose And Product Shape

The current app is a system-wide dictation utility with this user flow:

1. Start the app with `python main.py`.
2. The app appears in the system tray.
3. Hold the configured hotkey. The default is left Ctrl.
4. Speak while the key is held.
5. Release the hotkey.
6. OVI stops recording and converts the audio to WAV bytes.
7. OVI discards the recording if it is too short or too quiet.
8. OVI sends the WAV to the configured STT API.
9. If primary STT fails or returns no text, OVI tries a fallback STT provider if configured.
10. OVI filters known hallucination phrases from empty/silent Whisper outputs.
11. If AI rewrite is enabled and configured, OVI sends the transcription to an LLM for minimal correction.
12. OVI types the resulting text into the active window using `pynput`.
13. OVI optionally presses a post-type key such as Enter, Tab, Space, or Backspace.
14. OVI returns to idle and waits for the next hotkey press.

The project is a practical foundation for a WisprFlow-like app but is not yet a complete replacement. The MVP is intentionally simple: record one utterance, transcribe after release, type the result.

## Runtime Architecture

The runtime architecture has four major components:

- Tray/UI layer: `OVITrayApp` in `main.py`.
- Audio capture layer: `AudioRecorder` in `audio.py`.
- Hotkey listener layer: `HotkeyListener` in `hotkey.py`.
- Transcription/output layer: `Transcriber` in `transcriber.py`.

The top-level orchestration lives in `main.py`. `OVITrayApp` creates one instance of each core component, wires callbacks between them, and owns the state machine.

The key architectural pattern is callback-driven coordination:

- `HotkeyListener` detects global key/mouse press and release events.
- `HotkeyListener` calls callbacks provided by `OVITrayApp`.
- `OVITrayApp` starts/stops `AudioRecorder` based on those callbacks.
- `OVITrayApp` passes recorded WAV bytes to `Transcriber` in a background worker thread.
- `Transcriber` calls remote APIs, optionally rewrites text, and types into the active app.
- `OVITrayApp` updates tray icon/tooltips/messages using Qt signals.

The code uses a minimal state machine in `OVITrayApp`:

- `idle`: Ready to record.
- `recording`: Hotkey is held and microphone input is being captured.
- `processing`: Audio is being transcribed, optionally rewritten, and typed.

The state machine prevents overlapping recordings and prevents duplicate processing while a transcription is already running.

## Main Application: `main.py`

`main.py` is the entry point and contains the tray application.

Important imports:

- `PyQt6.QtCore.QObject`, `pyqtSignal` for thread-safe UI updates.
- `PyQt6.QtGui.QAction`, `QActionGroup`, `QIcon` for tray menu and icons.
- `PyQt6.QtWidgets.QApplication`, `QMenu`, `QSystemTrayIcon` for desktop tray UI.
- `AudioRecorder` from `audio.py`.
- `Transcriber` from `transcriber.py`.
- `HotkeyListener`, `KEY_MAP`, `MOUSE_MAP` from `hotkey.py`.

### `SignalBridge`

`SignalBridge` is a small `QObject` subclass with two Qt signals:

- `state_changed = pyqtSignal(str)`: Carries state strings such as `idle`, `recording`, and `processing`.
- `status_message = pyqtSignal(str)`: Carries user-facing tray notification text.

This exists because `OVITrayApp` starts a Python worker thread for transcription. Qt UI objects should be updated from the Qt main thread, not directly from worker threads. The worker emits signals through `SignalBridge`, and Qt dispatches the connected handlers safely in the UI thread.

### `OVITrayApp.__init__`

The constructor creates and wires the app:

- Creates `QApplication(sys.argv)`.
- Disables quitting when windows close with `setQuitOnLastWindowClosed(False)`, because this is a tray app.
- Sets the app name to `OVI`.
- Creates `AudioRecorder()`.
- Creates `Transcriber()`.
- Creates `HotkeyListener()` with `self._on_hotkey_pressed` and `self._on_hotkey_released` callbacks.
- Registers `self._hotkey_listener.set_typing` as the typing callback in the transcriber.
- Creates `SignalBridge()` and connects its signals to `_set_state` and `_show_message`.
- Loads tray icons from the `assets` directory.
- Creates a `QSystemTrayIcon`, assigns the idle icon, sets the idle tooltip, attaches the context menu, and shows the icon.
- Initializes `_state` to `idle`.

The typing callback wiring is important. When `Transcriber.type_text()` types output using `pynput`, those synthetic keyboard events could otherwise be observed by the global hotkey listener. `Transcriber` calls the callback with `True` before typing and `False` after typing. `HotkeyListener` uses that flag to ignore events during text injection.

### Tray Menu

`OVITrayApp._build_menu()` creates the tray context menu.

Current menu items:

- `Enabled`: A checked/unchecked toggle that enables or disables the hotkey listener.
- `Hotkey`: A submenu with predefined hotkey choices.
- `Post-type key`: A submenu controlling whether OVI presses an extra key after typing.
- `AI Rewrite`: A checked/unchecked toggle for LLM post-processing.
- `Exit`: Stops listeners, hides tray icon, and quits Qt.

Current hotkey options in the menu:

- `ctrl`
- `alt`
- `pause`
- `f13`
- `f14`
- `scroll_lock`
- `mouse4`
- `mouse5`

The default checked hotkey in the UI is `ctrl`, matching the default in `HotkeyListener`.

Current post-type key options:

- `None`
- `Enter`
- `Tab`
- `Space`
- `Backspace`

The default is `None`.

The `AI Rewrite` action defaults to `self._transcriber._rewrite_enabled`, which is true only if an LLM client was configured with `LLM_API_KEY`. This currently reaches into a private transcriber attribute, which works but is not ideal API design.

### State Transitions

The main state transition methods are `_on_hotkey_pressed`, `_on_hotkey_released`, and `_worker`.

When the hotkey is pressed:

```text
idle -> recording
```

`_on_hotkey_pressed()` checks `self._state`. If the app is not idle, the press is ignored. If idle, it emits `recording` through the bridge and starts `AudioRecorder.start_recording()`.

When the hotkey is released:

```text
recording -> processing -> idle
```

`_on_hotkey_released()` checks that the state is `recording`. If not, it ignores the release. It then calls `AudioRecorder.stop_recording()`.

If `stop_recording()` returns `None`, the recording was too short, empty, or silent. The state returns to `idle` and no API call is made.

If audio bytes are returned, the app emits `processing` and starts a daemon `threading.Thread` targeting `_worker(audio_bytes)`.

`_worker()` performs the slow work:

1. Calls `self._transcriber.transcribe(audio_bytes)`.
2. If text is returned, calls `self._transcriber.rewrite(text)`.
3. Calls `self._transcriber.type_text(text)`.
4. Emits a tray message like `Typed: <first 60 chars>`.
5. If no text is returned, emits `No speech detected.`.
6. Emits `idle` when done.

This worker thread keeps the Qt event loop responsive while network APIs run.

### Tray Icons And Tooltips

The app uses three icon states:

- `idle`: `assets/icon_idle.png`, grey microphone.
- `recording`: `assets/icon_recording.png`, red microphone.
- `processing`: `assets/icon_processing.png`, yellow microphone.

`_set_state()` stores the current state, changes the tray icon, and updates the tooltip.

Tooltips:

- Idle: `OVI — Voice Dictation (Idle)`
- Recording: `OVI — Recording...`
- Processing: `OVI — Transcribing...`

The `Enabled` toggle currently sets the tooltip to `Enabled` or `Disabled`, but later state changes can overwrite this tooltip. There is no dedicated enabled/disabled state in the state machine.

### App Startup And Shutdown

`OVITrayApp.run()` starts the hotkey listener, emits a startup tray message, and enters Qt's event loop:

```python
self._hotkey_listener.start()
self._bridge.status_message.emit("OVI ready. Hold Ctrl and speak.")
sys.exit(self._app.exec())
```

`_quit()` stops the hotkey listener, hides the tray icon, and quits the QApplication.

## Audio Capture: `audio.py`

`audio.py` contains the `AudioRecorder` class and constants for recording format and filtering.

Key constants:

- `SAMPLE_RATE = 16000`
- `CHANNELS = 1`
- `DTYPE = "int16"`
- `MIN_DURATION = 0.3`
- `SILENCE_THRESHOLD = 50`

This audio format is appropriate for Whisper-style speech-to-text systems and for Groq STT:

- 16 kHz sample rate is widely accepted by Whisper.
- Mono audio avoids unnecessary channels and upload size.
- int16 PCM WAV is simple and compatible.

### `AudioRecorder` State

`AudioRecorder` keeps:

- `_frames`: A list of NumPy arrays collected from the sounddevice callback.
- `_stream`: The current `sounddevice.InputStream`, or `None` when not recording.
- `_lock`: A `threading.Lock` protecting frame access between callback and main logic.
- `_start_time`: Monotonic timestamp for duration filtering.

### Recording Start

`start_recording()`:

1. Clears existing frames under the lock.
2. Stores `time.monotonic()` in `_start_time`.
3. Creates a `sounddevice.InputStream` with the configured sample rate, channels, dtype, and callback.
4. Starts the stream.

The current implementation uses the system default input device. There is no microphone selection yet.

### Sounddevice Callback

`_callback(indata, frames, time_info, status)` is called by `sounddevice` as audio buffers arrive.

If `status` is non-empty, it prints `[audio] <status>`. Then it appends a copy of the input data to `_frames` under the lock.

Copying `indata` is important because sounddevice may reuse buffers after the callback returns.

### Recording Stop

`stop_recording()`:

1. Returns `None` immediately if there is no active stream.
2. Stops and closes the stream.
3. Sets `_stream` to `None`.
4. Computes recording duration.
5. Returns `None` if the duration is under `MIN_DURATION`.
6. Concatenates collected NumPy frames under the lock.
7. Returns `None` if no frames were captured.
8. Computes RMS audio energy.
9. Returns `None` if RMS is below `SILENCE_THRESHOLD`.
10. Writes the audio as WAV into an in-memory `io.BytesIO` buffer.
11. Returns the WAV bytes.

### Silence Filtering

Silence filtering uses RMS:

```python
rms = np.sqrt(np.mean(audio_data.astype(np.float64) ** 2))
if rms < SILENCE_THRESHOLD:
    return None
```

This prevents API calls for empty or near-empty recordings and avoids common Whisper hallucinations such as short thank-you phrases on silence.

The silence filter is simple but useful. It is not a full VAD. Future improvements could add real voice activity detection, pre-roll, noise calibration, adaptive thresholds, or device-specific thresholds.

### Standalone Test Mode

Running `python audio.py` records 3 seconds and writes `test.wav` if audio is captured. This is useful for debugging microphone access and audio format independent of the tray app.

## Hotkey Handling: `hotkey.py`

`hotkey.py` implements global push-to-talk input using `pynput`.

It supports both keyboard keys and mouse side buttons.

### Key Maps

`KEY_MAP` maps user-friendly string names to `pynput.keyboard.Key` values:

- `ctrl`, `ctrl_l`, `ctrl_r`
- `alt`, `alt_l`, `alt_r`
- `pause`
- `f13`, `f14`, `f15`, `f16`
- `scroll_lock`

`MOUSE_MAP` maps:

- `mouse4` to `Button.x1`
- `mouse5` to `Button.x2`

The tray menu only exposes a subset of keys: Ctrl, Alt, Pause, F13, F14, Scroll Lock, Mouse 4, and Mouse 5. The lower-level map includes F15 and F16 but the menu does not currently expose them.

### `HotkeyListener` State

`HotkeyListener` stores:

- `_on_press_cb`: Callback invoked when push-to-talk starts.
- `_on_release_cb`: Callback invoked when push-to-talk ends.
- `_hotkey`: Current `pynput` `Key` or mouse `Button`.
- `_is_mouse`: Whether the current hotkey is a mouse button.
- `_enabled`: Whether hotkey events are accepted.
- `_is_pressed`: Guard against key-repeat and duplicate callbacks.
- `_typing`: Whether synthetic typing is currently happening.
- `_kb_listener`: Global keyboard listener.
- `_mouse_listener`: Global mouse listener.

The default hotkey is `Key.ctrl_l`.

### Changing Hotkeys

`set_hotkey(key_name)` lowercases the requested name and checks `MOUSE_MAP` first, then `KEY_MAP`.

If the name is found in `MOUSE_MAP`:

- `_hotkey` is set to the mouse button.
- `_is_mouse` is set to `True`.
- `_is_pressed` is reset.

If the name is found in `KEY_MAP`:

- `_hotkey` is set to the keyboard key.
- `_is_mouse` is set to `False`.
- `_is_pressed` is reset.

Invalid names are silently ignored. There is no user-facing validation error.

### Enabling And Disabling

`set_enabled(enabled)` toggles whether hotkey events are accepted. If disabled, `_is_pressed` is reset so the app does not get stuck in a pressed state.

### Typing Suppression

`set_typing(is_typing)` is called by the transcriber during text injection. When `_typing` is true, keyboard and mouse callbacks ignore events. This reduces the risk of OVI reacting to its own synthetic key output.

### Keyboard Callback Flow

`_on_press(key)` ignores events when:

- The listener is disabled.
- OVI is currently typing synthetic output.
- The configured hotkey is a mouse button.

If the pressed key matches `_hotkey` and `_is_pressed` is false, it sets `_is_pressed` to true and calls `_on_press_cb()`.

`_on_release(key)` has the same guards. If the released key matches `_hotkey` and `_is_pressed` is true, it sets `_is_pressed` to false and calls `_on_release_cb()`.

### Mouse Callback Flow

`_on_click(x, y, button, pressed)` ignores events when:

- The listener is disabled.
- OVI is typing synthetic output.
- The configured hotkey is not a mouse button.

If the button does not match `_hotkey`, the event is ignored.

If the matching button is pressed and `_is_pressed` is false, it starts push-to-talk. If the matching button is released and `_is_pressed` is true, it ends push-to-talk.

### Listener Lifecycle

`start()` creates both a keyboard listener and mouse listener, marks both daemon threads, and starts them.

`stop()` stops and clears whichever listeners exist.

### Platform Considerations

Because this is based on `pynput`, platform behavior depends heavily on OS permissions and display server support.

Known likely issues:

- Wayland on Linux can block global keyboard listeners or synthetic typing.
- macOS requires accessibility permissions for global hotkeys and keyboard injection.
- Windows is generally the easiest target, but tray icon visibility can still depend on user settings.

## Transcription And Output: `transcriber.py`

`transcriber.py` currently does several jobs:

- Loads environment configuration.
- Creates OpenAI-compatible clients for STT and LLM endpoints.
- Calls primary and fallback STT APIs.
- Filters known hallucination phrases.
- Optionally rewrites the transcription with an LLM.
- Types text into the active window.
- Optionally presses a post-type key.

This makes `Transcriber` central to the prototype but also too broad for a larger app. A future refactor should split it into separate STT client, rewrite client, output injector, and configuration modules.

### Environment Loading

`transcriber.py` calls `load_dotenv()` at import time. This loads variables from `.env` into the process environment.

This is simple for a prototype but not ideal for a desktop product. Future versions should use persistent app settings, secure API key storage where possible, and a UI for editing settings.

### Header Parsing

`_parse_headers(raw)` parses JSON strings from environment variables such as `STT_HEADERS` and returns a `dict[str, str]` or `None`.

Example:

```env
STT_HEADERS={"HTTP-Referer": "https://ovi.app", "X-Title": "OVI"}
```

This is useful for providers like OpenRouter that require extra HTTP headers.

If parsing fails, the code prints a warning and returns `None`. The warning currently says `STT_HEADERS` even when parsing LLM or fallback headers, because the helper does not know which variable it is parsing.

### Client Creation

`_make_client(api_key, base_url, headers)` returns an `OpenAI` client if `api_key` is non-empty. It passes:

- `api_key`
- `base_url`
- optional `default_headers`

If no API key is present, it returns `None`.

OpenAI-compatible local servers can be used by setting a dummy API key such as `local`.

### STT Configuration

Primary STT configuration:

- `STT_API_KEY`, default empty.
- `STT_BASE_URL`, default `https://api.groq.com/openai/v1`.
- `STT_MODEL`, default `whisper-large-v3`.
- `STT_HEADERS`, default empty.

Fallback STT configuration:

- `STT_FALLBACK_API_KEY`, default empty.
- `STT_FALLBACK_BASE_URL`, default `https://api.groq.com/openai/v1`.
- `STT_FALLBACK_MODEL`, default `whisper-large-v3`.
- `STT_FALLBACK_HEADERS`, default empty.

Language configuration:

- `STT_LANGUAGE`, default unset.
- If unset, STT language is auto-detected by the provider.
- If set, it is passed to the transcription endpoint as `language`.

The fork overview recommends changing the preferred model defaults to:

- Default primary: `whisper-large-v3-turbo`
- Quality fallback: `whisper-large-v3`

The current code still defaults to `whisper-large-v3` for both primary and fallback.

### LLM Rewrite Configuration

Primary LLM configuration:

- `LLM_API_KEY`, default empty.
- `LLM_BASE_URL`, default `https://api.groq.com/openai/v1`.
- `LLM_MODEL`, default `meta-llama/llama-4-scout-17b-16e-instruct`.
- `LLM_HEADERS`, default empty.

Fallback LLM configuration:

- `LLM_FALLBACK_API_KEY`, default empty.
- `LLM_FALLBACK_BASE_URL`, default `https://api.groq.com/openai/v1`.
- `LLM_FALLBACK_MODEL`, default `llama-3.1-8b-instant`.
- `LLM_FALLBACK_HEADERS`, default empty.

`_rewrite_enabled` defaults to true when `_llm_client` exists, which means `LLM_API_KEY` was configured. Otherwise it defaults to false.

### Post-Type Key Configuration

`POST_KEY_MAP` maps string names to `pynput.keyboard.Key` objects:

- `enter`
- `tab`
- `space`
- `backspace`

`set_post_key(key_name)` sets `_post_key` to the mapped key if the key name is valid. Otherwise it clears `_post_key`.

### STT API Call

`_call_api(client, model, audio_bytes)` sends WAV bytes to an OpenAI-compatible STT endpoint.

Implementation details:

- Wraps `audio_bytes` in `io.BytesIO`.
- Sets `audio_file.name = "recording.wav"` so the client treats it like an uploaded WAV file.
- Builds kwargs with `model` and `file`.
- Adds `language` if `STT_LANGUAGE` was configured.
- Calls `client.audio.transcriptions.create(**kwargs)`.
- Strips `response.text`.
- Returns the text if non-empty, otherwise `None`.

This assumes the response object has a `.text` field, matching OpenAI SDK transcription responses.

### STT Fallback Flow

`transcribe(audio_bytes)` performs this flow:

1. Initialize `text = None`.
2. If primary STT client exists, try `_call_api()` with the primary model.
3. If the primary call raises an exception, print `[transcriber] Primary STT error: ...`.
4. If no text was returned and fallback STT client exists, try `_call_api()` with the fallback model.
5. If fallback raises an exception, print `[transcriber] Fallback STT error: ...`.
6. If text is still `None`, return `None`.
7. Filter known hallucination phrases.
8. Return text.

Fallback is only used if primary fails by exception or returns no text. It is not used to compare transcription quality.

### Hallucination Filtering

`HALLUCINATION_PHRASES` contains known Whisper hallucinations that are common when audio is silent or nearly silent.

Current phrases include:

- `ďakujem za pozornosť`
- `ďakujem za sledovanie`
- `ďakujem`
- `thank you for watching`
- `thanks for watching`
- `thank you`
- `thanks for listening`
- `subtitles by`
- `subtitles made by`
- `подписывайтесь на канал`
- `you`

Filtering is done with:

```python
if text.lower().rstrip(".!?,") in HALLUCINATION_PHRASES:
    return None
```

This removes exact known phrases after lowercasing and stripping trailing punctuation. It does not remove longer strings that contain these phrases plus extra words.

### AI Rewrite Flow

`rewrite(text)` performs optional LLM cleanup.

It returns the original text if:

- Rewrite is disabled.
- There is no primary LLM client.
- Both primary and fallback LLM calls fail.
- The LLM returns an empty response.

The system prompt tells the model to:

- Fix spelling and grammar mistakes.
- Correct foreign words that were phonetically transcribed in the speech language.
- Make minimal changes.
- Avoid rephrasing or changing meaning/style.
- Return only corrected text.

The prompt includes the configured STT language if present, otherwise says `auto-detected`.

Primary LLM flow:

1. Calls `self._llm_client.chat.completions.create()`.
2. Sends a system prompt plus the transcription as the user message.
3. Reads `response.choices[0].message.content.strip()`.
4. If non-empty, prints the before/after rewrite and returns it.

Fallback LLM flow:

1. Only runs if primary failed or returned nothing.
2. Requires `_llm_fallback_client`.
3. Uses `_llm_fallback_model`.
4. Returns fallback result if non-empty.

If neither succeeds, the original text is returned.

### Text Injection

`type_text(text)` types into the active window using a global `pynput.keyboard.Controller` instance named `keyboard`.

Flow:

1. Sleep for 0.05 seconds so the user can finish releasing the hotkey.
2. If a typing callback exists, call it with `True`.
3. Call `keyboard.type(text)`.
4. If `_post_key` is configured, sleep for 0.02 seconds, then press and release that key.
5. In a `finally` block, call the typing callback with `False`.

The `finally` block is important because it clears the hotkey suppression flag even if keyboard injection fails.

### Standalone Test Mode

Running `python transcriber.py` reads `test.wav` by default, transcribes it, prints the result, waits 3 seconds, then types the result into the active window.

You can pass a custom WAV path:

```bash
python transcriber.py path/to/audio.wav
```

## Icon Generation: `generate_icons.py`

`generate_icons.py` creates simple 16x16 PNG microphone icons without using image libraries.

It implements:

- `create_png(width, height, pixels)`: Minimal RGBA PNG encoder using `struct` and `zlib`.
- `draw_mic_icon(color)`: Draws a tiny microphone body, arc, stand, and base into a pixel matrix.
- `main()`: Creates three icons under `assets/`.

Generated icon colors:

- `icon_idle.png`: Grey `(140, 140, 140)`.
- `icon_recording.png`: Red `(220, 40, 40)`.
- `icon_processing.png`: Yellow `(220, 180, 20)`.

The script assumes `assets/` already exists. It uses normal file writes in the script itself because it is a project utility.

These generated icons are acceptable for a prototype but should be replaced by proper app assets before packaging.

## Configuration Model

The current configuration model is environment-variable based. Users create a `.env` file in the project root. `python-dotenv` loads this file when `transcriber.py` is imported.

### STT Variables

Primary STT:

```env
STT_API_KEY=gsk_xxxxx
STT_BASE_URL=https://api.groq.com/openai/v1
STT_MODEL=whisper-large-v3
STT_LANGUAGE=sk
STT_HEADERS={"HTTP-Referer": "https://ovi.app", "X-Title": "OVI"}
```

Fallback STT:

```env
STT_FALLBACK_API_KEY=sk-xxxxx
STT_FALLBACK_BASE_URL=https://api.openai.com/v1
STT_FALLBACK_MODEL=whisper-1
STT_FALLBACK_HEADERS=
```

### LLM Rewrite Variables

Primary LLM:

```env
LLM_API_KEY=sk-xxxxx
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
LLM_HEADERS=
```

Fallback LLM:

```env
LLM_FALLBACK_API_KEY=sk-xxxxx
LLM_FALLBACK_BASE_URL=https://api.openai.com/v1
LLM_FALLBACK_MODEL=gpt-4o-mini
LLM_FALLBACK_HEADERS=
```

### Supported Providers

The app works with any OpenAI-compatible STT endpoint.

Known intended providers:

- Groq: `https://api.groq.com/openai/v1`, models such as `whisper-large-v3` and `whisper-large-v3-turbo`.
- OpenAI: `https://api.openai.com/v1`, models such as `whisper-1` and `gpt-4o-transcribe`.
- OpenRouter: `https://openrouter.ai/api/v1`, depending on available STT models and required headers.
- Local servers: any server exposing a compatible `/audio/transcriptions` endpoint.

Local model examples mentioned in `README.md`:

- `faster-whisper-server`
- `LocalAI`

Local servers may use a dummy key such as `local` because `_make_client()` only requires a non-empty string.

## Dependencies

`requirements.txt` lists:

- `PyQt6>=6.5.0`: Tray application and Qt signals.
- `sounddevice>=0.4.6`: Microphone input stream.
- `numpy>=1.24.0`: Audio frame storage and RMS calculations.
- `pynput>=1.7.6`: Global hotkeys and keyboard typing.
- `openai>=1.0.0`: OpenAI-compatible API client for STT and chat completions.
- `python-dotenv>=1.0.0`: Loads `.env` configuration.

System-level dependencies may also be required depending on platform, especially for audio input, tray integration, and global input capture.

## End-To-End Flow In Detail

This section traces the exact code path for a successful dictation.

### Startup

1. User runs `python main.py`.
2. `OVITrayApp()` is constructed.
3. `QApplication` is created.
4. `AudioRecorder`, `Transcriber`, and `HotkeyListener` are created.
5. `Transcriber` loads `.env` and creates configured API clients.
6. `HotkeyListener` is given callbacks into `OVITrayApp`.
7. `Transcriber` is given a callback to set `HotkeyListener._typing`.
8. Tray icons are loaded.
9. Tray menu is built.
10. Tray icon is shown.
11. `app.run()` starts the keyboard and mouse listeners.
12. Qt event loop starts.

### Recording

1. User presses the configured hotkey.
2. `pynput` calls `HotkeyListener._on_press()` or `_on_click()`.
3. Listener checks enabled state, typing suppression, key/button match, and repeat guard.
4. Listener calls `OVITrayApp._on_hotkey_pressed()`.
5. `OVITrayApp` checks state is `idle`.
6. State signal emits `recording`.
7. Tray icon changes to red and tooltip changes to recording.
8. `AudioRecorder.start_recording()` starts a `sounddevice.InputStream`.
9. The audio callback appends frames until release.

### Stop And Validate Audio

1. User releases the hotkey.
2. `pynput` calls `HotkeyListener._on_release()` or mouse release branch.
3. Listener calls `OVITrayApp._on_hotkey_released()`.
4. `OVITrayApp` checks state is `recording`.
5. `AudioRecorder.stop_recording()` stops and closes the stream.
6. Duration is checked against `MIN_DURATION`.
7. Frames are concatenated.
8. RMS is checked against `SILENCE_THRESHOLD`.
9. If valid, WAV bytes are created in memory.
10. If invalid, `None` is returned and app goes back to idle.

### Processing

1. If valid WAV bytes exist, state signal emits `processing`.
2. Tray icon changes to yellow and tooltip changes to transcribing.
3. A daemon worker thread starts.
4. Worker calls `Transcriber.transcribe(audio_bytes)`.

### STT

1. If primary STT client exists, call primary transcription API.
2. If primary fails or returns no text, try fallback STT client if configured.
3. If no text is returned, worker shows `No speech detected.`.
4. If text is returned, check hallucination phrase set.
5. If hallucination phrase is detected, return `None` and worker shows `No speech detected.`.
6. Otherwise return transcribed text.

### Rewrite

1. Worker calls `Transcriber.rewrite(text)`.
2. If rewrite disabled or primary LLM missing, original text is returned.
3. Otherwise primary LLM is asked to minimally fix the text.
4. If primary LLM fails, fallback LLM is tried if configured.
5. If all rewrite attempts fail, original text is kept.

### Typing

1. Worker calls `Transcriber.type_text(text)`.
2. Transcriber waits 0.05 seconds.
3. Transcriber sets hotkey listener typing suppression to true.
4. Transcriber types the text using `pynput.keyboard.Controller.type()`.
5. Transcriber optionally presses a post-type key.
6. Transcriber clears typing suppression in a `finally` block.
7. Worker emits tray message `Typed: ...`.
8. Worker emits state `idle`.
9. Tray icon returns to grey.

## Threading And Concurrency

There are multiple threads involved:

- Qt main thread: owns UI and event loop.
- `pynput` keyboard listener thread.
- `pynput` mouse listener thread.
- `sounddevice` audio callback thread.
- OVI worker thread for transcription and typing.

Thread safety mechanisms currently used:

- `AudioRecorder._lock` protects `_frames` between audio callback and stop logic.
- `SignalBridge` sends state and status updates from worker threads to Qt safely.
- `HotkeyListener._is_pressed` prevents repeated press callbacks from key repeat.
- `HotkeyListener._typing` suppresses hotkey events during synthetic typing.

Potential threading limitations:

- `HotkeyListener` state fields are not protected by locks. In practice this is probably acceptable for the prototype, but changes can happen from Qt callbacks while listener threads read state.
- `OVITrayApp._state` is maintained in the Qt thread through signals, but callbacks from `pynput` may call `_on_hotkey_pressed` and `_on_hotkey_released` from listener threads. Those methods read `_state` and emit signals. The current flow is simple enough but not rigorously synchronized.
- There is no cancellation of an in-flight transcription if the app is disabled or quitting.

## Error Handling And User Feedback

Current error handling is mostly `print()` based.

Examples:

- Audio callback status prints `[audio] ...`.
- Header parse failures print `[transcriber] Warning: ...`.
- Primary/fallback STT failures print `[transcriber] Primary STT error: ...` or fallback equivalent.
- Primary/fallback LLM failures print `[transcriber] Primary LLM error: ...` or fallback equivalent.
- Filtered hallucinations print `[transcriber] Filtered hallucination: ...`.
- Rewrite before/after prints `[transcriber] Rewrite: ...`.

User-facing feedback is limited to tray messages:

- Startup: `OVI ready. Hold Ctrl and speak.`
- Success: `Typed: <first 60 characters>`
- No text: `No speech detected.`

There are no user-facing error messages for missing API keys, network failures, microphone failures, invalid headers, or permissions problems.

Future production work should replace prints with structured logging and expose actionable errors in the UI.

## Current Strengths

- The core push-to-talk speech loop works.
- Audio format is appropriate for Whisper/Groq STT.
- Silence filtering avoids wasted API calls and common hallucinations.
- The hotkey listener is small, practical, and supports keyboard and mouse triggers.
- Typing suppression reduces self-triggering during output injection.
- STT abstraction supports primary and fallback providers.
- OpenAI-compatible client setup makes Groq, OpenAI, OpenRouter, and local servers feasible.
- Custom headers support provider-specific requirements.
- Optional LLM rewrite is useful for grammar, spelling, punctuation, names, and foreign-word cleanup.
- The tray state machine is easy to understand.
- Qt signal bridge is the right direction for worker-thread UI updates.
- Individual modules have simple standalone test modes.

## Current Limitations

> **All resolved in Screamer** (except streaming, always-listening, and VAD which remain out of scope).

- No persistent app settings. Hotkey, enabled state, post-type key, and rewrite toggle reset on restart.
- `.env` configuration is acceptable for a prototype but rough for desktop users.
- No settings window.
- No microphone selector or device management.
- No structured logging.
- Errors are mostly printed to stdout rather than surfaced to the user.
- `Transcriber` combines too many responsibilities.
- Tray menu is the only settings surface.
- No packaging, installer, autostart integration, or signed builds.
- Tray icons are generated placeholder assets.
- No streaming transcription.
- No continuous or always-listening dictation mode.
- No toggle-to-record mode; only hold-to-talk exists currently.
- No real VAD beyond simple RMS silence filtering.
- No tests.
- No type-checking or linting configuration.
- No retry/backoff policy beyond one fallback attempt.
- No secure credential storage.
- `pynput` can have platform problems on Wayland and macOS permissions.
- The app does not currently persist or remember user choices from the tray menu.

## Recommended Fork Direction

The fork overview recommends keeping the existing push-to-talk MVP and improving product foundations first.

### Keep ✅

- Keep the push-to-talk flow as the MVP. ✅
- Keep `audio.py` mostly as-is initially. ✅ (rewritten with device selection + calibration)
- Keep 16 kHz mono int16 WAV capture. ✅
- Keep silence filtering. ✅ (RMS auto-calibration added)
- Keep the STT client shape and OpenAI-compatible provider support. ✅ (rewritten with httpx)
- Keep primary + fallback STT. ✅
- Keep optional AI rewrite. ✅
- Keep tray state machine and Qt signal bridge. ✅
- ~~Keep typing suppression.~~ Dropped — not needed with RegisterHotKey + SendInput.
- Keep optional post-type key behavior. ✅

### Replace Or Refactor ✅

- Replace `.env`-only config with persistent app settings. ✅ (QSettings + DPAPI)
- Replace inline tray menu as the main settings surface with a real settings window. ✅ (4-tab QDialog)
- Replace `print()` diagnostics with structured logging. ✅ (stdlib logging + rotating file)
- Split `Transcriber` into smaller modules. ✅ (stt.py, rewrite.py, injector.py)
- Replace generated tiny tray icons with proper assets. ✅ (32x32 embedded base64 PNGs)
- Add packaging/build tooling before treating it as a product. ✅ (PyInstaller spec + build script)

### Add First ✅

- Persistent configuration. ✅
- Device selection. ✅
- Structured logging. ✅
- Packaging/build scripts. ✅
- User-facing error notifications. ✅ (AppError → tray balloons)
- Settings window. ✅

### Add Later

- VAD. Deferred — RMS pre-filter + no_speech_prob is sufficient for push-to-talk.
- Better recording UX. Deferred.
- Toggle mode where one shortcut starts recording and a second shortcut stops/transcribes. ✅
- Optional always-listening or streaming behavior. Out of scope.
- More robust platform-specific input handling. Out of scope.
- Autostart. ✅ (startup.py via HKCU Run key)
- Installer. Out of scope.

### Recommended Model Defaults

The current code defaults to `whisper-large-v3`. For a Groq-first fork, recommended defaults are:

- Primary/default STT: `whisper-large-v3-turbo`
- Quality fallback STT: `whisper-large-v3`

> **Note:** Screamer leaves all model fields empty by default. Users configure everything.

## Suggested Future Module Split

> **Implemented.** The `Transcriber` god object has been split. Screamer's module layout:
> `config.py`, `audio.py`, `hotkey.py`, `stt.py`, `rewrite.py`, `injector.py`, `icons.py`, `settings_dialog.py`, `main.py`, `utils.py`, `startup.py`.

The largest architectural issue is that `Transcriber` does too much. A cleaner future layout could be:

- `config.py`: Persistent settings model, default values, migration, environment import.
- `audio.py`: Audio capture and device enumeration.
- `hotkey.py`: Hotkey registration and event mode handling.
- `stt.py`: STT provider client, primary/fallback logic, language handling.
- `rewrite.py`: LLM rewrite client, primary/fallback logic, prompts.
- `injector.py`: Keyboard text injection and post-type key behavior.
- `tray.py`: Tray icon, menu, state display.
- `settings_window.py`: Qt settings UI.
- `logging_setup.py`: Structured app logging.
- `main.py`: Composition root and high-level state machine.

A minimal first refactor could split only `Transcriber` into three classes:

- `SpeechToTextClient`
- `RewriteClient`
- `TextInjector`

Then `OVITrayApp._worker()` would become:

```python
text = self._stt.transcribe(audio_bytes)
if text:
    text = self._rewriter.rewrite(text)
    self._injector.type_text(text)
```

This keeps behavior identical while reducing coupling.

## Settings Persistence Design Notes

> **Implemented.** Screamer uses QSettings (IniFormat) at `%LOCALAPPDATA%/Screamer/settings.ini` for plain settings and Windows DPAPI with entropy for API keys at `%LOCALAPPDATA%/Screamer/keys.enc`. `.env` import backfills empty fields only.

The first major product improvement should be persistent settings.

Current runtime settings that should persist:

- Enabled/disabled state.
- Hotkey choice.
- Post-type key.
- AI rewrite enabled/disabled.
- STT provider fields.
- STT model.
- STT language.
- STT fallback fields.
- LLM rewrite provider fields.
- LLM model.
- LLM fallback fields.
- Microphone device.
- Recording mode: hold-to-talk or toggle-to-record.

Qt provides `QSettings`, which may be a good fit for small desktop settings. For credentials, consider platform keyring integration rather than storing API keys in plain text.

Important migration path:

1. Continue accepting `.env` during transition.
2. Load persistent settings first or second based on chosen precedence.
3. Provide an import path from `.env` into settings UI.
4. Avoid silent overwrites of user-edited settings.

## Toggle Recording Mode Design Notes

> **Implemented.** Screamer supports both hold-to-talk and toggle modes. `HotkeyListener` abstracts the mode; in toggle mode, `WM_HOTKEY` fires on each press to flip state. Configured via `recording_mode` in settings.

The current app is hold-to-talk only. A toggle mode can reuse most of the existing state machine.

Current hold-to-talk semantics:

- Press hotkey: start recording.
- Release hotkey: stop recording and transcribe.

Toggle mode semantics:

- First hotkey activation: start recording.
- Second hotkey activation: stop recording and transcribe.

Implementation considerations:

- `HotkeyListener` currently emits both press and release callbacks. Toggle mode likely needs a single activation callback, probably on press.
- For keyboard events, keep `_is_pressed` to avoid repeat activations while the key is held.
- For mouse events, activate on button press only.
- `OVITrayApp` can branch behavior based on `recording_mode`.
- State transitions still map cleanly to `idle`, `recording`, and `processing`.
- The settings UI should expose `Hold to talk` and `Toggle recording`.

## Device Selection Design Notes

> **Implemented.** `audio.py` enumerates devices via `sounddevice.query_devices()`, stores device ID and name in QSettings, and uses `resolve_device()` for recovery (stored ID → name search → default). The Audio tab in settings provides a device dropdown and Recalibrate button.

`AudioRecorder` currently uses the default input device by omitting the `device` argument to `sd.InputStream`.

For device selection:

- Use `sounddevice.query_devices()` to list devices.
- Filter devices with input channels greater than zero.
- Store a selected device ID or name in persistent settings.
- Pass `device=<selected>` to `sd.InputStream`.
- Handle missing devices gracefully when unplugged.
- Provide a default-device option.

Device IDs may change across reboots or hardware changes, so storing both ID and name can make recovery easier.

## Logging Design Notes

> **Implemented.** Screamer uses stdlib `logging` with a `RotatingFileHandler` at `%LOCALAPPDATA%/Screamer/screamer.log` (2 MB, 3 backups) plus a console handler. API keys are never logged; transcripts only in debug mode.

Replace `print()` calls with Python's `logging` module.

Suggested logger names:

- `src.main`
- `src.audio`
- `src.hotkey`
- `src.stt`
- `src.rewrite`
- `src.injector`
- `src.config`
- `src.startup`

Suggested log destinations:

- Console during development.
- Rotating file logs for packaged app.
- Optional UI log viewer or diagnostics export.

Important events to log:

- App startup and version.
- Settings load path.
- Selected hotkey and recording mode.
- Selected audio device.
- Recording start/stop/duration/RMS.
- STT provider/model used.
- STT fallback activation.
- LLM rewrite activation/fallback.
- Text injection success/failure.
- User-facing errors.

Avoid logging secrets and avoid logging full dictated text by default unless a debug setting explicitly enables it.

## Testing Strategy

> **Partial.** Screamer has test files in `tests/`: `test_audio.py`, `test_config.py`, `test_icons.py`, `test_mappings.py`, `test_startup.py`, `test_stt_rewrite.py`, `test_tray_menu.py`.

There are no automated tests today. Useful future tests:

- Header parsing returns dict for valid JSON.
- Header parsing returns `None` for invalid JSON.
- `_make_client` returns `None` without API key.
- Hallucination filtering removes exact known phrases.
- Hallucination filtering preserves normal text.
- `set_post_key` maps valid keys and clears invalid keys.
- `set_hotkey` maps keyboard and mouse names.
- `AudioRecorder.stop_recording()` returns `None` for too-short recordings.
- WAV generation produces valid headers for synthetic audio.
- Rewrite returns original text when disabled.
- STT fallback is called when primary raises.

Because the app depends on external APIs, audio hardware, global input hooks, and active-window typing, many tests should use mocks. Integration tests can be separate and opt-in.

## Security And Privacy Considerations

This app captures microphone audio and sends it to remote providers. It also injects keyboard input into active windows.

Important considerations for a real product:

- Make provider destination clear to users.
- Avoid sending audio when silence is detected.
- Provide local/offline provider options.
- Do not log audio or full transcript text by default.
- Store API keys securely.
- Make keyboard injection behavior explicit.
- Avoid typing into unexpected windows if focus changes during transcription.
- Consider a confirmation or preview mode for sensitive workflows.

The current implementation types into whichever window is focused after transcription completes. If the user changes focus while OVI is processing, the text will go to the new focused window. This is normal for simple dictation tools but can surprise users.

## License

The project is licensed under GNU AGPLv3.

This matters for forks. Before building a closed-source product or distributing modified versions, review AGPLv3 obligations carefully. The fork overview explicitly calls out this license concern.

## Quick Start For Another Agent

To understand or modify this project quickly:

1. Start with `main.py` to understand the state machine and component wiring.
2. Read `audio.py` to understand WAV capture and silence filtering.
3. Read `hotkey.py` to understand global input events and typing suppression.
4. Read `transcriber.py` to understand provider config, STT fallback, rewrite, hallucination filtering, and typing.
5. Read `.env.example` and `README.md` for supported configuration.
6. Be careful when changing `Transcriber`; it currently owns multiple concerns.
7. Be careful when changing hotkey behavior; keyboard and mouse modes are both supported.
8. Preserve the `SignalBridge` pattern when updating UI from worker threads.
9. Preserve the audio format unless there is a clear STT compatibility reason to change it.
10. Prefer small changes because this is a compact prototype with tight coupling between modules.

## Current Mental Model

OVI is best understood as a four-stage pipeline:

```text
Global hotkey -> Audio capture -> STT/LLM processing -> Keyboard injection
```

`main.py` owns the lifecycle and state. `audio.py` creates clean WAV bytes. `hotkey.py` decides when recording starts and stops. `transcriber.py` turns WAV bytes into final text and types it.

The app is already useful because this pipeline works. The next engineering phase should focus less on changing the core loop and more on making the prototype into a durable desktop application: persistent settings, device selection, better errors, logging, settings UI, packaging, and then richer dictation modes.
