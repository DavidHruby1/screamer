# Plan — Autostart (Launch on Windows startup)

## Goal
Let the user opt into Screamer launching automatically when they log into Windows,
toggled from the Settings dialog. No new dependencies, no new module.

## Approach
Register the running executable under the per-user **Run** registry key:
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`, value name `Screamer`.

- Uses `winreg` (stdlib, zero deps). HKCU (per-user) needs no admin rights.
- Preferred over a `shell:startup` `.lnk` shortcut: no need to build/manage a shortcut
  file, registry write is atomic and easy to query.

The **registry is the source of truth** for whether autostart is on. The config carries
the user's intent for the dialog; on load we reconcile the field from the actual registry
state so the checkbox always reflects reality.

## Where the code lives
`config.py` — it already owns Windows system integration (DPAPI) with the same
platform-guard + lazy-import pattern. Avoids adding an 11th module (CLAUDE.md convention).
`winreg` is Windows-only, so it must be imported **lazily inside the functions**, never at
module top level (modules must import on any OS).

## Changes

### `src/utils.py`
- Add `AppError.AUTOSTART_FAILED = "Could not update Windows startup setting."`
  (per CLAUDE.md: add an enum member, not an ad-hoc message).

### `src/config.py`
- Add field to `AppConfig`: `autostart: bool = False`.
- Constants: `_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"`.
- `_autostart_command() -> str` — the command to register:
  ```python
  import sys
  if getattr(sys, "frozen", False):      # PyInstaller bundle
      return f'"{sys.executable}"'
  return f'"{sys.executable}" -m src.main'  # dev fallback
  ```
- `set_autostart(enabled: bool) -> None`:
  - non-Windows → debug-log no-op, return.
  - enabled → `CreateKey(HKCU, _RUN_KEY)` (opens or creates the Run key),
    `SetValueEx(key, APP_NAME, 0, REG_SZ, cmd)`.
  - disabled → `OpenKey(HKCU, _RUN_KEY, 0, KEY_SET_VALUE)`, `DeleteValue(key, APP_NAME)`;
    ignore `FileNotFoundError` (key or value already absent).
  - wrap `OSError` → raise `ScreamerError(AppError.AUTOSTART_FAILED, str(e))`.
- `is_autostart_enabled() -> bool`:
  - non-Windows → `False`.
  - open `HKCU\_RUN_KEY` with `KEY_READ`, `QueryValueEx(key, APP_NAME)`; return `True` if
    present, `False` on `FileNotFoundError`. (Don't compare the command string — the path
    differs between dev and frozen; presence of our value is enough.)
- `load_config()`: after loading fields, reconcile `cfg.autostart = is_autostart_enabled()`
  (registry authoritative).
- `save_config()`: **unchanged** — stays pure (QSettings + secrets only). It is called on
  every tray toggle and in tests, where autostart never changes; the registry write does
  not belong here.

### `src/settings_dialog.py`
- General tab: add `QCheckBox("Start with Windows")`.
- `_populate`: `self._autostart_check.setChecked(cfg.autostart)`.
- `_collect`: `cfg.autostart = self._autostart_check.isChecked()`.
- Apply the registry side effect where autostart actually changes — a small helper
  `_apply_autostart()` called from both `_on_apply` (Apply button) and `_validate_and_accept`
  (OK), after `_collect`. Wrap in try/except → `QMessageBox.warning` on `ScreamerError`.
  (`settings_dialog` already imports `config` and already performs persistence side effects
  via `save_config` on Apply, so this is consistent.)

### `src/main.py`
- **No changes.** The dialog owns the apply; `load_config()` reconciles the field from the
  registry so the checkbox reflects reality after the dialog reloads config from disk.

### Docs
- `docs/IMPLEMENTATION.md`: add `set_autostart` / `is_autostart_enabled` to the config.py
  contract if that file enumerates the public surface.
- `README.md`: mention "Launch on Windows startup" in Features.

### Tests (`tests/test_config.py`)
Cross-platform (mock/patch — never touch the real registry):
- `AppConfig().autostart is False`.
- `is_autostart_enabled()` returns `False` when `platform.system()` patched to non-Windows.
- `set_autostart(True/False)` is a no-op (no raise) on non-Windows.
- `_autostart_command()` frozen vs dev (patch `sys.frozen`, `sys.executable`): frozen →
  just the quoted exe; dev → quoted exe + ` -m src.main`.

## Verification
- `python -m compileall src/ tests/` and `python -c "import src"` on the dev box.
- Run new unittest tests.
- Manual (Windows, packaged): toggle checkbox on → confirm `HKCU\...\Run\Screamer` value via
  `reg query`; reboot/relogin launches the app; toggle off → value removed.

## Risks / limitations
- **Dev-mode autostart** (`-m src.main`) relies on the working directory containing `src/`;
  Run-key commands launch from the user profile dir, so dev autostart may not resolve the
  module. Acceptable — autostart targets the packaged exe; documented.
- Registry write denied is rare for HKCU; surfaced as a balloon on the dialog apply path.
- No tray-menu toggle (kept in Settings only) to limit scope.

## Commit split
1. `feat(config): add autostart registry helpers and AppConfig field`
   (utils.py AppError + config.py helpers/field/load reconcile + tests)
2. `feat(settings): add "Start with Windows" toggle`
   (settings_dialog.py checkbox + apply on Apply/OK)
3. `docs: document autostart` (README + IMPLEMENTATION.md + this plan)
