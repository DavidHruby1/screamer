# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Screamer is a Windows desktop push-to-talk dictation tool. Hold a hotkey, speak, release; audio is recorded at 16 kHz mono, sent to a Whisper-compatible STT endpoint, optionally cleaned up by an LLM rewrite, and typed into the active window via Win32 `SendInput`. It runs as a system-tray app with a settings dialog. Stack: Python 3 + PySide6 (Qt), `sounddevice`, `numpy`, `httpx`. Packaged with PyInstaller.

> Note: `docs/OVERVIEW.md` is a stale pre-fork scouting note (it references PyQt6 and a `transcriber.py` that no longer exists). The authoritative design doc is `docs/IMPLEMENTATION.md` + `docs/PLAN.md`, which match the current code.

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

There is **no pytest suite**. Each backend module has a `__main__` block used as its smoke test:

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

The codebase is a strict DAG rooted at `main.py` (the composition root). These dependency rules are load-bearing — preserve them when editing:

- **`main.py` imports everything; nothing imports `main.py`.** It owns the tray icon, the `idle → recording → processing → idle` state machine, and the worker thread lifecycle.
- **The five backend modules (`audio`, `hotkey`, `stt`, `rewrite`, `injector`) must NOT import each other.** They may import only `utils.py`.
- **`stt.py` and `rewrite.py` receive `AppConfig` as a parameter** — they do not import `config.py`. `audio.py` receives device id / name / RMS threshold from `main.py`.
- **Qt (PySide6) lives only in `utils.py`, `icons.py`, `settings_dialog.py`, `main.py`.** The backend modules are Qt-free.
- **`settings_dialog.py` imports only `config.py` and `utils.py`.** It edits a *copy* of the config; the original is untouched until accept.

### Threading model

- The Qt main thread owns all UI. Recording start/stop runs on the main thread.
- The full pipeline (`transcribe → rewrite → type_text`) runs in `_WorkerThread` (a `QThread`) so it never blocks the UI. It checks a `threading.Event` (`cancel_event`) before each blocking step and emits results back via `finished_signal`.
- The hotkey listener runs its own daemon thread with a Win32 `GetMessage` pump. It communicates to the Qt main thread through `SignalBridge` (the `QObject`-with-`Signal` bridge in `utils.py`) — this cross-thread signal pattern is how worker/hotkey threads safely touch the UI.

### Error handling

Backend code raises `ScreamerError(AppError.X, detail=...)` — never bare `print()` or swallowed exceptions. `AppError` (in `utils.py`) is an enum whose `.value` is a user-facing message. `main.py` surfaces these as tray balloon notifications. Non-fatal issues (fallback used, rewrite failed) are carried as `PipelineResult.warnings` rather than raised. When adding a new failure mode, add an `AppError` enum member rather than inventing an ad-hoc message.

### Config & secrets

- Plain settings persist via `QSettings` (IniFormat). API-key fields are encrypted with **Windows DPAPI** before being written (see `_SECRET_FIELDS` in `config.py`).
- All app data lives under `%LOCALAPPDATA%/Screamer/` (`APP_DIR` in `utils.py`). Logs go to a rotating `screamer.log` there.
- **Never log `api_key` values. Never log transcript text unless `setup_logging(debug=True)`.**

### Platform guards

Windows-first, but every module must **import** cleanly on any OS (agents may run on Linux/macOS). Windows-only runtime paths (`hotkey.py`, `injector.py`, DPAPI in `config.py`) guard Win32 calls behind `platform.system() == "Windows"` and raise `ScreamerError(AppError.UNSUPPORTED_PLATFORM)` at *runtime* rather than crashing at import time. DPAPI roundtrip, `RegisterHotKey`, and `SendInput` can only be fully verified on Windows.

## Conventions

- Public API surface of each module is fixed by the contracts in `docs/IMPLEMENTATION.md`. Phase 2 (`main.py`, `settings_dialog.py`) wires Phase 1 modules using only those exports — if you change a backend signature, update that doc.
- No new third-party dependencies and no new modules beyond the 10 in `src/` without a strong reason; the project is deliberately small.
- Hotkeys are `config.Hotkey` value objects (modifiers + one key/mouse trigger), serialized to a canonical string (`ctrl+alt+key:0x20`, `ctrl+mouse:x1`); legacy preset keys auto-migrate via `Hotkey.parse`. Presets live in `HOTKEY_OPTIONS` (`config.py`); the listener uses low-level hooks (`WH_KEYBOARD_LL`/`WH_MOUSE_LL`) and swallows the matched trigger. Add safe-bind-alone keys via `SAFE_STANDALONE_KEYS` in `config.py`.
