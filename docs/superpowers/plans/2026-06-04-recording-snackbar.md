# Recording Snackbar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small animated on-screen indicator at the bottom-center of the screen that shows a pulsing "Recording" pill while capturing audio and a "Processing" pill while the pipeline runs.

**Architecture:** A new self-contained `src/snackbar.py` provides a frameless, always-on-top, translucent, click-through `RecordingSnackbar` overlay widget plus two pure helper functions (state→content mapping, bottom-center geometry) that are unit-testable without a display. `src/main.py` owns one snackbar instance and drives `show_state` / `hide_state` from the single existing state chokepoint `_apply_state`. No `config.py` / `settings_dialog.py` changes — the feature is always-on for v1, so it does **not** conflict with open PR #13 (custom hotkeys).

**Tech Stack:** Python 3.12+, PySide6 (Qt6) — `QWidget`, `QPropertyAnimation`, `QPainter`, `QGuiApplication.primaryScreen().availableGeometry()`. Tests: `unittest` with `QT_QPA_PLATFORM=offscreen` (matches existing `tests/test_icons.py` / `tests/test_tray_menu.py`).

**Branch:** Create `feat/recording-snackbar` off `main` (independent of the open CI/feature PRs).

---

## Background facts (verified)

- `src/icons.py` defines `TrayState(Enum)` with values `"idle"`, `"recording"`, `"processing"`. `_apply_state(state: TrayState)` in `src/main.py:269` is the **single** place all three states pass through (sets tray icon + tooltip). This is the only wiring seam needed.
- `_apply_state` is called from `__init__` (`main.py:130`), `_start_recording` (`:284`), `_finalize_recording` (`:298`), and the worker callbacks (`:348`, `:356`, `:360`). So driving the snackbar from inside `_apply_state` covers every transition automatically.
- `main()` calls `app.setQuitOnLastWindowClosed(False)` (`main.py:494`), so a hidden/extra top-level overlay window never keeps the app alive or quits it.
- Existing Qt tests set `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` **before** importing PySide6, and obtain the app via `QApplication.instance() or QApplication([])`.
- Context7 (Qt for Python 6) confirms: `WA_TranslucentBackground` requires `FramelessWindowHint` on Windows; `windowOpacity` is animatable via `QPropertyAnimation(widget, b"windowOpacity")`; `QScreen.availableGeometry()` returns the usable area excluding the taskbar; `Qt.Tool` keeps the window out of the taskbar.

## File Structure

- **Create `src/snackbar.py`** — pure helpers `snackbar_content_for()` + `bottom_center_xy()`, and the `RecordingSnackbar(QWidget)` overlay. One responsibility: the recording/processing visual indicator.
- **Create `tests/test_snackbar.py`** — pure-logic tests (no display needed for the helpers) + one offscreen widget smoke test.
- **Modify `src/main.py`** — import the snackbar, build one instance in `__init__`, drive it from `_apply_state`, hide it on quit.
- **Modify `docs/IMPLEMENTATION.md`** — add a `### snackbar.py` API-contract block under the existing per-module sections.
- **Modify `README.md`** — one line under features noting the on-screen recording indicator.

---

### Task 1: Pure state→content mapping

**Files:**
- Create: `src/snackbar.py`
- Test: `tests/test_snackbar.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snackbar.py
import os
import unittest

# Set before any PySide6 import so the helper tests need no display server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SnackbarContentTests(unittest.TestCase):
    def test_recording_maps_to_red_label(self):
        from src.snackbar import snackbar_content_for

        content = snackbar_content_for("recording")
        self.assertIsNotNone(content)
        label, rgb = content
        self.assertEqual(label, "Recording")
        self.assertEqual(rgb, (229, 57, 53))

    def test_processing_maps_to_amber_label(self):
        from src.snackbar import snackbar_content_for

        content = snackbar_content_for("processing")
        self.assertIsNotNone(content)
        label, rgb = content
        self.assertEqual(label, "Processing")
        self.assertEqual(rgb, (255, 179, 0))

    def test_idle_and_unknown_map_to_none(self):
        from src.snackbar import snackbar_content_for

        self.assertIsNone(snackbar_content_for("idle"))
        self.assertIsNone(snackbar_content_for("nonsense"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_snackbar -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.snackbar'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/snackbar.py
"""Frameless on-screen overlay showing live recording / processing status.

The two module-level helpers (``snackbar_content_for`` and ``bottom_center_xy``)
are pure and unit-testable without a running QApplication. ``RecordingSnackbar``
is the Qt overlay widget itself.
"""

from __future__ import annotations

# Keyed by ``TrayState.value`` so this stays decoupled from icons.py.
# Value = (label, dot RGB). Absent key (e.g. "idle") -> hidden.
_CONTENT: dict[str, tuple[str, tuple[int, int, int]]] = {
    "recording": ("Recording", (229, 57, 53)),   # red
    "processing": ("Processing", (255, 179, 0)),  # amber
}


def snackbar_content_for(state_value: str) -> tuple[str, tuple[int, int, int]] | None:
    """Return (label, dot_rgb) for a TrayState value, or None when the snackbar
    should be hidden (idle / unknown)."""
    return _CONTENT.get(state_value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_snackbar -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/snackbar.py tests/test_snackbar.py
git commit -m "feat(snackbar): add pure state->content mapping"
```

---

### Task 2: Pure bottom-center geometry helper

**Files:**
- Modify: `src/snackbar.py`
- Test: `tests/test_snackbar.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_snackbar.py
class SnackbarGeometryTests(unittest.TestCase):
    def test_centers_horizontally_and_sits_above_bottom_margin(self):
        from PySide6.QtCore import QPoint, QRect, QSize
        from src.snackbar import bottom_center_xy

        avail = QRect(0, 0, 1920, 1040)   # 1920x1080 minus a 40px taskbar
        size = QSize(160, 40)
        point = bottom_center_xy(avail, size, margin=48)

        self.assertIsInstance(point, QPoint)
        self.assertEqual(point.x(), (1920 - 160) // 2)      # 880
        self.assertEqual(point.y(), 0 + 1040 - 40 - 48)     # 952

    def test_respects_non_zero_screen_origin(self):
        from PySide6.QtCore import QRect, QSize
        from src.snackbar import bottom_center_xy

        avail = QRect(100, 50, 800, 600)
        size = QSize(200, 50)
        point = bottom_center_xy(avail, size, margin=10)

        self.assertEqual(point.x(), 100 + (800 - 200) // 2)  # 400
        self.assertEqual(point.y(), 50 + 600 - 50 - 10)      # 590
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_snackbar.SnackbarGeometryTests -v`
Expected: FAIL — `ImportError: cannot import name 'bottom_center_xy'`.

- [ ] **Step 3: Write minimal implementation**

Add to the top imports of `src/snackbar.py`:

```python
from PySide6.QtCore import QPoint, QRect, QSize
```

Add below `snackbar_content_for`:

```python
def bottom_center_xy(available: QRect, size: QSize, margin: int = 48) -> QPoint:
    """Top-left point that places a *size* window horizontally centered within
    *available* (a screen's available geometry), *margin* px above its bottom edge."""
    x = available.x() + (available.width() - size.width()) // 2
    y = available.y() + available.height() - size.height() - margin
    return QPoint(x, y)
```

`QPoint`/`QRect`/`QSize` are `QtCore` value types and construct without a running
QApplication, so these tests need no display.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_snackbar.SnackbarGeometryTests -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/snackbar.py tests/test_snackbar.py
git commit -m "feat(snackbar): add bottom-center geometry helper"
```

---

### Task 3: RecordingSnackbar overlay widget

**Files:**
- Modify: `src/snackbar.py`
- Test: `tests/test_snackbar.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_snackbar.py
class SnackbarWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:  # PySide6 not installed in this environment.
            raise unittest.SkipTest(f"PySide6 unavailable: {e}")
        cls._app = QApplication.instance() or QApplication([])

    def test_show_state_makes_widget_visible_then_hide_keeps_object(self):
        from src.snackbar import RecordingSnackbar

        bar = RecordingSnackbar()
        self.assertFalse(bar.isVisible())

        bar.show_state("Recording", (229, 57, 53))
        self.assertTrue(bar.isVisible())
        self.assertEqual(bar.current_label(), "Recording")

        # Switching content while visible updates the label in place.
        bar.show_state("Processing", (255, 179, 0))
        self.assertEqual(bar.current_label(), "Processing")
        self.assertTrue(bar.isVisible())

        # hide_state starts a fade; the object survives and pulse stops.
        bar.hide_state()
        self.assertFalse(bar.is_pulsing())

    def test_is_click_through_and_non_focusable(self):
        from PySide6.QtCore import Qt
        from src.snackbar import RecordingSnackbar

        bar = RecordingSnackbar()
        self.assertTrue(bar.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(bar.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertEqual(bar.focusPolicy(), Qt.FocusPolicy.NoFocus)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_snackbar.SnackbarWidgetTests -v`
Expected: FAIL — `ImportError: cannot import name 'RecordingSnackbar'`.

- [ ] **Step 3: Write minimal implementation**

Replace the import line added in Task 2 with the full import set, and append the
widget class. Final import block at the top of `src/snackbar.py`:

```python
from PySide6.QtCore import Property, QPoint, QPropertyAnimation, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget
```

Append the widget class to `src/snackbar.py`:

```python
class RecordingSnackbar(QWidget):
    """Frameless, always-on-top, translucent, click-through status pill.

    Lives at the bottom-center of the primary screen. Shows a pulsing colored
    dot plus a label. Never takes focus and never appears in the taskbar.
    """

    _MARGIN = 48      # px above the screen's available bottom edge
    _PAD_X = 18       # horizontal inner padding
    _PAD_Y = 11       # vertical inner padding
    _DOT_R = 6        # dot radius
    _GAP = 11         # gap between dot and text
    _RADIUS = 15      # pill corner radius

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keep out of taskbar / alt-tab
        )
        # WA_TranslucentBackground requires FramelessWindowHint on Windows.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._label = "Recording"
        self._dot = QColor(229, 57, 53)
        self._pulse = 1.0  # 0.25..1.0, drives dot alpha

        # Looping pulse on the custom "pulse" property (ping-pong via mid keyframe).
        self._pulse_anim = QPropertyAnimation(self, b"pulse", self)
        self._pulse_anim.setDuration(900)
        self._pulse_anim.setStartValue(1.0)
        self._pulse_anim.setKeyValueAt(0.5, 0.25)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setLoopCount(-1)

        # One-shot fade on windowOpacity for show/hide.
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.finished.connect(self._on_fade_finished)

    # --- custom animatable property -------------------------------------
    def _get_pulse(self) -> float:
        return self._pulse

    def _set_pulse(self, value: float) -> None:
        self._pulse = value
        self.update()

    pulse = Property(float, _get_pulse, _set_pulse)

    # --- public test/inspection helpers ---------------------------------
    def current_label(self) -> str:
        return self._label

    def is_pulsing(self) -> bool:
        from PySide6.QtCore import QAbstractAnimation

        return self._pulse_anim.state() == QAbstractAnimation.State.Running

    # --- show / hide ----------------------------------------------------
    def show_state(self, label: str, dot_rgb: tuple[int, int, int]) -> None:
        """Show (or update) the pill with *label* and dot color *dot_rgb*."""
        self._label = label
        self._dot = QColor(*dot_rgb)
        self._resize_to_content()
        self._reposition()
        # Cancel any in-flight fade first so a pending fade-out (from a recent
        # hide_state) can't hide a freshly-shown pill on a fast hide->show.
        self._fade.stop()
        if self.isVisible():
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(0.0)
            self.show()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        if not self.is_pulsing():
            self._pulse_anim.start()
        self.update()

    def hide_state(self) -> None:
        """Fade out and hide. Safe to call when already hidden."""
        self._pulse_anim.stop()
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        # Only actually hide once we've faded all the way out.
        if self.windowOpacity() <= 0.01:
            self.hide()

    # --- layout ---------------------------------------------------------
    def _resize_to_content(self) -> None:
        fm = QFontMetrics(self.font())
        text_w = fm.horizontalAdvance(self._label) + 4  # slack: avoid right side-bearing clip
        text_h = fm.height()
        width = self._PAD_X + (2 * self._DOT_R) + self._GAP + text_w + self._PAD_X
        height = self._PAD_Y + max(text_h, 2 * self._DOT_R) + self._PAD_Y
        self.setFixedSize(width, height)

    def _reposition(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        self.move(bottom_center_xy(screen.availableGeometry(), self.size(), self._MARGIN))

    # --- painting -------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(
            float(rect.x()), float(rect.y()),
            float(rect.width()), float(rect.height()),
            float(self._RADIUS), float(self._RADIUS),
        )
        painter.fillPath(path, QColor(28, 28, 30, 220))  # dark translucent pill

        # Pulsing dot.
        dot = QColor(self._dot)
        dot.setAlphaF(max(0.0, min(1.0, self._pulse)))
        cx = rect.x() + self._PAD_X + self._DOT_R
        cy = rect.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QPoint(cx, cy), self._DOT_R, self._DOT_R)

        # Label.
        painter.setPen(QColor(245, 245, 247))
        text_x = cx + self._DOT_R + self._GAP
        painter.drawText(
            QRect(text_x, rect.y(), rect.width() - text_x - self._PAD_X, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.end()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_snackbar.SnackbarWidgetTests -v`
Expected: PASS (2 tests). Then run the full snackbar module:
Run: `python -m unittest tests.test_snackbar -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/snackbar.py tests/test_snackbar.py
git commit -m "feat(snackbar): add frameless click-through overlay widget"
```

---

### Task 4: Wire the snackbar into the tray app

**Files:**
- Modify: `src/main.py` (imports near `:38`; `__init__` near `:126`; `_apply_state` at `:269`; quit path near `:472`)
- Test: `tests/test_snackbar_wiring.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snackbar_wiring.py
import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from src.icons import TrayState
from src.main import _TrayApp


def _bare_app():
    """Construct a _TrayApp without running its real __init__/Qt build steps."""
    QApplication.instance() or QApplication([])
    app = _TrayApp.__new__(_TrayApp)
    QObject.__init__(app)
    app._tray = MagicMock()       # _apply_state calls setIcon/setToolTip
    app._snackbar = MagicMock()   # what we assert on
    return app


class SnackbarWiringTests(unittest.TestCase):
    def test_recording_state_shows_snackbar_with_content(self):
        app = _bare_app()
        app._apply_state(TrayState.RECORDING)
        app._snackbar.show_state.assert_called_once_with("Recording", (229, 57, 53))
        app._snackbar.hide_state.assert_not_called()

    def test_processing_state_shows_amber(self):
        app = _bare_app()
        app._apply_state(TrayState.PROCESSING)
        app._snackbar.show_state.assert_called_once_with("Processing", (255, 179, 0))

    def test_idle_state_hides_snackbar(self):
        app = _bare_app()
        app._apply_state(TrayState.IDLE)
        app._snackbar.hide_state.assert_called_once_with()
        app._snackbar.show_state.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_snackbar_wiring -v`
Expected: FAIL — `_apply_state` does not reference `self._snackbar` yet, so `show_state`/`hide_state` are never called (AssertionError).

- [ ] **Step 3: Write minimal implementation**

In `src/main.py`, add the import next to the other `src` imports (after the `from src.rewrite import rewrite` line, keeping alpha-ish grouping near `:40`):

```python
from src.snackbar import RecordingSnackbar, snackbar_content_for
```

In `_TrayApp.__init__`, add the snackbar instance **before** the first `_apply_state(TrayState.IDLE)` call. Insert right after `self._enabled = True` (`main.py:126`):

```python
        self._snackbar = RecordingSnackbar()
```

So the block reads:

```python
        self._recording = False
        self._enabled = True

        self._snackbar = RecordingSnackbar()

        self._build_tray()
        self._build_hotkey()
        self._apply_state(TrayState.IDLE)
```

Extend `_apply_state` (`main.py:269`) to drive the snackbar after the tooltip is set:

```python
    def _apply_state(self, state: TrayState) -> None:
        self._tray.setIcon(QIcon(get_icon_pixmap(state)))
        labels = {
            TrayState.IDLE: "Idle",
            TrayState.RECORDING: "Recording...",
            TrayState.PROCESSING: "Processing...",
        }
        self._tray.setToolTip(f"Screamer — {labels[state]}")

        content = snackbar_content_for(state.value)
        if content is None:
            self._snackbar.hide_state()
        else:
            self._snackbar.show_state(*content)
```

In the quit path, hide the overlay before quitting Qt. After `self._tray.hide()` (`main.py:472`) add:

```python
        self._snackbar.hide_state()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_snackbar_wiring -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_snackbar_wiring.py
git commit -m "feat(snackbar): drive overlay from tray state machine"
```

---

### Task 5: Docs + full regression

**Files:**
- Modify: `docs/IMPLEMENTATION.md` (add `### snackbar.py` after the `### icons.py` block, ~`:247`)
- Modify: `README.md`

- [ ] **Step 1: Add the API-contract block to `docs/IMPLEMENTATION.md`**

Insert after the `### icons.py` block (before `### settings_dialog.py`):

```markdown
### snackbar.py

```python
def snackbar_content_for(state_value: str) -> tuple[str, tuple[int, int, int]] | None: ...
    """Map a TrayState value to (label, dot_rgb); None for idle/unknown (hidden)."""

def bottom_center_xy(available: QRect, size: QSize, margin: int = 48) -> QPoint: ...
    """Top-left point centering *size* horizontally in *available*, *margin* above bottom."""

class RecordingSnackbar(QWidget):
    """Frameless, always-on-top, translucent, click-through status pill at the
    bottom-center of the primary screen. Pulsing dot + label.
    Never takes focus or appears in the taskbar."""
    def show_state(self, label: str, dot_rgb: tuple[int, int, int]) -> None: ...
    def hide_state(self) -> None: ...
```
```

- [ ] **Step 2: Add a feature line to `README.md`**

Add one bullet to the `## What Screamer does` list (that is the actual section header; ~line 29). Match the surrounding bullet style:

```markdown
- On-screen recording indicator: a small pulsing pill appears at the bottom-center of the screen while recording and processing.
```

(Read the file first and match its exact bullet formatting.)

- [ ] **Step 3: Compile + run the entire suite**

Run:
```bash
python -m compileall src/
python -m unittest discover -s tests -v
```
Expected: compile clean; all tests pass (existing suite + the new `test_snackbar` and `test_snackbar_wiring`).

- [ ] **Step 4: Manual smoke (optional but recommended)**

Run: `python -m src.main`
Expected: holding/toggling the hotkey shows a red "Recording" pill bottom-center; on release it switches to an amber "Processing" pill; when the result is injected the pill fades out. The pill never steals focus from the active app and clicks pass through it.

- [ ] **Step 5: Commit**

```bash
git add docs/IMPLEMENTATION.md README.md
git commit -m "docs(snackbar): document recording indicator overlay"
```

---

## Self-Review notes (author)

- **Spec coverage:** FEATURES.md #10 asks for a "small animated snackbar" at the "middle of the bottom of the screen" showing audio is being recorded. Covered: bottom-center placement (Task 2 + `_reposition`), animation (pulsing dot + fade, Task 3), recording indication (Task 4). Processing state is an agreed extension.
- **No config/settings touch:** Confirmed — only `snackbar.py` (new), `main.py`, docs. Zero overlap with PR #13's `config.py`/`settings_dialog.py`.
- **Type consistency:** `show_state(label, dot_rgb)` / `hide_state()` are used identically in Task 3 (definition), Task 4 (wiring + wiring test), and the docs block. `snackbar_content_for` returns `(label, rgb)` consumed via `*content` splat — matches the 2-arg `show_state` signature. `bottom_center_xy(available, size, margin)` signature identical in Tasks 2, 3 (`_reposition`), and docs.
- **Headless safety:** helper tests use only `QtCore` value types (no QApplication); widget tests guard with `setUpClass` skip + offscreen platform, matching `tests/test_icons.py`.
