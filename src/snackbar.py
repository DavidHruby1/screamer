"""Frameless on-screen overlay showing live recording / processing status.

The two module-level helpers (``snackbar_content_for`` and ``bottom_center_xy``)
are pure and unit-testable without a running QApplication. ``RecordingSnackbar``
is the Qt overlay widget itself.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QPoint, QPropertyAnimation, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

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


def bottom_center_xy(available: QRect, size: QSize, margin: int = 48) -> QPoint:
    """Top-left point that places a *size* window horizontally centered within
    *available* (a screen's available geometry), *margin* px above its bottom edge."""
    x = available.x() + (available.width() - size.width()) // 2
    y = available.y() + available.height() - size.height() - margin
    return QPoint(x, y)


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
