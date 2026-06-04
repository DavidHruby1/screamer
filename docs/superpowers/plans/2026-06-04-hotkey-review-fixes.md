# PR #13 Review Fixes Implementation Plan (hotkey shutdown race + mouse capture grab)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address the two findings in DavidHruby1's review of PR #13: (P1) `HotkeyListener` start/stop shutdown race that can leak global hooks/threads, and (P2) mouse-button hotkey recording that isn't grabbed globally in the settings dialog.

**Architecture:** Add a `threading.Event` readiness handshake between `start()` and the hook thread (queue created + hooks attempted before `start()` returns and before `stop()` posts `WM_QUIT`), make `stop()` post `WM_QUIT` with retry on the now-guaranteed queue and only clear thread state once the thread has actually exited. For the dialog, grab the mouse (not just the keyboard) while recording so global side/middle-button presses are captured anywhere.

**Tech Stack:** Python `threading`, Win32 via `ctypes` (`PeekMessageW`, `PostThreadMessageW`, LL hooks), PySide6 (`grabMouse`/`releaseMouse`), `unittest`.

---

## Context (verified against the rebased branch)

- `src/hotkey.py`: `start()` (77-85) spawns the thread and returns immediately; `_thread_id` (224) is set inside `_message_loop` only after the thread runs. `stop()` (87-98) posts `WM_QUIT` only `if self._thread_id`, ignores the `PostThreadMessageW` result, then sets `_thread = None` unconditionally after a timed join.
- `src/settings_dialog.py`: `HotkeyCaptureEdit.start_recording()` (675-679) calls `grabKeyboard()` only; `stop_recording()` (681-683) calls `releaseKeyboard()` only. `mousePressEvent` (703-712) handles side/middle buttons but only those routed to the widget.
- Branch was rebased onto `origin/main` (`c274310`); suite is green (68 tests). The dev host is Windows, so the Win32 start/stop tests run locally and on the `windows-latest` CI job.

## File Structure

- Modify: `src/hotkey.py` — readiness event, queue pre-creation, robust `stop()`, `PeekMessageW` declaration, `import time`.
- Modify: `src/settings_dialog.py` — `grabMouse()`/`releaseMouse()` in start/stop recording.
- Modify: `tests/test_hotkey_listener.py` — Windows start/stop lifecycle tests.
- Modify: `tests/test_settings_hotkey.py` — mouse-grab + side-button capture tests.

---

## Task 1: P1 — fix `HotkeyListener` start/stop shutdown race

**Files:**
- Modify: `src/hotkey.py`
- Test: `tests/test_hotkey_listener.py`

- [ ] **Step 1: Write failing/lifecycle tests**

Append to `tests/test_hotkey_listener.py` (add `import platform` at the top of the file):

```python
class LifecycleTests(unittest.TestCase):
    def test_stop_without_start_is_safe(self):
        hk = Hotkey(frozenset(), "key", 0x91)
        listener, _p, _r = _listener(hk, HotkeyMode.HOLD)
        # Must not raise or hang regardless of platform.
        listener.stop()
        self.assertIsNone(listener._thread)

    @unittest.skipUnless(platform.system() == "Windows", "LL hooks require Windows")
    def test_start_then_stop_exits_cleanly(self):
        hk = Hotkey(frozenset(), "key", 0x91)  # Scroll Lock
        listener, _p, _r = _listener(hk, HotkeyMode.HOLD)
        listener.start()
        self.assertTrue(listener._ready.is_set(), "start() must wait for readiness")
        listener.stop()
        self.assertIsNone(listener._thread, "thread reference cleared after clean exit")
        self.assertEqual(listener._thread_id, 0)
        self.assertIsNone(listener._kb_hook)
        self.assertIsNone(listener._mouse_hook)

    @unittest.skipUnless(platform.system() == "Windows", "LL hooks require Windows")
    def test_repeated_start_stop(self):
        hk = Hotkey(frozenset(), "key", 0x91)
        listener, _p, _r = _listener(hk, HotkeyMode.HOLD)
        for _ in range(3):
            listener.start()
            listener.stop()
            self.assertIsNone(listener._thread)
```

- [ ] **Step 2: Run tests to see the lifecycle failures**

Run: `.venv/Scripts/python.exe -m unittest tests.test_hotkey_listener -v`
Expected: `test_stop_without_start_is_safe` FAILS or hangs is avoided (currently `stop()` returns early because `_thread` is None — it may already pass); the `_ready` attribute references in the Windows tests FAIL with `AttributeError: '_ready'` (not implemented yet).

- [ ] **Step 3: Add `import time` and a `PM_NOREMOVE` constant**

In `src/hotkey.py`, change the imports block:

```python
import logging
import platform
import threading
from enum import Enum
```
to:
```python
import logging
import platform
import threading
import time
from enum import Enum
```

And add a constant near the other Win32 message constants (after `HC_ACTION = 0`):

```python
PM_NOREMOVE = 0x0000
```

- [ ] **Step 4: Add the readiness event in `__init__`**

In `src/hotkey.py` `__init__`, after `self._stop_event = threading.Event()`:

```python
        self._stop_event = threading.Event()
        self._ready = threading.Event()
```

- [ ] **Step 5: Make `start()` wait for readiness**

Replace `start()`:

```python
    def start(self) -> None:
        if platform.system() != "Windows":
            raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "Low-level hooks require Windows")
        self._stop_event.clear()
        self._ready.clear()
        self._held.clear()
        self._armed = False
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        # Block until the loop thread has created its message queue and attempted
        # to install the hooks, so callers know the listener is live and so a
        # subsequent stop() can reliably post WM_QUIT.
        if not self._ready.wait(timeout=5.0):
            log.warning("Hotkey listener did not signal readiness within 5s")
        log.info("HotkeyListener started: %s mode=%s", self._hotkey.to_canonical(), self._mode.value)
```

- [ ] **Step 6: Make `stop()` robust**

Replace `stop()`:

```python
    def stop(self) -> None:
        if platform.system() != "Windows":
            return
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            self._thread_id = 0
            return

        # The loop thread sets _ready once its message queue exists; wait so the
        # WM_QUIT below is delivered instead of dropped during a startup race.
        self._ready.wait(timeout=5.0)

        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        tid = self._thread_id
        if tid:
            # Retry until the post is accepted or the thread has exited.
            for _ in range(100):
                if not thread.is_alive():
                    break
                if user32.PostThreadMessageW(tid, WM_QUIT, 0, 0):
                    break
                time.sleep(0.02)

        thread.join(timeout=5.0)
        if thread.is_alive():
            log.error("Hotkey thread did not exit; hooks may remain installed")
        else:
            self._thread = None
            self._thread_id = 0
            log.info("HotkeyListener stopped")
```

- [ ] **Step 7: Pre-create the message queue and signal readiness in `_message_loop`**

In `src/hotkey.py` `_message_loop`, replace the block from `self._thread_id = kernel32.GetCurrentThreadId()` through the `log.info("Hooks installed ...")` line and the message-pump `while` loop:

```python
        self._kb_proc = HOOKPROC(kb_callback)
        self._mouse_proc = HOOKPROC(mouse_callback)

        self._thread_id = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)

        # Force-create this thread's message queue up front so a racing stop()
        # can deliver WM_QUIT via PostThreadMessageW even before the pump runs.
        msg = ctypes.wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)

        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
        self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hmod, 0)
        hooks_ok = bool(self._kb_hook and self._mouse_hook)

        # Signal readiness once the queue exists and hooks were attempted, so
        # start() can return and stop() can post WM_QUIT — even on failure.
        self._ready.set()

        if not hooks_ok:
            log.error("SetWindowsHookEx failed: kb=%s mouse=%s", self._kb_hook, self._mouse_hook)
            self._bridge.error_occurred.emit(AppError.HOTKEY_HOOK_FAILED)
            self._uninstall(user32)
            return

        log.info("Hooks installed for %s", self._hotkey.to_canonical())

        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._uninstall(user32)
        log.info("Message loop exited")
```

- [ ] **Step 8: Declare `PeekMessageW`**

In `_declare_win32_functions`, add (next to the other `user32` declarations):

```python
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
```

- [ ] **Step 9: Run tests + lint + format**

Run:
```
.venv/Scripts/python.exe -m unittest tests.test_hotkey_listener -v
.venv/Scripts/python.exe -m ruff check src/ tests/
.venv/Scripts/python.exe -m ruff format --check src/ tests/
```
Expected: all listener tests pass (including the Windows lifecycle tests on this host); ruff clean. If `format --check` flags the edited files, run `.venv/Scripts/python.exe -m ruff format src/ tests/` and re-check.

- [ ] **Step 10: Commit**

```bash
git add src/hotkey.py tests/test_hotkey_listener.py
git commit -m "fix(hotkey): close start/stop shutdown race that could leak global hooks"
```

---

## Task 2: P2 — grab the mouse while recording a hotkey

**Files:**
- Modify: `src/settings_dialog.py`
- Test: `tests/test_settings_hotkey.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_settings_hotkey.py` (add imports `from PySide6.QtCore import QEvent, QPointF`, `from PySide6.QtGui import QMouseEvent`, and `HotkeyCaptureEdit` to the `src.settings_dialog` import). Use a subclass to spy on the grab/release calls (robust against PySide6 method shadowing):

```python
class _SpyCapture(HotkeyCaptureEdit):
    def __init__(self):
        super().__init__()
        self.events = []

    def grabKeyboard(self):
        self.events.append("grab_kb")

    def grabMouse(self):
        self.events.append("grab_mouse")

    def releaseKeyboard(self):
        self.events.append("rel_kb")

    def releaseMouse(self):
        self.events.append("rel_mouse")


class CaptureGrabTests(unittest.TestCase):
    def test_start_recording_grabs_mouse_and_keyboard(self):
        edit = _SpyCapture()
        edit.start_recording()
        self.assertIn("grab_kb", edit.events)
        self.assertIn("grab_mouse", edit.events)

    def test_stop_recording_releases_mouse_and_keyboard(self):
        edit = _SpyCapture()
        edit.start_recording()
        edit.stop_recording()
        self.assertIn("rel_mouse", edit.events)
        self.assertIn("rel_kb", edit.events)

    def test_side_button_press_emits_hotkey(self):
        edit = _SpyCapture()
        edit.start_recording()
        captured = []
        edit.captured.connect(captured.append)
        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(0, 0),
            Qt.BackButton,
            Qt.BackButton,
            Qt.NoModifier,
        )
        edit.mousePressEvent(ev)
        self.assertEqual(captured, [Hotkey(frozenset(), "mouse", MOUSE_X1)])
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_settings_hotkey -v`
Expected: `test_start_recording_grabs_mouse_and_keyboard` FAILS (only "kb" recorded — `grabMouse` not called yet).

- [ ] **Step 3: Grab/release the mouse in the widget**

In `src/settings_dialog.py`, replace `start_recording` and `stop_recording`:

```python
    def start_recording(self) -> None:
        self._recording = True
        self.setText("press keys or a mouse button…")
        self.setFocus(Qt.OtherFocusReason)
        self.grabKeyboard()
        self.grabMouse()

    def stop_recording(self) -> None:
        self._recording = False
        self.releaseMouse()
        self.releaseKeyboard()
```

- [ ] **Step 4: Run tests + lint + format**

Run:
```
.venv/Scripts/python.exe -m unittest tests.test_settings_hotkey -v
.venv/Scripts/python.exe -m ruff check src/ tests/
.venv/Scripts/python.exe -m ruff format --check src/ tests/
```
Expected: all pass; ruff clean (run `ruff format` if needed).

- [ ] **Step 5: Commit**

```bash
git add src/settings_dialog.py tests/test_settings_hotkey.py
git commit -m "fix(hotkey): grab mouse during capture so side buttons record anywhere"
```

---

## Task 3: Full verification + plan doc

- [ ] **Step 1: Whole-suite + import + compile**

Run:
```
.venv/Scripts/python.exe -m compileall src/ tests/
.venv/Scripts/python.exe -c "import src; print('OK')"
.venv/Scripts/python.exe -m unittest discover -s tests
```
Expected: compile OK, `OK`, all tests pass.

- [ ] **Step 2: Commit the plan**

```bash
git add docs/superpowers/plans/2026-06-04-hotkey-review-fixes.md
git commit -m "docs: add PR #13 review-fix plan"
```

---

## Notes / Decisions baked in

- **Readiness handshake closes the race precisely as the reviewer asked:** the queue is created (via `PeekMessageW`) and hooks are attempted *before* `_ready` is set; `start()` waits for it (so the listener is live on return) and `stop()` waits for it (so `WM_QUIT` lands).
- **`stop()` no longer lies about shutdown:** it retries `PostThreadMessageW`, and only clears `_thread`/`_thread_id` if the thread actually exited; otherwise it logs an error and keeps the reference (no false "stopped").
- **Win32 lifecycle tests run on this Windows host and the `windows-latest` CI job**; they are `skipUnless(Windows)` so the ubuntu `compile` job still imports cleanly.
- **Mouse grab:** `grabMouse()` routes global mouse presses to the capture field while recording, so side/middle buttons bind without hovering the field; released on stop. Covered by a stubbed-grab test plus a synthetic side-button capture test.
- Scope stays on the two review findings; no unrelated changes.
