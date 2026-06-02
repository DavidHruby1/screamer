# Screamer — Architecture Plan (v3 — Grilled and Resolved)

---

## 1. Vision

A Windows desktop dictation tool. Hold a hotkey (or toggle), speak, polished text appears wherever your cursor is. Lives in the system tray, clean settings window, ships as a single .exe. Open source (MIT), runs locally (audio + keyboard injection are local, STT is cloud), under 3000 lines of Python. Target: Python 3.12.

**The name:** "Screamer" — you scream at your computer and it listens.

---

## 2. What We Learned From OVI

OVI proved the core pipeline works. But it also proved what happens when you don't split responsibilities early: `transcriber.py` became a god object.

### What to Keep
- The 4-stage pipeline shape
- 16kHz mono int16 WAV audio format
- Push-to-talk with global hotkey
- Primary + fallback STT provider pattern
- Optional LLM rewrite for grammar/spelling cleanup
- Tray state machine (idle → recording → processing → idle)
- Qt signal bridge for thread-safe UI updates
- Optional post-type key
- .env import as migration path

### What to Fix

| Original Plan | Problem | Solution |
|---|---|---|
| No persistent settings → QSettings | Same | Keep QSettings (IniFormat) |
| .env-only → settings window | Same | Keep 4-tab settings dialog |
| No mic selector → device dropdown | Same | Keep, with device recovery: stored ID → name search → default. Balloon only if user-chosen device is lost. |
| print() errors → structured logging | Same | Keep stdlib logging |
| Transcriber bundles STT + LLM + injection | 350-line god object | Split into `stt.py`, `rewrite.py`, `injector.py` |
| Placeholder icons → programmatic generation | Runtime generation adds complexity | Embed base64 PNGs (32x32) at module level |
| No .exe packaging → PyInstaller | Same | Optimize with `--exclude QtQml` + `pyside6-essentials` + UPX (excluding Qt plugins) |
| Secrets in .env → keyring | keyring has 4-5 transitive deps and edge-case bugs | Windows DPAPI via ctypes (~40 lines, zero deps, with entropy) |
| No error feedback → tray balloons | Balloons without structured error types = inconsistent UX | AppError enum; modules emit codes, main.py translates to balloons |
| Only hold-to-talk → add toggle | Same | Keep both modes |
| pynput for hotkeys + injection | pynput has open Windows 11 24H2 bugs (#621, #670) | Win32 `RegisterHotKey` + message-only window for hotkeys; `SendInput` for injection; both via ctypes |
| Typing suppression flag | Needed with pynput (hotkey listening and injection used same library) | DROPPED — `RegisterHotKey` and `SendInput` don't collide |
| Single-key hotkeys (ctrl, alt) | Easy conflict with apps; modifier-only keys unreliable with `RegisterHotKey` | Combo hotkeys: `Ctrl+Alt+Space` (default), `Ctrl+Shift+Space`, `Ctrl+Alt+D/S/V`, `Scroll Lock`, `Pause` |
| Hallucination phrase blacklist | Deletes legitimate speech (e.g. "thank you for watching" said on purpose) | DROPPED — LLM rewrite handles cleanup; raw transcription passes through untouched |

---

## 3. Competitive Landscape

The dictation market is stratified. Screamer's niche is: **simple, open source (MIT), cloud dictation, clean UX, small codebase.** It competes on simplicity and openness, not feature count.

Key insight: The Whisper model is commoditized. Differentiation is what happens AFTER transcription.

---

## 4. Technical Decisions

### Framework: PySide6 (LGPL, MIT-compatible)

**Optimizations applied:**
- Install `pyside6-essentials` only (not `pyside6` meta-package)
- Build from clean venv

**Deferred optimizations** (not yet in spec):
- PyInstaller: `--exclude PySide6.QtQml --exclude PySide6.QtQuick --exclude PySide6.QtWebEngineCore`
- UPX compression with Qt plugin exclusions (`--upx-exclude plugins/* --upx-exclude platforms/* --upx-exclude styles/*`)

**Why:** Thread safety via signals, QFormLayout for clean settings, QSystemTrayIcon with proper balloons, LGPL license. Expected bundle: 30-50MB.

### API Client: httpx

Switched from openai SDK (11.3MB) to httpx (2.8MB installed, 560KB wheel). No pydantic bloat. Native multipart. Verified with Groq.

### Hotkeys: Win32 `RegisterHotKey` via ctypes

| Component | Technology | Lines | Dependency |
|-----------|-----------|-------|------------|
| Global hotkey listening | `RegisterHotKey` + `WM_HOTKEY` message loop via ctypes | ~80 | Zero |
| Hold mode release detection | `GetAsyncKeyState` polling at ~50ms intervals | ~25 | Zero |
| Text injection | `SendInput` with `KEYEVENTF_UNICODE` via ctypes | ~35 | Zero |

**Architecture detail — message-only window:**
`RegisterHotKey` requires an HWND to receive `WM_HOTKEY` messages. The hotkey thread creates a message-only window (invisible — no pixels, no alt-tab, purely a mailbox for Windows notifications) via `CreateWindowEx(NULL, "STATIC", ..., HWND_MESSAGE, ...)`. The thread runs a `GetMessage` loop, blocking efficiently until `WM_HOTKEY` arrives. Callbacks dispatch to `main.py` via `SignalBridge`.

**Default hotkey: `Ctrl+Alt+Space`.** A combo hotkey that rarely conflicts with application shortcuts. The hotkey options are combo bindings (e.g., `Ctrl+Alt+Space`, `Ctrl+Shift+Space`, `Ctrl+Alt+D`) plus single keys like `Scroll Lock` and `Pause`, all defined as `HotkeyBinding(modifiers, vk)` structs in `config.py`. Users change the hotkey in settings.

**Hold mode release detection:**
1. On `WM_HOTKEY`: call press callback, start `GetAsyncKeyState` polling loop at 50ms
2. Poll until the key is no longer held, then call release callback
3. Toggle mode: no polling — `WM_HOTKEY` fires on each press to flip state

**SendInput struct:** Copy verified `INPUT`/`KEYBDINPUT` union definitions from `pynput/_util/win32.py` (MIT-licensed, includes attribution comment). This avoids alignment bugs (28 bytes 32-bit, 40 bytes 64-bit).

**Trade-off:** Windows-only. Cross-platform later would need a new backend behind a `BaseHotkeyListener` abstraction.

### VAD: Silero dropped

Push-to-talk apps don't need VAD. RMS pre-filter + auto-calibration + `no_speech_prob` (0.7 threshold) is sufficient.

### Key Storage: DPAPI via ctypes

Windows DPAPI (`CryptProtectData` / `CryptUnprotectData` from `crypt32.dll`) directly via ctypes.
- ~40 lines of wrapper code, truly zero dependencies
- Encrypts with user's Windows logon credentials + application-specific entropy
- Stores encrypted blob in `%LOCALAPPDATA%/Screamer/keys.enc`
- Not visible in Credential Manager UI (acceptable for this tool)

**Caveat:** If a Windows admin resets the user's password, the DPAPI master key is regenerated and all encrypted data is permanently lost. User must re-enter API keys. Document this.

### Settings: QSettings (IniFormat)

Persist to `%LOCALAPPDATA%/Screamer/settings.ini`. Cross-platform format. Non-secret settings only. Single consistent `%LOCALAPPDATA%` folder for keys, settings, and logs.

### Logging: stdlib logging

Console in dev, rotating file in production. `%LOCALAPPDATA%/Screamer/screamer.log`. Never log secrets or transcripts unless debug flag enabled.

---

## 5. Architecture — Module Breakdown

10 modules, ~1700 core lines. With docstrings, type hints, and error handling: ~2400-2800 lines. Under 3000-line hard limit.

```
screamer/
├── main.py              ~500 lines   Tray + menu + state machine + worker lifecycle + composition
├── settings_dialog.py   ~600 lines   4-tab settings dialog with validation and dynamic reveals
├── config.py            ~530 lines   QSettings + DPAPI key storage + defaults + .env import + logging setup + validation
├── audio.py             ~280 lines   Mic capture + device list + device recovery + RMS + auto-calibration + WAV encoding
├── hotkey.py            ~315 lines   Win32 RegisterHotKey + message-only window + message pump + hold/toggle abstraction
├── stt.py               ~150 lines   httpx STT client + primary/fallback + verbose_json + no_speech_prob filter
├── rewrite.py           ~130 lines   httpx LLM client + primary/fallback + prompt template
├── injector.py          ~155 lines   SendInput typing + post-key
├── icons.py             ~65 lines    Embedded base64 PNG data (32x32) for idle/recording/processing states
├── utils.py             ~55 lines    SignalBridge + AppError enum + shared constants + ScreamerError
└── startup.py           ~90 lines    Windows startup registration via HKCU Run key
```

**Dependency graph (no circular imports):**

```
main.py
  ├── config.py
  ├── audio.py
  ├── hotkey.py
  ├── stt.py
  ├── rewrite.py
  ├── injector.py
  ├── icons.py
  ├── utils.py
  └── startup.py

settings_dialog.py
  ├── config.py
  └── startup.py

audio.py, hotkey.py, injector.py → utils.py (SignalBridge, AppError)

stt.py, rewrite.py → (no imports between them)

startup.py → utils.py (AppError, ScreamerError)
```

### Component Responsibilities

| Module | Single Responsibility |
|--------|----------------------|
| `main.py` | QApplication, tray icon, context menu, state machine, worker thread lifecycle, composition root, error-to-balloon translation |
| `settings_dialog.py` | 4-tab QDialog (General, STT, LLM, Audio), form validation, dynamic field reveal, import .env, reset defaults |
| `config.py` | All settings persistence (QSettings), secure key storage (DPAPI with entropy), default values, .env migration (populates empty fields only), logging configuration, config validation |
| `audio.py` | Enumerate input devices, start/stop mic stream, produce WAV bytes, RMS auto-calibration (measure ambient noise × 2.0, fallback to 5.0), device ID recovery |
| `hotkey.py` | Create message-only window, RegisterHotKey via Win32, run GetMessage pump in thread, abstract hold vs toggle mode, dispatch via SignalBridge |
| `stt.py` | Send WAV to STT endpoint via httpx, primary + fallback providers, request verbose_json, filter by no_speech_prob (keep if ANY segment < 0.7) |
| `rewrite.py` | Send text to LLM endpoint via httpx, primary + fallback providers, inject system prompt with STT language |
| `injector.py` | Type text at cursor via SendInput, press post-type key after 0.05s delay |
| `icons.py` | Provide tray icon QPixmaps from embedded base64 PNG data (grey idle, red recording, yellow processing) |
| `utils.py` | SignalBridge (QObject + Signal), AppError enum, ScreamerError, PipelineResult, shared constants |
| `startup.py` | Windows startup registration via HKCU Run key (enable/disable/check, sync helper) |

### Threading Model

| Thread | Owner | Responsibility |
|--------|-------|---------------|
| Qt main thread | PySide6 | Tray UI, settings dialog, signal dispatch, state machine |
| Hotkey message pump thread | `hotkey.py` | Win32 `GetMessage` loop for `WM_HOTKEY` (message-only window) |
| Hotkey release-watch thread | `hotkey.py` | Polls `GetAsyncKeyState` at 50ms to detect key release in hold mode |
| sounddevice callback thread | `sounddevice` | Audio frame capture (lock-protected list append) |
| Worker QThread | `main.py` | Transcription + rewrite + typing (QThread subclass, serialized, cancellable via Event) |

### Worker Cancellation / Graceful Shutdown

1. On Exit: disable hotkey, set cancellation Event
2. Wait for worker to finish (5-second timeout, discard result on expiry)
3. Stop audio stream if recording
4. Save QSettings
5. Quit Qt

---

## 6. Settings Window (4 Tabs)

### General tab
- Hotkey: QComboBox — Ctrl+Alt+Space (default), Ctrl+Shift+Space, Ctrl+Alt+D, Ctrl+Alt+S, Ctrl+Alt+V, Scroll Lock, Pause
- Recording mode: QRadioButton — Hold to talk / Toggle
- Post-type key: QComboBox — None, Enter, Tab, Space, Backspace
- Start with Windows: QCheckBox (Windows-only; disabled on other platforms)

### STT tab
- API Key: QLineEdit (password echo) + eye toggle
- Base URL: QLineEdit (empty — user must configure)
- Model: QLineEdit (empty — user must configure)
- Language: QLineEdit (empty = auto-detect)
- Custom Headers: QLineEdit (JSON string, optional)
- Enable Fallback: QCheckBox → reveals fallback fields
- Fallback: API Key, Base URL, Model, Custom Headers

### LLM tab
- Enable AI Rewrite: QCheckBox → reveals fields
- API Key: QLineEdit (password)
- Base URL: QLineEdit
- Model: QLineEdit
- Custom Headers: QLineEdit (JSON string, optional)
- System Prompt: QPlainTextEdit (editable, monospace, comes with sensible default prompt, "Reset to default" button available)
- Enable Fallback → reveals fallback fields

### Audio tab
- Input Device: QComboBox from `sounddevice.query_devices()`
- Recalibrate: Button to remeasure ambient noise floor for RMS threshold (auto-calibrated on first launch, fallback to 50)

### Bottom bar
Import from .env, Reset to Defaults, OK, Cancel, Apply

**Defaults:** All STT/LLM fields (API key, base URL, model) start empty. Users must configure everything. App starts to tray with balloon notification directing to Settings. Automatic settings dialog on first launch.

**Behavior notes:**
- .env import populates only empty fields (does not overwrite user-changed settings)
- Custom HTTP headers kept as one QLineEdit per provider (JSON string), placed below Base URL
- All defaults empty — no hardcoded model names that could go stale
- Tray menu quick-toggles sync bidirectionally with Settings dialog values
- Recalibrate button re-measures ambient noise and stores new RMS threshold

---

## 7. Tray Menu

```
Screamer
─────────
✓ Enabled
─────────
Record Mode    ✓ Hold to talk | Toggle
Hotkey         ✓ Ctrl+Alt+Space | Ctrl+Shift+Space | Ctrl+Alt+D | ... | Scroll Lock | Pause
Post-type Key  ✓ None | Enter | Tab | ...
─────────
AI Rewrite     ✓ (checked if enabled)
─────────
Settings...    → opens settings dialog
─────────
Exit           → graceful shutdown
```

Tray menu items sync bidirectionally with Settings dialog. Changing a value in either place updates the other.

Tray icons: grey (idle), red (recording), yellow (processing) — 32x32 base64 PNGs. Tooltip updates. Balloon notifications for errors and results.

---

## 8. Recording Mode Flow

Hold mode:
Press hotkey   → idle → recording
Release        → recording → processing → idle

Toggle mode:
Press hotkey   → idle → recording
Press again    → recording → processing → idle

Same state machine. `hotkey.py` abstracts the mode.

---

## 9. Build Order (each step independently testable)

| Step | File | Test |
|------|------|------|
| 1 | `requirements.txt` | `pip install -r requirements.txt` |
| 2 | `config.py` | `python -m src.config` — prints all settings with defaults, tests DPAPI roundtrip with entropy |
| 3 | `icons.py` | `python -m src.icons` — saves test PNGs (32x32) to verify embedded data |
| 4 | `audio.py` | `python -m src.audio` — records 3s, writes test.wav, tests device recovery and auto-calibration |
| 5 | `hotkey.py` | `python -m src.hotkey` — prints "pressed"/"released" on hotkey, test hold vs toggle (Windows only; graceful message otherwise) |
| 6 | `stt.py` | `python -m src.stt test.wav` — transcribes and prints text, tests no_speech_prob filtering (0.7 threshold) |
| 7 | `rewrite.py` | `python -m src.rewrite "test sentense wit erors"` — prints corrected text |
| 8 | `injector.py` | `python -m src.injector "hello world"` — types into active window with 0.05s post-key delay (Windows only) |
| 9 | `startup.py` | `python -m src.startup` — checks/sets Windows startup registry key |
| 10 | `settings_dialog.py` | `python -m src.settings_dialog` — standalone dialog, test validation |
| 11 | `main.py` | `python -m src.main` — full app, tray, menu, settings, dictation loop |

---

## 10. Line Budget

| File | Lines | What's in it |
|------|-------|-------------|
| `main.py` | ~500 | QApplication, tray icon, menu (100), state machine + worker lifecycle (120), composition wiring (80), error-to-balloon mapping (50), icon state changes (40), bidirectional tray/settings sync (40), CLI entry point (30) |
| `settings_dialog.py` | ~600 | Dialog setup (60), General tab (80), STT tab (120), LLM tab (120), Audio tab (80), validation + dynamic reveals (50), bottom bar (30), standalone test (20) |
| `config.py` | ~530 | QSettings wrapper (60), DPAPI encrypt/decrypt with entropy (80), logging setup (30), defaults + HotkeyBinding + HOTKEY_BINDINGS (120), .env importer (40), validation + parse_custom_headers (80), CLI smoke test (30) |
| `audio.py` | ~280 | Device enumeration (40), stream start/stop/callback (80), WAV encoding (30), RMS auto-calibration (50), device recovery (40), CLI smoke test (20) |
| `hotkey.py` | ~315 | Message-only window creation (40), RegisterHotKey setup (30), message pump thread (80), release-watch thread (40), Win32 ABI declarations (80), CLI smoke test (25) |
| `stt.py` | ~150 | httpx client setup (20), primary STT call (40), fallback STT call (30), verbose_json parsing + no_speech_prob filter (30), CLI smoke test (20) |
| `rewrite.py` | ~130 | httpx client setup (20), primary rewrite (35), fallback rewrite (25), prompt template with language injection (20), CLI smoke test (15) |
| `injector.py` | ~155 | SendInput struct definitions (50), unicode + VK injection (40), post-key handling with 0.05s delay (30), UTF-16 encoding (10), CLI smoke test (15) |
| `icons.py` | ~65 | Base64 PNG strings (35), QPixmap loader with cache (15), CLI smoke test (10) |
| `utils.py` | ~55 | SignalBridge (15), AppError enum (15), ScreamerError (10), PipelineResult (10), constants (5) |
| `startup.py` | ~90 | Registry key management (40), startup_command detection (20), sync_enabled helper (15), CLI smoke test (10) |
| **Total** | **~2860** | |
| **Hard limit** | **3000** | |

---

## 11. What's NOT In Scope

| Feature | Why not now |
|---------|------------|
| Streaming/real-time injection | Adds WebSocket, partial text, revision complexity |
| Local/offline STT (whisper.cpp) | ~2GB model, GPU backend, model management UI |
| Local LLM for rewrite | Model bundling, GPU setup |
| Per-app profiles | Complex settings model |
| Command mode / voice editing | Different product (Wispr Flow territory) |
| Code signing | Packaging-time concern, not code |
| Linux/macOS support | Windows-first; hotkey backend is Win32-ctypes |
| Hallucination blacklist | DROPPED — can accidentally delete legitimate speech. LLM rewrite handles cleanup if enabled. |

---

## 12. Dependency Summary

| Package | Version | License | Purpose | Installed Size |
|---------|---------|---------|---------|---------------|
| `pyside6-essentials` | >=6.5.0 | LGPL | Tray + settings dialog | ~60MB |
| `sounddevice` | >=0.4.6 | MIT | Microphone input stream | ~2MB |
| `numpy` | >=1.24.0 | BSD | Audio frame buffer + RMS | ~30MB |
| `httpx` | >=0.27.0 | BSD | STT + LLM API calls | ~2.8MB |
| `python-dotenv` | >=1.0 | BSD | .env import for OVI migration | ~50KB |

**Total deps: 5 packages.** Removed from OVI: `pynput`, `keyring`, `openai`.

**PyInstaller bundle estimate:** 30-50MB (with exclusions and UPX).

**System deps:** PortAudio (pulled in by `sounddevice` on Windows via wheel).

---

## 13. Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Win32 hotkeys don't work in elevated apps | Medium | `RegisterHotKey` has same UAC limitation as low-level hooks. Document: run Screamer as admin if target app is elevated. |
| DPAPI-encrypted key lost on admin password reset | Medium | Document: user must re-enter API keys after password reset. DPAPI entropy adds app-binding. |
| DPAPI blob not portable across users/machines | Low | By design — key is tied to user account. Re-enter on new machine. |
| Device ID changes on reboot/hardware swap | Low | Recovery chain: stored ID → name search → default. Balloon only if user-chosen device lost. |
| httpx multipart handling differs from openai SDK | Low | Standard multipart/form-data. Tested with Groq. |
| Qt DLLs bloat bundle | Low | 30-50MB with `pyside6-essentials`. |
| Worker thread killed on force-quit | Low | Graceful shutdown with cancellation Event and 5-second timeout. |
| No cross-platform hotkey backend | Low (future) | Windows-only for now. Architecture allows backend abstraction later. |
| Groq removes `no_speech_prob` | Low | Fallback to RMS auto-calibration. Multiple providers support `verbose_json`. |
| Auto-calibration captures noise during speech | Low | Calibration records in a controlled state (user prompted). Fallback to 5.0 if calibration fails or yields absurd value. |
| Startup registry key left behind on uninstall | Low | `startup.py` sync helper checks and cleans up. User can also manually delete from `regedit`. |

---

## 14. Centralized Error Handling

`AppError` enum in `utils.py`:

```python
class AppError(Enum):
    MIC_UNAVAILABLE = "No microphone detected. Check your audio settings."
    MIC_DISCONNECTED = "Microphone disconnected during recording."
    STT_FAILED = "Transcription failed. Check your API key and internet."
    STT_FALLBACK_USED = "Primary STT failed. Used fallback provider."
    LLM_FAILED = "AI rewrite failed. Using raw transcription."
    NETWORK_ERROR = "Network error. Please check your connection."
    NO_SPEECH = "No speech detected. Try speaking louder or closer."
    INJECTION_FAILED = "Could not type text. Focus may have changed."
    HOTKEY_CONFLICT = "Hotkey conflict. Choose a different hotkey."
    UNSUPPORTED_PLATFORM = "This feature is only available on Windows."
    KEY_STORAGE_FAILED = "Could not save or load API keys securely."
    STARTUP_REGISTRATION_FAILED = "Could not update Windows startup setting."
```

Each module raises/returns `AppError` codes. `main.py` translates them into:
- Tray balloon message (user-facing)
- Log entry (developer-facing, with exception details)
- State transition (e.g., `processing → idle` with error tooltip)

This keeps modules decoupled from UI strings.

---

## 15. What Success Looks Like

A user:
1. Downloads Screamer.exe (~35MB optimized)
2. Runs it — tray icon appears
3. Opens Settings → configures STT provider, API key, model, LLM rewrite
4. Holds Scroll Lock, speaks a sentence, releases
5. Polished text appears wherever their cursor was
6. Works in Notepad, Slack, Chrome, VS Code, Terminal
7. Survives reboots (settings persist in `%LOCALAPPDATA%/Screamer/`)
8. Can switch to toggle mode
9. Understands what went wrong (tray balloon, not silent failure)
10. Can edit the LLM system prompt to tune cleanup

---

## 16. Design Notes & Gotchas

### Hotkey — Message-Only Window
`RegisterHotKey` needs an HWND. A message-only window (`CreateWindowEx` with `HWND_MESSAGE` parent) provides one without cluttering the desktop. It has no pixels, no taskbar entry, no focus — purely a mailbox for `WM_HOTKEY`. The thread runs `GetMessage` (efficient kernel wait) and dispatches callbacks via `SignalBridge`.

### Hold Mode Release Detection
`RegisterHotKey` only fires `WM_HOTKEY` on key press. Release detection polls `GetAsyncKeyState` at 50ms intervals until the key is no longer held.

### SendInput Struct Alignment
`INPUT`/`KEYBDINPUT` ctypes struct differs by architecture (28 bytes 32-bit, 40 bytes 64-bit). The implementation defines the full `INPUT` union (`KEYBDINPUT` + `MOUSEINPUT` + `HARDWAREINPUT`) with `_anonymous_` to get correct alignment on both 32-bit and 64-bit Windows.

### UPX Corrupts Qt Plugin DLLs
UPX strips the `.qtmetad` section from Qt plugin DLLs. PyInstaller spec must exclude: `upx_exclude=['plugins/*', 'platforms/*', 'styles/*']`.

### DPAPI Admin Password Reset
If a Windows admin resets the user's password via `net user`, the DPAPI master key is regenerated and all encrypted data is permanently lost. Document: re-enter API keys in settings.

### SendInput and UIPI
`SendInput` is blocked by UIPI when injecting into an elevated process. Same limitation as pynput. Document: run Screamer as admin if target app is elevated.

### RMS Auto-Calibration
On first launch, record 2 seconds of ambient noise, compute RMS, set threshold to noise_floor × 2.0. Store in QSettings. "Recalibrate" button in Audio tab. Fallback to 5.0 if calibration fails or yields absurd value. The RMS pre-filter is a cheap pre-check — `no_speech_prob` (0.7) is the real silence detector.

### `no_speech_prob` Filter
Threshold: 0.7. Keep recording if ANY segment is below 0.7. Only reject if ALL segments exceed 0.7 (entire recording is silence).

### Post-Type Key Timing
Hardcoded 0.05s delay after typing before pressing post-key. User selects which key in settings (None, Enter, Tab, Space, Backspace).

### .env Import
Optional dependency (`python-dotenv`). Only populates empty fields — never overwrites user-configured settings. A no-op if no `.env` file exists.

### No Typing Suppression
Typing suppression (OVI's `set_typing` flag) is dropped. With `RegisterHotKey` (kernel-level, only fires for the registered key combo) and `SendInput` (separate mechanism typing regular characters), the two cannot collide. No self-triggering possible.

---

## Key Differences From Original Plan

| Area | Original Plan | Final |
|------|--------------|-------|
| **Modules** | 5 (~1750 lines) | 11 (~2860 with docs) |
| **Transcriber** | Single 350-line module | Split into `stt.py` + `rewrite.py` + `injector.py` |
| **Hotkeys** | pynput | Win32 `RegisterHotKey` + message-only window via ctypes |
| **Hotkey bindings** | Single keys (ctrl, alt, scroll_lock) | Combo keys: `Ctrl+Alt+Space` (default), `Ctrl+Shift+Space`, `Ctrl+Alt+D/S/V`, `Scroll Lock`, `Pause` |
| **Injection** | pynput | `SendInput` via ctypes (full INPUT/KEYBDINPUT struct definitions) |
| **Key storage** | keyring | DPAPI via ctypes with entropy |
| **All file paths** | `%APPDATA%` (roaming) | `%LOCALAPPDATA%` (consistent, no roaming sync issues) |
| **Error handling** | "tray balloons" (vague) | `AppError` enum + `ScreamerError` exception + centralized translation |
| **Worker shutdown** | Not mentioned | Cancellation Event + 5-second timeout |
| **Device handling** | "Store ID and name" | Recovery chain: ID → name → default. Balloon only if user-chosen. |
| **Bundle size** | "120MB acceptable" | 30-50MB optimized |
| **Dependencies** | 7 packages | 5 packages |
| **Typing suppression** | Hotkey listener flag | Dropped — not needed with RegisterHotKey + SendInput |
| **Hallucination blacklist** | Hardcoded phrase list | Dropped — LLM rewrite handles cleanup |
| **Model defaults** | Hardcoded (whisper-large-v3-turbo, llama-3.1-8b-instant) | All empty — user configures everything |
| **RMS threshold** | Hardcoded 50 | Auto-calibrate (noise × 2.0), fallback to 5.0 |
| **no_speech_prob** | 0.6 threshold | 0.7 threshold, keep if ANY segment below |
| **Post-key delay** | 0.02s | 0.05s (hardcoded) |
| **Icons** | 16x16 runtime-generated | 32x32 embedded base64 PNGs |
| **Custom HTTP headers** | In .env only | Per-provider QLineEdit in settings dialog |
| **Tray/settings sync** | Not addressed | Bidirectional sync (settings writes to disk, main reloads and rebuilds menu) |
| **.env import** | Not specified | Populates only empty fields |
| **Autostart** | V1.1 feature (deferred) | Implemented via `startup.py` (HKCU Run key) |
| **Worker thread** | threading.Thread | QThread subclass with signals |
| **Default hotkey** | scroll_lock | Ctrl+Alt+Space |
| **Settings on first launch** | Not specified | Auto-opens settings dialog when config has validation issues |
