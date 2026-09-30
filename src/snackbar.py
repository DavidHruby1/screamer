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
    "recording": ("Recording", (229, 57, 53)),  # red
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

    _MARGIN = 48  # px above the screen's available bottom edge
    _PAD_X = 18  # horizontal inner padding
    _PAD_Y = 11  # vertical inner padding
    _DOT_R = 6  # dot radius
    _GAP = 11  # gap between dot and text
    _RADIUS = 15  # pill corner radius

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
        self._input_label: str | None = None
        self._input_level = 0.0
        self._has_callback_data = False

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
    def set_input_status(self, device_label: str, level: float, has_callback_data: bool) -> None:
        """Display fixed full-scale RMS (0..1), not gain or recognition confidence."""
        if self._label != "Recording":
            return
        self._input_label = device_label
        self._input_level = max(0.0, min(level, 1.0)) if has_callback_data else 0.0
        self._has_callback_data = has_callback_data
        self._resize_to_content()
        self._reposition()
        self.update()

    def input_status_text(self) -> str:
        if self._input_label is None:
            return ""
        observation = "Input level" if self._has_callback_data else "Waiting for samples"
        prefix = f"{observation} - "
        fm = QFontMetrics(self.font())
        device_width = max(0, self.width() - 2 * self._PAD_X - fm.horizontalAdvance(prefix))
        device_label = fm.elidedText(self._input_label, Qt.TextElideMode.ElideRight, device_width)
        return prefix + device_label

    def input_level(self) -> float:
        return self._input_level

    def show_state(self, label: str, dot_rgb: tuple[int, int, int]) -> None:
        """Show (or update) the pill with *label* and dot color *dot_rgb*."""
        self._label = label
        self._dot = QColor(*dot_rgb)
        self._input_label = "Input device unknown" if label == "Recording" else None
        self._input_level = 0.0
        self._has_callback_data = False
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
        self._input_label = None
        self._input_level = 0.0
        self._has_callback_data = False
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
        if self._input_label is not None:
            width = max(
                width,
                min(
                    fm.horizontalAdvance("Waiting for samples - ")
                    + fm.horizontalAdvance(self._input_label)
                    + 4
                    + 2 * self._PAD_X,
                    420,
                ),
            )
            height += text_h + 14
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
            float(rect.x()),
            float(rect.y()),
            float(rect.width()),
            float(rect.height()),
            float(self._RADIUS),
            float(self._RADIUS),
        )
        painter.fillPath(path, QColor(28, 28, 30, 220))  # dark translucent pill

        # Pulsing dot.
        dot = QColor(self._dot)
        dot.setAlphaF(max(0.0, min(1.0, self._pulse)))
        cx = rect.x() + self._PAD_X + self._DOT_R
        fm = QFontMetrics(self.font())
        title_height = max(fm.height(), 2 * self._DOT_R)
        cy = self._PAD_Y + title_height // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QPoint(cx, cy), self._DOT_R, self._DOT_R)

        # Label.
        painter.setPen(QColor(245, 245, 247))
        text_x = cx + self._DOT_R + self._GAP
        painter.drawText(
            QRect(text_x, self._PAD_Y, rect.width() - text_x - self._PAD_X, title_height),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        if self._input_label is not None:
            input_width = rect.width() - 2 * self._PAD_X
            painter.setPen(QColor(190, 190, 195))
            painter.drawText(
                QRect(self._PAD_X, self._PAD_Y + title_height, input_width, fm.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.input_status_text(),
            )
            meter_y = self._PAD_Y + title_height + fm.height() + 4
            painter.fillRect(QRect(self._PAD_X, meter_y, input_width, 5), QColor(75, 75, 80))
            painter.fillRect(
                QRect(self._PAD_X, meter_y, round(input_width * self._input_level), 5),
                QColor(90, 205, 150),
            )
        painter.end()
