"""Frameless on-screen overlay showing live recording / processing status.

The two module-level helpers (``snackbar_content_for`` and ``bottom_center_xy``)
are pure and unit-testable without a running QApplication. ``RecordingSnackbar``
is the Qt overlay widget itself.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import Property, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from src import theme

# Keyed by ``TrayState.value`` so this stays decoupled from icons.py.
# Value = (label, dot RGB). Absent key (e.g. "idle") -> hidden.
_CONTENT: dict[str, tuple[str, tuple[int, int, int]]] = {
    "recording": ("Recording", theme.rgb(theme.EMBER)),
    "processing": ("Processing", theme.rgb(theme.TIDE)),
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
    _PAD_Y = 12  # vertical inner padding
    _DOT_R = 5  # dot radius
    _GAP = 12  # gap between dot and text
    _RADIUS = 16  # pill corner radius
    _WAVE_H = 20  # waveform meter height
    _BAR_W = 3  # waveform bar width
    _BAR_GAP = 2  # gap between waveform bars

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

        self.setFont(theme.ui_font(13, QFont.Weight.Medium))
        self._label = "Recording"
        self._dot = QColor(theme.EMBER)
        # Recent input levels drawn as a waveform; display only.
        self._levels: deque[float] = deque(maxlen=96)
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
        if has_callback_data:
            self._levels.append(self._input_level)
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
        self._levels.clear()
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
            prefix_w = fm.horizontalAdvance("Waiting for samples - ")
            status_padding = 4 + 2 * self._PAD_X
            # Match the separate prefix/device advances used by input_status_text.
            status_w = prefix_w + fm.horizontalAdvance(self._input_label) + status_padding
            max_status_w = prefix_w + fm.horizontalAdvance("M" * 32) + status_padding
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                max_status_w = min(
                    max_status_w, screen.availableGeometry().width() - 2 * self._MARGIN
                )
            width = max(
                width,
                prefix_w + status_padding,
                min(status_w, max_status_w),
            )
            height += text_h + 10 + self._WAVE_H
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

        # Dark glass card with a hairline edge.
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, float(self._RADIUS), float(self._RADIUS))
        painter.fillPath(path, theme.qcolor(theme.SURFACE, 0.95))
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        painter.drawPath(path)

        # Pulsing dot with a soft breathing halo.
        pulse = max(0.0, min(1.0, self._pulse))
        fm = QFontMetrics(self.font())
        title_height = max(fm.height(), 2 * self._DOT_R)
        center = QPointF(self._PAD_X + self._DOT_R, self._PAD_Y + title_height / 2)
        halo = QColor(self._dot)
        halo.setAlphaF(0.22 * pulse)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        halo_r = self._DOT_R + 2.0 + 3.0 * (1.0 - pulse)
        painter.drawEllipse(center, halo_r, halo_r)
        dot = QColor(self._dot)
        dot.setAlphaF(0.35 + 0.65 * pulse)
        painter.setBrush(dot)
        painter.drawEllipse(center, float(self._DOT_R), float(self._DOT_R))

        # Label.
        painter.setPen(QColor(theme.TEXT))
        text_x = self._PAD_X + 2 * self._DOT_R + self._GAP
        painter.drawText(
            QRect(text_x, self._PAD_Y, self.width() - text_x - self._PAD_X, title_height),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        if self._input_label is not None:
            input_width = self.width() - 2 * self._PAD_X
            status_y = self._PAD_Y + title_height + 4
            painter.setPen(QColor(theme.TEXT_2))
            painter.drawText(
                QRect(self._PAD_X, status_y, input_width, fm.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.input_status_text(),
            )
            self._paint_waveform(
                painter,
                QRectF(self._PAD_X, status_y + fm.height() + 6, input_width, self._WAVE_H),
            )
        painter.end()

    def _paint_waveform(self, painter: QPainter, area: QRectF) -> None:
        """Recent fixed full-scale RMS levels as bars, newest on the right."""
        step = self._BAR_W + self._BAR_GAP
        count = max(1, int((area.width() + self._BAR_GAP) // step))
        levels = list(self._levels)[-count:]
        levels = [None] * (count - len(levels)) + levels
        mid = area.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        for i, level in enumerate(levels):
            x = area.left() + i * step
            if level is None:
                color = theme.qcolor(theme.LINE_STRONG)
                height = 2.0
            else:
                age = (i + 1) / count  # fade older bars toward the left
                color = QColor(self._dot)
                color.setAlphaF(0.30 + 0.70 * age)
                height = max(2.0, level * area.height())
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(x, mid - height / 2, self._BAR_W, height),
                self._BAR_W / 2,
                self._BAR_W / 2,
            )
