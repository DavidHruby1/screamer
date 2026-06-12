# Custom Hotkeys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user bind any keyboard key/combination *or* a mouse side/middle button as the push-to-talk hotkey, captured via a press-to-record UI, while keeping the existing presets.

**Architecture:** Replace the Win32 `RegisterHotKey` listener with global low-level hooks (`WH_KEYBOARD_LL` + `WH_MOUSE_LL`) so arbitrary keys and mouse buttons work, releases are detected natively (no `GetAsyncKeyState` polling), and the trigger can be swallowed. A new pure `Hotkey` value object in `config.py` (modifiers + a single key/mouse trigger) is the single representation, serialized to one canonical string for QSettings, with backward-compatible parsing of the 7 legacy preset keys. The settings dialog gains a press-to-capture widget (Qt key/mouse events; no global hook needed while the dialog is focused). Validation (pure, testable) blocks dangerous bindings.

**Tech Stack:** Python 3, PySide6 (Qt), Win32 via `ctypes` (`SetWindowsHookEx`/`CallNextHookEx`/`UnhookWindowsHookEx`/`PostThreadMessageW`), `unittest`.

---

## File Structure

- `src/utils.py` — add two `AppError` members. No other change.
- `src/config.py` — add `Hotkey` value object + constants (mouse ids, modifier-VK maps, safe-standalone sets, VK name table) + legacy migration. Remove the obsolete `RegisterHotKey` artifacts (`MOD_*`, `HotkeyBinding`, `HOTKEY_BINDINGS`). Switch `HOTKEY_OPTIONS` values and the `AppConfig.hotkey` default to canonical strings. Update `load_config`/`validate_config`.
- `src/hotkey.py` — rewrite `HotkeyListener` to install LL hooks; extract a pure matching core (`_on_kb_event`/`_on_mouse_event`/`_trigger_down`/`_trigger_up`) that is OS-independent and unit-tested.
- `src/settings_dialog.py` — add `HotkeyCaptureEdit` widget + pure conversion helpers + presets-combo-with-Custom + populate/collect wiring.
- `src/main.py` — parse `config.hotkey` → `Hotkey` when building the listener; keep the rest.
- Tests: `tests/test_hotkey_model.py` (new), `tests/test_hotkey_listener.py` (new), `tests/test_settings_hotkey.py` (new), update `tests/test_mappings.py` and `tests/test_tray_menu.py`.
- Docs: `docs/IMPLEMENTATION.md` (contract update), `CLAUDE.md` (hotkey note).

Run the whole suite with: `python -m unittest discover -s tests -v`
Compile check: `python -m compileall src/`

---

## Task 1: AppError members (`utils.py`)

**Files:**
- Modify: `src/utils.py:28-29`

- [ ] **Step 1: Add two enum members**

In `src/utils.py`, replace the `HOTKEY_CONFLICT` line with the conflict line plus two new members:

```python
    HOTKEY_CONFLICT = "Hotkey conflict. Choose a different hotkey."
    HOTKEY_INVALID = "That key combination can't be used. Add a modifier or pick another key."
    HOTKEY_HOOK_FAILED = "Could not install the global hotkey listener."
```

- [ ] **Step 2: Verify import**

Run: `python -c "from src.utils import AppError; print(AppError.HOTKEY_INVALID.value, AppError.HOTKEY_HOOK_FAILED.value)"`
Expected: prints both messages, no error.

- [ ] **Step 3: Commit**

```bash
git add src/utils.py
git commit -m "feat(hotkey): add invalid/hook-failed error codes"
```

---

## Task 2: `Hotkey` value object + constants (`config.py`)

**Files:**
- Modify: `src/config.py` (constants block ~39-78)
- Test: `tests/test_hotkey_model.py` (create)

The model is pure (no Qt, no Win32) so it is fully unit-testable on any OS.

- [ ] **Step 1: Write failing tests**

Create `tests/test_hotkey_model.py`:

```python
import unittest

from src.config import (
    Hotkey,
    MOUSE_X1,
    MOUSE_X2,
    MOUSE_MIDDLE,
)


class HotkeyModelTests(unittest.TestCase):
    def test_canonical_roundtrip_key_with_mods(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        self.assertEqual(hk.to_canonical(), "ctrl+alt+key:0x20")
        self.assertEqual(Hotkey.parse("ctrl+alt+key:0x20"), hk)

    def test_canonical_orders_mods_consistently(self):
        a = Hotkey(frozenset({"alt", "ctrl"}), "key", 0x44)
        b = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x44)
        self.assertEqual(a.to_canonical(), b.to_canonical())
        self.assertEqual(a.to_canonical(), "ctrl+alt+key:0x44")

    def test_canonical_roundtrip_bare_key(self):
        hk = Hotkey(frozenset(), "key", 0x91)  # Scroll Lock
        self.assertEqual(hk.to_canonical(), "key:0x91")
        self.assertEqual(Hotkey.parse("key:0x91"), hk)

    def test_canonical_roundtrip_mouse(self):
        hk = Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1)
        self.assertEqual(hk.to_canonical(), "ctrl+mouse:x1")
        self.assertEqual(Hotkey.parse("ctrl+mouse:x1"), hk)
        self.assertEqual(Hotkey.parse("mouse:x2"), Hotkey(frozenset(), "mouse", MOUSE_X2))
        self.assertEqual(Hotkey.parse("mouse:middle"), Hotkey(frozenset(), "mouse", MOUSE_MIDDLE))

    def test_parse_legacy_keys_migrate(self):
        self.assertEqual(Hotkey.parse("ctrl_alt_space"), Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20))
        self.assertEqual(Hotkey.parse("scroll_lock"), Hotkey(frozenset(), "key", 0x91))
        self.assertEqual(Hotkey.parse("pause"), Hotkey(frozenset(), "key", 0x13))

    def test_parse_invalid_returns_none(self):
        self.assertIsNone(Hotkey.parse(""))
        self.assertIsNone(Hotkey.parse("garbage"))
        self.assertIsNone(Hotkey.parse("ctrl+mouse:x9"))

    def test_label_human_readable(self):
        self.assertEqual(Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20).to_label(), "Ctrl+Alt+Space")
        self.assertEqual(Hotkey(frozenset(), "key", 0x91).to_label(), "Scroll Lock")
        self.assertEqual(Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1).to_label(), "Ctrl+Mouse Back")
        self.assertEqual(Hotkey(frozenset(), "mouse", MOUSE_MIDDLE).to_label(), "Mouse Middle")

    def test_validate_ok_with_modifier(self):
        self.assertIsNone(Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20).validate())

    def test_validate_ok_safe_standalone(self):
        self.assertIsNone(Hotkey(frozenset(), "key", 0x91).validate())   # Scroll Lock
        self.assertIsNone(Hotkey(frozenset(), "key", 0x70).validate())   # F1
        self.assertIsNone(Hotkey(frozenset(), "mouse", MOUSE_X1).validate())

    def test_validate_rejects_bare_normal_key(self):
        self.assertIsNotNone(Hotkey(frozenset(), "key", 0x20).validate())  # bare Space
        self.assertIsNotNone(Hotkey(frozenset(), "key", 0x41).validate())  # bare A

    def test_validate_rejects_modifier_as_trigger(self):
        self.assertIsNotNone(Hotkey(frozenset({"ctrl"}), "key", 0x11).validate())  # trigger is Ctrl

    def test_validate_rejects_unknown_mouse_button(self):
        self.assertIsNotNone(Hotkey(frozenset(), "mouse", 99).validate())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_hotkey_model -v`
Expected: FAIL — `ImportError: cannot import name 'Hotkey'`.

- [ ] **Step 3: Implement the model**

In `src/config.py`, replace the entire block from `MOD_CONTROL = 0x0002` (line 39) through the `HOTKEY_BINDINGS = { ... }` dict (ending line 78) with the following. (This deletes `MOD_*`, `HotkeyBinding`, and `HOTKEY_BINDINGS`, which the new LL-hook listener no longer needs.)

```python
# Mouse trigger ids (our own discriminators, not Win32 constants).
MOUSE_X1 = 1       # "back" side button (XBUTTON1)
MOUSE_X2 = 2       # "forward" side button (XBUTTON2)
MOUSE_MIDDLE = 3   # middle / wheel button

_MOUSE_TOKEN_TO_CODE = {"x1": MOUSE_X1, "x2": MOUSE_X2, "middle": MOUSE_MIDDLE}
_MOUSE_CODE_TO_TOKEN = {v: k for k, v in _MOUSE_TOKEN_TO_CODE.items()}
_MOUSE_CODE_TO_LABEL = {MOUSE_X1: "Mouse Back", MOUSE_X2: "Mouse Forward", MOUSE_MIDDLE: "Mouse Middle"}

# Canonical modifier order for serialization/labels.
_MOD_ORDER = ("ctrl", "alt", "shift", "win")
_MOD_LABEL = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}

# Win32 virtual-key codes that ARE modifiers (generic + L/R variants).
# A trigger key may never be one of these.
MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})

# Map a modifier VK (as reported by the LL keyboard hook) to its canonical name.
MODIFIER_VK_TO_NAME = {
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt",
    0x5B: "win", 0x5C: "win",
}

# Keys safe to bind alone (won't eat normal typing / clicking).
SAFE_STANDALONE_KEYS = frozenset(
    set(range(0x70, 0x88))            # F1..F24
    | {0x91,                          # Scroll Lock
       0x13,                          # Pause
       0x2D,                          # Insert
       0x2C,                          # PrintScreen
       0x5D,                          # Apps / Menu
       0x90}                          # Num Lock
)

# Human-readable names for common VK codes (labels only).
_VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause",
    0x1B: "Esc", 0x20: "Space", 0x21: "Page Up", 0x22: "Page Down",
    0x23: "End", 0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right",
    0x28: "Down", 0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5D: "Menu", 0x90: "Num Lock", 0x91: "Scroll Lock",
}
_VK_NAMES.update({c: chr(c) for c in range(0x30, 0x3A)})          # 0-9
_VK_NAMES.update({c: chr(c) for c in range(0x41, 0x5B)})          # A-Z
_VK_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})      # F1..F24


def _vk_label(vk: int) -> str:
    return _VK_NAMES.get(vk, f"Key 0x{vk:02X}")


# Legacy preset keys (pre-custom-hotkey format) → canonical Hotkey.
_LEGACY_HOTKEYS = {
    "ctrl_alt_space": ("ctrl+alt", 0x20),
    "ctrl_shift_space": ("ctrl+shift", 0x20),
    "ctrl_alt_d": ("ctrl+alt", 0x44),
    "ctrl_alt_s": ("ctrl+alt", 0x53),
    "ctrl_alt_v": ("ctrl+alt", 0x56),
    "scroll_lock": ("", 0x91),
    "pause": ("", 0x13),
}


@dataclass(frozen=True)
class Hotkey:
    """A push-to-talk binding: a set of modifiers + a single key or mouse trigger.

    ``mods`` is a subset of {"ctrl","alt","shift","win"}. ``kind`` is "key" or
    "mouse". ``code`` is a Win32 virtual-key code (kind="key") or one of the
    ``MOUSE_*`` ids (kind="mouse").
    """

    mods: frozenset
    kind: str
    code: int

    def to_canonical(self) -> str:
        prefix = "".join(f"{m}+" for m in _MOD_ORDER if m in self.mods)
        if self.kind == "mouse":
            token = _MOUSE_CODE_TO_TOKEN.get(self.code, str(self.code))
            return f"{prefix}mouse:{token}"
        return f"{prefix}key:0x{self.code:02X}"

    def to_label(self) -> str:
        prefix = "".join(f"{_MOD_LABEL[m]}+" for m in _MOD_ORDER if m in self.mods)
        if self.kind == "mouse":
            return prefix + _MOUSE_CODE_TO_LABEL.get(self.code, f"Mouse {self.code}")
        return prefix + _vk_label(self.code)

    def validate(self) -> str | None:
        """Return an error message if this binding is unsafe, else None."""
        if self.kind == "mouse":
            if self.code not in _MOUSE_CODE_TO_TOKEN:
                return "Only the side or middle mouse buttons can be used."
            return None
        if self.code in MODIFIER_VKS:
            return "Pick a non-modifier key, then add Ctrl/Alt/Shift as modifiers."
        if self.code in SAFE_STANDALONE_KEYS:
            return None
        if not self.mods:
            return "Add a modifier (Ctrl/Alt/Shift) or choose a function key."
        return None

    @classmethod
    def parse(cls, value: str) -> "Hotkey | None":
        """Parse a canonical string or a legacy preset key. None if invalid."""
        if not value:
            return None
        if value in _LEGACY_HOTKEYS:
            mod_str, code = _LEGACY_HOTKEYS[value]
            mods = frozenset(p for p in mod_str.split("+") if p)
            return cls(mods, "key", code)

        parts = value.split("+")
        trigger = parts[-1]
        mod_parts = parts[:-1]
        if any(m not in _MOD_ORDER for m in mod_parts):
            return None
        mods = frozenset(mod_parts)

        if trigger.startswith("mouse:"):
            token = trigger[len("mouse:"):]
            if token not in _MOUSE_TOKEN_TO_CODE:
                return None
            return cls(mods, "mouse", _MOUSE_TOKEN_TO_CODE[token])
        if trigger.startswith("key:"):
            try:
                code = int(trigger[len("key:"):], 16)
            except ValueError:
                return None
            return cls(mods, "key", code)
        return None
```

Also update the module imports note: `Hotkey` uses `frozenset` from builtins; `dataclass` is already imported (line 9). No new imports needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_hotkey_model -v`
Expected: PASS (all 12 tests).

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_hotkey_model.py
git commit -m "feat(hotkey): add Hotkey value object with parse/label/validate"
```

---

## Task 3: Presets, defaults, load/validate integration (`config.py`)

**Files:**
- Modify: `src/config.py` (`HOTKEY_OPTIONS` ~45-53, `AppConfig.hotkey` line 115, `load_config` ~350-351, `validate_config` ~389-390)
- Test: update `tests/test_mappings.py`

- [ ] **Step 1: Rewrite the mappings tests**

Replace the whole body of `tests/test_mappings.py` with:

```python
import unittest

from src.config import (
    HOTKEY_OPTIONS,
    POST_KEY_OPTIONS,
    AppConfig,
    Hotkey,
)


class MappingTests(unittest.TestCase):
    def test_default_hotkey_is_ctrl_alt_space(self) -> None:
        self.assertEqual(AppConfig().hotkey, "ctrl+alt+key:0x20")
        parsed = Hotkey.parse(AppConfig().hotkey)
        self.assertEqual(parsed, Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20))

    def test_first_preset_is_ctrl_alt_space(self) -> None:
        self.assertEqual(HOTKEY_OPTIONS[0], ("ctrl+alt+key:0x20", "Ctrl+Alt+Space"))

    def test_all_presets_parse_validate_and_relabel(self) -> None:
        for value, label in HOTKEY_OPTIONS:
            hk = Hotkey.parse(value)
            self.assertIsNotNone(hk, f"preset {value!r} must parse")
            self.assertIsNone(hk.validate(), f"preset {value!r} must be valid")
            self.assertEqual(hk.to_label(), label, f"preset {value!r} label mismatch")

    def test_presets_do_not_offer_bare_modifiers(self) -> None:
        for value, _label in HOTKEY_OPTIONS:
            hk = Hotkey.parse(value)
            self.assertNotIn(hk.code, (0x10, 0x11, 0x12), "no bare modifier presets")

    def test_post_key_options_include_none(self) -> None:
        self.assertIn(("none", "None"), POST_KEY_OPTIONS)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_mappings -v`
Expected: FAIL — preset values are still legacy strings like `"ctrl_alt_space"`.

- [ ] **Step 3: Update `HOTKEY_OPTIONS` to canonical strings**

In `src/config.py`, replace the `HOTKEY_OPTIONS` list (lines 45-53) with:

```python
# App-level options shared by settings and tray menus. Values are canonical
# Hotkey strings (see Hotkey.to_canonical); labels come from Hotkey.to_label.
HOTKEY_OPTIONS: list[tuple[str, str]] = [
    ("ctrl+alt+key:0x20", "Ctrl+Alt+Space"),
    ("ctrl+shift+key:0x20", "Ctrl+Shift+Space"),
    ("ctrl+alt+key:0x44", "Ctrl+Alt+D"),
    ("ctrl+alt+key:0x53", "Ctrl+Alt+S"),
    ("ctrl+alt+key:0x56", "Ctrl+Alt+V"),
    ("key:0x91", "Scroll Lock"),
    ("key:0x13", "Pause"),
]
```

- [ ] **Step 4: Update the `AppConfig.hotkey` default**

In `src/config.py` line 115, change:

```python
    hotkey: str = "ctrl_alt_space"
```

to:

```python
    hotkey: str = "ctrl+alt+key:0x20"
```

- [ ] **Step 5: Update `load_config` fallback**

In `src/config.py`, replace lines 350-351:

```python
    if cfg.hotkey not in HOTKEY_BINDINGS:
        cfg.hotkey = "ctrl_alt_space"
```

with (normalizes legacy values to canonical and falls back if unparseable/invalid):

```python
    parsed_hotkey = Hotkey.parse(cfg.hotkey)
    if parsed_hotkey is None or parsed_hotkey.validate() is not None:
        cfg.hotkey = "ctrl+alt+key:0x20"
    else:
        cfg.hotkey = parsed_hotkey.to_canonical()
```

- [ ] **Step 6: Update `validate_config`**

In `src/config.py`, replace lines 389-390:

```python
    if cfg.hotkey not in HOTKEY_BINDINGS:
        issues.append(ConfigValidationIssue("Choose a supported global hotkey.", 0))
```

with:

```python
    parsed_hotkey = Hotkey.parse(cfg.hotkey)
    if parsed_hotkey is None or parsed_hotkey.validate() is not None:
        issues.append(ConfigValidationIssue("Choose a valid global hotkey.", 0))
```

- [ ] **Step 7: Run tests**

Run: `python -m unittest tests.test_mappings tests.test_hotkey_model -v`
Expected: PASS.

- [ ] **Step 8: Verify config smoke + compile**

Run: `python -m compileall src/config.py` then `python -c "from src.config import load_config, AppConfig; c=AppConfig(); print(c.hotkey)"`
Expected: prints `ctrl+alt+key:0x20`, no error.

- [ ] **Step 9: Commit**

```bash
git add src/config.py tests/test_mappings.py
git commit -m "feat(hotkey): store hotkeys as canonical strings with legacy migration"
```

---

## Task 4: LL-hook listener (`hotkey.py`)

**Files:**
- Rewrite: `src/hotkey.py`
- Test: `tests/test_hotkey_listener.py` (create)

The matching core is pure (touches only `self._held`, `self._armed`, `self._mode`, `self._hotkey`, `self._bridge`) so it is tested without Win32. Win32 install lives in `start()`/`_message_loop`.

- [ ] **Step 1: Write failing tests for the matching core**

Create `tests/test_hotkey_listener.py`:

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.config import Hotkey, MOUSE_X1
from src.hotkey import (
    HotkeyListener,
    HotkeyMode,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
)
from src.utils import SignalBridge

_app = QApplication.instance() or QApplication([])


def _listener(hotkey, mode):
    bridge = SignalBridge()
    pressed = []
    released = []
    bridge.hotkey_pressed.connect(lambda: pressed.append(1))
    bridge.hotkey_released.connect(lambda: released.append(1))
    return HotkeyListener(hotkey, mode, bridge), pressed, released


VK_LCTRL = 0xA2
VK_LALT = 0xA4


class HoldKeyTests(unittest.TestCase):
    def test_full_combo_press_and_release(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        listener, pressed, released = _listener(hk, HotkeyMode.HOLD)

        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, VK_LCTRL))  # modifier passes
        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, VK_LALT))
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, 0x20))       # trigger suppressed
        self.assertEqual(pressed, [1])
        self.assertEqual(released, [])

        self.assertTrue(listener._on_kb_event(WM_KEYUP, 0x20))         # release suppressed
        self.assertEqual(released, [1])

    def test_autorepeat_does_not_re_emit(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        listener._on_kb_event(WM_KEYDOWN, VK_LALT)
        listener._on_kb_event(WM_KEYDOWN, 0x20)
        listener._on_kb_event(WM_KEYDOWN, 0x20)  # autorepeat
        listener._on_kb_event(WM_KEYDOWN, 0x20)  # autorepeat
        self.assertEqual(pressed, [1])

    def test_wrong_modifiers_do_not_fire(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)  # only ctrl, alt missing
        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, 0x20))  # not suppressed
        self.assertEqual(pressed, [])

    def test_extra_modifier_blocks_match(self):
        hk = Hotkey(frozenset({"ctrl"}), "key", 0x20)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        listener._on_kb_event(WM_KEYDOWN, VK_LALT)   # extra alt held
        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, 0x20))
        self.assertEqual(pressed, [])


class ToggleKeyTests(unittest.TestCase):
    def test_toggle_emits_pressed_each_time_no_released(self):
        hk = Hotkey(frozenset(), "key", 0x91)  # Scroll Lock, no mods
        listener, pressed, released = _listener(hk, HotkeyMode.TOGGLE)
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, 0x91))
        self.assertTrue(listener._on_kb_event(WM_KEYUP, 0x91))
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, 0x91))
        self.assertEqual(pressed, [1, 1])
        self.assertEqual(released, [])


class MouseTests(unittest.TestCase):
    def test_mouse_x1_with_ctrl(self):
        hk = Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1)
        listener, pressed, released = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        # mouseData high word = XBUTTON1 (0x0001)
        self.assertTrue(listener._on_mouse_event(WM_XBUTTONDOWN, 0x0001 << 16))
        self.assertEqual(pressed, [1])
        self.assertTrue(listener._on_mouse_event(WM_XBUTTONUP, 0x0001 << 16))
        self.assertEqual(released, [1])

    def test_left_button_ignored(self):
        hk = Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        self.assertFalse(listener._on_mouse_event(0x0201, 0))  # WM_LBUTTONDOWN
        self.assertEqual(pressed, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_hotkey_listener -v`
Expected: FAIL — `ImportError` (new symbols / changed constructor not present yet).

- [ ] **Step 3: Rewrite `src/hotkey.py`**

Replace the ENTIRE file `src/hotkey.py` with:

```python
"""Global hotkey listener using Win32 low-level hooks (WH_KEYBOARD_LL + WH_MOUSE_LL).

Supports arbitrary keys, mouse side/middle buttons, hold/toggle modes, and
swallowing the trigger event. The matching core (_on_kb_event / _on_mouse_event)
is pure and OS-independent; only start()/stop() touch Win32.
"""

from __future__ import annotations

import logging
import platform
import threading
from enum import Enum

from src.config import (
    Hotkey,
    MODIFIER_VK_TO_NAME,
    MOUSE_MIDDLE,
    MOUSE_X1,
    MOUSE_X2,
)
from src.utils import AppError, ScreamerError, SignalBridge

log = logging.getLogger(__name__)

# Win32 message constants (also imported by tests).
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

_KEY_DOWN = frozenset({WM_KEYDOWN, WM_SYSKEYDOWN})
_KEY_UP = frozenset({WM_KEYUP, WM_SYSKEYUP})

# XBUTTON discriminators in the high word of MSLLHOOKSTRUCT.mouseData.
_XBUTTON1 = 0x0001
_XBUTTON2 = 0x0002


class HotkeyMode(Enum):
    HOLD = "hold"
    TOGGLE = "toggle"


class HotkeyListener:
    """Low-level-hook hotkey listener with hold/toggle modes and trigger suppression."""

    def __init__(self, hotkey: Hotkey, mode: HotkeyMode, bridge: SignalBridge) -> None:
        self._hotkey = hotkey
        self._mode = mode
        self._bridge = bridge
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._stop_event = threading.Event()
        # Matching state.
        self._held: set[str] = set()
        self._armed = False
        # Keep ctypes callbacks alive across the message loop's lifetime.
        self._kb_proc = None
        self._mouse_proc = None
        self._kb_hook = None
        self._mouse_hook = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if platform.system() != "Windows":
            raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "Low-level hooks require Windows")
        self._stop_event.clear()
        self._held.clear()
        self._armed = False
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        log.info("HotkeyListener started: %s mode=%s", self._hotkey.to_canonical(), self._mode.value)

    def stop(self) -> None:
        if platform.system() != "Windows":
            return
        self._stop_event.set()
        if self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._thread_id = 0
        log.info("HotkeyListener stopped")

    def set_mode(self, mode: HotkeyMode) -> None:
        self._mode = mode
        self._armed = False
        log.info("Hotkey mode changed to %s", mode.value)

    # ------------------------------------------------------------------
    # Pure matching core (OS-independent; unit-tested)
    # ------------------------------------------------------------------

    def _on_kb_event(self, wparam: int, vk: int) -> bool:
        """Handle a keyboard hook event. Return True to suppress (swallow) it."""
        mod = MODIFIER_VK_TO_NAME.get(vk)
        if mod is not None:
            if wparam in _KEY_DOWN:
                self._held.add(mod)
            elif wparam in _KEY_UP:
                self._held.discard(mod)
            return False  # modifiers always pass through

        if self._hotkey.kind != "key" or vk != self._hotkey.code:
            return False

        if wparam in _KEY_DOWN:
            return self._trigger_down()
        if wparam in _KEY_UP:
            return self._trigger_up()
        return False

    def _on_mouse_event(self, wparam: int, mouse_data: int) -> bool:
        """Handle a mouse hook event. Return True to suppress (swallow) it."""
        if wparam == WM_MBUTTONDOWN:
            btn, is_down = MOUSE_MIDDLE, True
        elif wparam == WM_MBUTTONUP:
            btn, is_down = MOUSE_MIDDLE, False
        elif wparam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            high = (mouse_data >> 16) & 0xFFFF
            if high == _XBUTTON1:
                btn = MOUSE_X1
            elif high == _XBUTTON2:
                btn = MOUSE_X2
            else:
                return False
            is_down = wparam == WM_XBUTTONDOWN
        else:
            return False  # left/right/move/wheel — never our trigger

        if self._hotkey.kind != "mouse" or btn != self._hotkey.code:
            return False
        return self._trigger_down() if is_down else self._trigger_up()

    def _trigger_down(self) -> bool:
        if self._armed:
            return True  # autorepeat / duplicate down while held
        if self._held != self._hotkey.mods:
            return False
        self._armed = True
        self._bridge.hotkey_pressed.emit()
        return True

    def _trigger_up(self) -> bool:
        if not self._armed:
            return False
        self._armed = False
        if self._mode == HotkeyMode.HOLD:
            self._bridge.hotkey_released.emit()
        return True

    # ------------------------------------------------------------------
    # Win32 message loop + hook installation
    # ------------------------------------------------------------------

    def _message_loop(self) -> None:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        lresult = getattr(ctypes.wintypes, "LRESULT", ctypes.c_ssize_t)
        ulong_ptr = getattr(ctypes.wintypes, "ULONG_PTR", ctypes.c_size_t)
        HOOKPROC = ctypes.WINFUNCTYPE(
            lresult, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
        )

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.wintypes.DWORD),
                ("scanCode", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.wintypes.LONG), ("y", ctypes.wintypes.LONG)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT),
                ("mouseData", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        _declare_win32_functions(ctypes, user32, kernel32, HOOKPROC, lresult)

        def kb_callback(ncode, wparam, lparam):
            if ncode == HC_ACTION:
                kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self._on_kb_event(wparam, kb.vkCode):
                    return 1
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        def mouse_callback(ncode, wparam, lparam):
            if ncode == HC_ACTION:
                ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if self._on_mouse_event(wparam, ms.mouseData):
                    return 1
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        self._kb_proc = HOOKPROC(kb_callback)
        self._mouse_proc = HOOKPROC(mouse_callback)

        self._thread_id = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)

        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
        self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hmod, 0)
        if not self._kb_hook or not self._mouse_hook:
            log.error("SetWindowsHookEx failed: kb=%s mouse=%s", self._kb_hook, self._mouse_hook)
            self._bridge.error_occurred.emit(AppError.HOTKEY_HOOK_FAILED)
            self._uninstall(user32)
            return

        log.info("Hooks installed for %s", self._hotkey.to_canonical())

        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._uninstall(user32)
        log.info("Message loop exited")

    def _uninstall(self, user32) -> None:
        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None


def _declare_win32_functions(ctypes, user32, kernel32, hookproc, lresult) -> None:
    wintypes = ctypes.wintypes
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetCurrentThreadId.argtypes = []

    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc, ctypes.c_void_p, wintypes.DWORD]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.CallNextHookEx.restype = lresult
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = lresult
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if platform.system() != "Windows":
        print("HotkeyListener requires Windows.")
        print("On non-Windows: start() raises ScreamerError(UNSUPPORTED_PLATFORM).")
        print("Import test passed — no crash at import time.")
        raise SystemExit(0)

    from PySide6.QtWidgets import QApplication
    import sys

    from src.config import Hotkey

    app = QApplication(sys.argv)
    bridge = SignalBridge()
    bridge.hotkey_pressed.connect(lambda: print("PRESSED"))
    bridge.hotkey_released.connect(lambda: print("RELEASED"))

    listener = HotkeyListener(Hotkey(frozenset(), "key", 0x91), HotkeyMode.HOLD, bridge)
    listener.start()
    print("Press Scroll Lock to test (Ctrl+C to quit)...")
    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_hotkey_listener -v`
Expected: PASS (all tests).

- [ ] **Step 5: Compile + import check**

Run: `python -m compileall src/hotkey.py` and `python -c "import src.hotkey; print('OK')"`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add src/hotkey.py tests/test_hotkey_listener.py
git commit -m "feat(hotkey): replace RegisterHotKey with low-level keyboard/mouse hooks"
```

---

## Task 5: Capture UI in settings dialog (`settings_dialog.py`)

**Files:**
- Modify: `src/settings_dialog.py` (imports; `_build_general_tab` ~150-177; `_populate` ~302-303; `_collect` ~345; helpers near `_combo_index` ~549)
- Test: `tests/test_settings_hotkey.py` (create)

- [ ] **Step 1: Write failing tests for pure helpers + populate/collect**

Create `tests/test_settings_hotkey.py`:

```python
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.config import AppConfig, Hotkey, MOUSE_X1
from src.settings_dialog import (
    SettingsDialog,
    _mods_from_qt,
    _mouse_button_to_code,
)

_app = QApplication.instance() or QApplication([])


class QtConversionTests(unittest.TestCase):
    def test_mods_from_qt(self):
        mods = _mods_from_qt(Qt.ControlModifier | Qt.AltModifier)
        self.assertEqual(mods, frozenset({"ctrl", "alt"}))
        self.assertEqual(_mods_from_qt(Qt.NoModifier), frozenset())
        self.assertEqual(_mods_from_qt(Qt.MetaModifier), frozenset({"win"}))

    def test_mouse_button_to_code(self):
        self.assertEqual(_mouse_button_to_code(Qt.BackButton), MOUSE_X1)
        self.assertIsNone(_mouse_button_to_code(Qt.LeftButton))


class PopulateCollectTests(unittest.TestCase):
    def test_roundtrip_preset(self):
        cfg = AppConfig()  # ctrl+alt+key:0x20
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=lambda *a, **k: None)
        try:
            dlg._collect()
            self.assertEqual(dlg.get_config().hotkey, "ctrl+alt+key:0x20")
        finally:
            dlg.deleteLater()

    def test_roundtrip_custom(self):
        cfg = AppConfig()
        cfg.hotkey = "ctrl+mouse:x1"
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=lambda *a, **k: None)
        try:
            self.assertEqual(dlg._captured_hotkey, Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1))
            dlg._collect()
            self.assertEqual(dlg.get_config().hotkey, "ctrl+mouse:x1")
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_settings_hotkey -v`
Expected: FAIL — `ImportError` for `_mods_from_qt` / `_mouse_button_to_code`.

- [ ] **Step 3: Add imports**

In `src/settings_dialog.py`, after the `QtCore` import line (13), add `QtGui` import, and add `Signal` to the QtCore import. Replace line 13:

```python
from PySide6.QtCore import Qt
```

with:

```python
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
```

Add to the `src.config` import block (lines 35-45) the `Hotkey`, `MOUSE_MIDDLE`, `MOUSE_X1`, `MOUSE_X2` names:

```python
from src.config import (
    AppConfig,
    DEFAULT_LLM_SYSTEM_PROMPT,
    HOTKEY_OPTIONS,
    Hotkey,
    MOUSE_MIDDLE,
    MOUSE_X1,
    MOUSE_X2,
    POST_KEY_OPTIONS,
    import_from_env,
    load_config,
    reset_config,
    save_config,
    validate_config,
)
```

- [ ] **Step 4: Add the pure helpers + capture widget**

In `src/settings_dialog.py`, immediately before `def _combo_index(` (line 549), insert:

```python
def _mods_from_qt(modifiers) -> frozenset:
    """Map Qt.KeyboardModifiers to our canonical modifier-name set."""
    mods = set()
    if modifiers & Qt.ControlModifier:
        mods.add("ctrl")
    if modifiers & Qt.AltModifier:
        mods.add("alt")
    if modifiers & Qt.ShiftModifier:
        mods.add("shift")
    if modifiers & Qt.MetaModifier:
        mods.add("win")
    return frozenset(mods)


_QT_MOUSE_TO_CODE = {
    Qt.BackButton: MOUSE_X1,
    Qt.ForwardButton: MOUSE_X2,
    Qt.MiddleButton: MOUSE_MIDDLE,
}

# Qt key codes that are modifiers (ignored as a trigger during capture).
# Stored as ints so membership works regardless of enum/int return type.
_QT_MODIFIER_KEYS = frozenset(
    int(k) for k in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr)
)


def _mouse_button_to_code(button):
    """Map a Qt.MouseButton to a MOUSE_* code, or None if not bindable."""
    return _QT_MOUSE_TO_CODE.get(button)


class HotkeyCaptureEdit(QLineEdit):
    """Read-only field that records the next key/mouse chord while recording.

    Emits ``captured`` with a Hotkey on a complete chord. Keyboard chords finalize
    on the first non-modifier key; mouse chords finalize on a side/middle click.
    """

    captured = Signal(object)  # Hotkey
    cancelled = Signal()       # Esc pressed during recording

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        self._recording = True
        self.setText("press keys or a mouse button…")
        self.setFocus(Qt.OtherFocusReason)
        self.grabKeyboard()

    def stop_recording(self) -> None:
        self._recording = False
        self.releaseKeyboard()

    def show_hotkey(self, hotkey: Hotkey) -> None:
        self.setText(hotkey.to_label())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        event.accept()
        if int(event.key()) == int(Qt.Key_Escape):
            self.cancelled.emit()
            return
        if event.isAutoRepeat() or int(event.key()) in _QT_MODIFIER_KEYS:
            return
        vk = event.nativeVirtualKey()
        if not vk:
            return
        self.captured.emit(Hotkey(_mods_from_qt(event.modifiers()), "key", vk))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._recording:
            super().mousePressEvent(event)
            return
        code = _mouse_button_to_code(event.button())
        if code is None:
            event.accept()  # swallow left/right; only side/middle bind
            return
        event.accept()
        self.captured.emit(Hotkey(_mods_from_qt(event.modifiers()), "mouse", code))
```

- [ ] **Step 5: Build the capture row in `_build_general_tab`**

In `src/settings_dialog.py`, replace the hotkey combo block (lines 154-157):

```python
        self._hotkey_combo = QComboBox()
        for key, label in HOTKEY_OPTIONS:
            self._hotkey_combo.addItem(label, key)
        form.addRow("Hotkey:", self._hotkey_combo)
```

with:

```python
        self._captured_hotkey: Hotkey | None = None

        self._hotkey_combo = QComboBox()
        for key, label in HOTKEY_OPTIONS:
            self._hotkey_combo.addItem(label, key)
        self._hotkey_combo.addItem("Custom…", "__custom__")
        self._hotkey_combo.activated.connect(self._on_hotkey_preset_chosen)
        form.addRow("Hotkey:", self._hotkey_combo)

        self._hotkey_capture = HotkeyCaptureEdit()
        self._hotkey_capture.captured.connect(self._on_hotkey_captured)
        self._hotkey_capture.cancelled.connect(self._stop_hotkey_recording)
        self._hotkey_record_btn = QPushButton("Record")
        self._hotkey_record_btn.setCheckable(True)
        self._hotkey_record_btn.clicked.connect(self._on_hotkey_record_clicked)
        capture_row = QHBoxLayout()
        capture_row.addWidget(self._hotkey_capture, 1)
        capture_row.addWidget(self._hotkey_record_btn)
        form.addRow("", capture_row)

        self._hotkey_error = QLabel("")
        self._hotkey_error.setStyleSheet("color: #c0392b;")
        self._hotkey_error.setVisible(False)
        form.addRow("", self._hotkey_error)
```

- [ ] **Step 6: Add the interaction handlers**

In `src/settings_dialog.py`, immediately after `_build_general_tab` ends (before `# --- STT tab ---` comment near line 179), insert:

```python
    # --- Hotkey capture interaction -----------------------------------

    def _set_captured_hotkey(self, hotkey: Hotkey) -> None:
        """Store a validated hotkey and reflect it in combo + capture field."""
        self._captured_hotkey = hotkey
        self._hotkey_capture.show_hotkey(hotkey)
        self._hotkey_error.setVisible(False)
        canonical = hotkey.to_canonical()
        idx = _combo_index(self._hotkey_combo, canonical)
        self._hotkey_combo.setCurrentIndex(
            idx if idx >= 0 else _combo_index(self._hotkey_combo, "__custom__")
        )

    def _on_hotkey_preset_chosen(self, index: int) -> None:
        data = self._hotkey_combo.itemData(index)
        if data == "__custom__":
            self._start_hotkey_recording()
            return
        hotkey = Hotkey.parse(data)
        if hotkey is not None:
            self._set_captured_hotkey(hotkey)

    def _start_hotkey_recording(self) -> None:
        self._hotkey_record_btn.setChecked(True)
        self._hotkey_record_btn.setText("Cancel")
        self._hotkey_error.setVisible(False)
        self._hotkey_capture.start_recording()

    def _stop_hotkey_recording(self) -> None:
        self._hotkey_record_btn.setChecked(False)
        self._hotkey_record_btn.setText("Record")
        self._hotkey_capture.stop_recording()
        if self._captured_hotkey is not None:
            self._hotkey_capture.show_hotkey(self._captured_hotkey)

    def _on_hotkey_record_clicked(self, checked: bool) -> None:
        if checked:
            self._start_hotkey_recording()
        else:
            self._stop_hotkey_recording()

    def _on_hotkey_captured(self, hotkey: Hotkey) -> None:
        error = hotkey.validate()
        if error is not None:
            self._hotkey_error.setText(error)
            self._hotkey_error.setVisible(True)
            return  # stay in recording so the user can try again
        self._set_captured_hotkey(hotkey)
        self._stop_hotkey_recording()
```

- [ ] **Step 7: Wire populate + collect**

In `src/settings_dialog.py` `_populate`, replace lines 302-303:

```python
        idx = _combo_index(self._hotkey_combo, cfg.hotkey)
        self._hotkey_combo.setCurrentIndex(max(idx, 0))
```

with:

```python
        hotkey = Hotkey.parse(cfg.hotkey) or Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        self._set_captured_hotkey(hotkey)
```

In `_collect`, replace line 345:

```python
        cfg.hotkey = self._hotkey_combo.currentData()
```

with:

```python
        if self._captured_hotkey is not None:
            cfg.hotkey = self._captured_hotkey.to_canonical()
```

- [ ] **Step 8: Run tests**

Run: `python -m unittest tests.test_settings_hotkey -v`
Expected: PASS.

- [ ] **Step 9: Compile + import**

Run: `python -m compileall src/settings_dialog.py` and `python -c "import src.settings_dialog; print('OK')"`
Expected: `OK`.

- [ ] **Step 10: Commit**

```bash
git add src/settings_dialog.py tests/test_settings_hotkey.py
git commit -m "feat(hotkey): add press-to-capture hotkey UI in settings"
```

---

## Task 6: Wire the listener in `main.py`

**Files:**
- Modify: `src/main.py` (config import block 27-35; `_make_listener` ~248-252)
- Test: update `tests/test_tray_menu.py` hotkey values to canonical

- [ ] **Step 1: Update the failing tray test values**

In `tests/test_tray_menu.py` `test_set_hotkey_rebuilds_by_default_but_can_skip` (lines 127 & 133), change the legacy strings to canonical:

```python
            tray_app._set_hotkey("ctrl+alt+key:0x20")
```
and
```python
            tray_app._set_hotkey("ctrl+shift+key:0x20", rebuild_menu=False)
```

- [ ] **Step 2: Run to confirm current behavior still references old listener**

Run: `python -m unittest tests.test_tray_menu -v`
Expected: PASS already (these tests mock `_restart_hotkey`), but values are now canonical. If it fails, it's an unrelated import error — fix in Step 3/4 first.

- [ ] **Step 3: Import `Hotkey` in main**

In `src/main.py`, add `Hotkey` to the `src.config` import block (lines 27-35):

```python
from src.config import (
    HOTKEY_OPTIONS,
    POST_KEY_OPTIONS,
    AppConfig,
    Hotkey,
    import_from_env,
    load_config,
    save_config,
    validate_config,
)
```

- [ ] **Step 4: Parse the hotkey when building the listener**

In `src/main.py`, replace `_make_listener` (lines 248-252):

```python
    def _make_listener(self) -> None:
        """Create and start a HotkeyListener from current config, storing it on self."""
        mode = HotkeyMode.TOGGLE if self._config.recording_mode == "toggle" else HotkeyMode.HOLD
        self._hotkey = HotkeyListener(self._config.hotkey, mode, self._bridge)
        self._hotkey.start()
```

with:

```python
    def _make_listener(self) -> None:
        """Create and start a HotkeyListener from current config, storing it on self."""
        mode = HotkeyMode.TOGGLE if self._config.recording_mode == "toggle" else HotkeyMode.HOLD
        hotkey = Hotkey.parse(self._config.hotkey) or Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        self._hotkey = HotkeyListener(hotkey, mode, self._bridge)
        self._hotkey.start()
```

- [ ] **Step 5: Run tray tests + full suite**

Run: `python -m unittest discover -s tests -v`
Expected: PASS (all tests across all files).

- [ ] **Step 6: Compile + import main**

Run: `python -m compileall src/` and `python -c "import src.main; print('OK')"`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add src/main.py tests/test_tray_menu.py
git commit -m "feat(hotkey): build listener from parsed custom hotkey"
```

---

## Task 7: Docs + final verification

**Files:**
- Modify: `docs/IMPLEMENTATION.md` (hotkey/config/settings contract), `CLAUDE.md` (hotkey note)

- [ ] **Step 1: Update `docs/IMPLEMENTATION.md`**

Find the hotkey section and the `HotkeyListener` / `HOTKEY_BINDINGS` contract. Replace references to `HotkeyListener(key: str, ...)` and `HOTKEY_BINDINGS` with the new contract:
- `HotkeyListener(hotkey: Hotkey, mode: HotkeyMode, bridge: SignalBridge)`
- `config.Hotkey` value object: `mods: frozenset`, `kind: "key"|"mouse"`, `code: int`; methods `to_canonical()`, `to_label()`, `validate() -> str|None`, classmethod `parse(str) -> Hotkey|None`.
- Listener now uses `WH_KEYBOARD_LL` + `WH_MOUSE_LL` (not `RegisterHotKey`); trigger is suppressed; legacy preset strings auto-migrate.

(Edit prose to match; exact wording follows the existing doc's style.)

- [ ] **Step 2: Update `CLAUDE.md` hotkey line**

In `CLAUDE.md`, in the Conventions section, replace the line:

```
- Hotkey strings map to Win32 virtual key codes via `_VK_MAP` in `hotkey.py`; add new hotkey options there and in `HOTKEY_OPTIONS` in `settings_dialog.py`.
```

with:

```
- Hotkeys are `config.Hotkey` value objects (modifiers + one key/mouse trigger), serialized to a canonical string (`ctrl+alt+key:0x20`, `ctrl+mouse:x1`). Presets live in `HOTKEY_OPTIONS` (`config.py`); the listener uses low-level hooks (`WH_KEYBOARD_LL`/`WH_MOUSE_LL`). Add safe-standalone keys via `SAFE_STANDALONE_KEYS` in `config.py`.
```

- [ ] **Step 3: Final full verification**

Run all three:
```bash
python -m compileall src/
python -c "import src; print('OK')"
python -m unittest discover -s tests -v
```
Expected: compile OK, `OK`, and all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/IMPLEMENTATION.md CLAUDE.md
git commit -m "docs(hotkey): document custom hotkey model and low-level hooks"
```

---

## Notes / Decisions baked in

- **Suppression:** the matched trigger down/up is swallowed (returns 1 from the hook); modifiers always pass through.
- **Safety:** bare normal keys (letters/space) need a modifier; only F-keys, lock/pause/insert/printscreen/menu/numlock and mouse side/middle buttons may bind alone; left/right mouse and modifier-only triggers are rejected.
- **Backward compatibility:** the 7 legacy preset strings parse and are re-serialized to canonical on load.
- **Boundaries preserved:** `config.py` stays Qt-free and Win32-free (pure model); Qt→model conversion lives in `settings_dialog.py`; Win32 lives in `hotkey.py`; `hotkey.py` importing `config` matches the existing pattern.
- **Tray submenu:** still shows presets; when the active hotkey is custom, no preset radio is checked (custom is configured via Settings). This is intentional minimalism, not a gap.
