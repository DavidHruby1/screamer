# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Screamer is a Windows desktop push-to-talk dictation tool. Hold a hotkey, speak, release; audio is recorded at 16 kHz mono, sent to a Whisper-compatible STT endpoint, optionally cleaned up by an LLM rewrite, and typed into the active window via Win32 `SendInput`. It runs as a system-tray app with a settings dialog. Stack: Python 3 + PySide6 (Qt), `sounddevice`, `numpy`, `httpx`. Packaged with PyInstaller.

> Note: `docs/OVERVIEW.md` is a historical pre-fork scouting note. The current config and public API are documented in `docs/IMPLEMENTATION.md`; release operations are in `docs/RELEASES.md`.

## Commands

```powershell
# Dev setup
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run the tray app
python -m src.main

# Build a Windows .exe (creates .venv, installs deps, runs PyInstaller)
.\build_windows.ps1   # output: dist\Screamer\Screamer.exe

# Verification (must pass on any OS)
python -m compileall src/
python -c "import src; print('OK')"
```

### Per-module smoke tests

The regression suite uses `python -m unittest discover -s tests -v`. Each backend module also has a `__main__` smoke test:

```powershell
python -m src.icons              # writes 3 test PNGs (32x32)
python -m src.config             # prints defaults, DPAPI roundtrip, creates APP_DIR
python -m src.audio              # records 3s -> test.wav, prints duration + RMS
python -m src.hotkey             # prints pressed/released (Windows only)
python -m src.injector "hello"   # types into active window (Windows only)
python -m src.stt test.wav       # transcribes (needs API config)
python -m src.rewrite "test sentense"   # corrects text (needs API config)
python -m src.settings_dialog    # launches the 4-tab dialog standalone
```

CLI scripts resolve credentials in this order: `load_config()` (QSettings + DPAPI) → backfill empty fields from a `.env` at cwd via `import_from_env()` → if still empty, print a setup message to stderr and `exit(1)`. No hardcoded provider defaults.

## Architecture

`main.py` is the composition root. Current dependency boundaries:

- **`main.py` imports everything; nothing imports `main.py`.** It owns the tray icon, the `idle → recording → processing → idle` state machine, and the worker thread lifecycle.
- Backend modules do not import one another; `stt.py` and `rewrite.py` share `http_client.py` and receive `AppConfig` from `main.py`. `audio.py` and `hotkey.py` use config constants and value objects.
- Qt is used in the UI modules and the signal bridge in `utils.py`; audio, STT, rewrite, injection and HTTP transport remain Qt-free.
- `settings_dialog.py` edits a *copy* of the config. Apply and OK persist the validated values, while Cancel discards unapplied edits.

### Threading model

- The Qt main thread owns all UI. Recording start/stop runs on the main thread.
- Network steps (`transcribe → rewrite`) run in `_WorkerThread` (a `QThread`). It checks a `threading.Event` (`cancel_event`) before each blocking step, then signals the Qt main thread, which performs final `type_text` injection.
- The hotkey listener runs its own daemon thread with a Win32 `GetMessage` pump. It communicates to the Qt main thread through `SignalBridge` (the `QObject`-with-`Signal` bridge in `utils.py`) — this cross-thread signal pattern is how worker/hotkey threads safely touch the UI.

### Error handling

Backend code raises `ScreamerError(AppError.X, detail=...)` — never bare `print()` or swallowed exceptions. `AppError` (in `utils.py`) is an enum whose `.value` is a user-facing message. `main.py` surfaces these as tray balloon notifications. Non-fatal issues (fallback used, rewrite failed) are carried as `PipelineResult.warnings` rather than raised. When adding a new failure mode, add an `AppError` enum member rather than inventing an ad-hoc message.

### Config & secrets

- Settings, including provider URLs and DPAPI-encrypted API keys and custom headers, persist in one atomically replaced INI serialized with `QSettings` (see `_SECRET_FIELDS` in `config.py`). Existing `keys.enc` files migrate after a successful save.
- All app data lives under `%LOCALAPPDATA%/Screamer/` (`APP_DIR` in `utils.py`). Logs go to a rotating `screamer.log` there.
- **Never log `api_key` values. Never log transcript text unless `setup_logging(debug=True)`.**

### Platform guards

Windows-first, but every module must **import** cleanly on any OS (agents may run on Linux/macOS). Windows-only runtime paths guard Win32 calls at runtime. DPAPI roundtrip, low-level hook installation, and `SendInput` can only be fully verified on Windows.

## Conventions

- Public API contracts are maintained in `docs/IMPLEMENTATION.md`. If you change a backend signature or a persistence guarantee, update that doc alongside code.
- Avoid unnecessary third-party dependencies or new modules; the project is deliberately small.
- Hotkeys are `config.Hotkey` value objects (modifiers + one key/mouse trigger), serialized to a canonical string (`ctrl+alt+key:0x20`, `ctrl+mouse:x1`); legacy preset keys auto-migrate via `Hotkey.parse`. Presets live in `HOTKEY_OPTIONS` (`config.py`); the listener uses low-level hooks (`WH_KEYBOARD_LL`/`WH_MOUSE_LL`) and swallows the matched trigger. Add safe-bind-alone keys via `SAFE_STANDALONE_KEYS` in `config.py`.
