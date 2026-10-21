"""Frameless on-screen overlay showing live recording / processing status.

The two module-level helpers (``snackbar_content_for`` and ``bottom_center_xy``)
are pure and unit-testable without a running QApplication. ``RecordingSnackbar``
is the Qt overlay widget itself.
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    Property,
    QElapsedTimer,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
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


# Speech RMS lives far below full scale, so a linear meter barely moves. Map
# dBFS onto 0..1 instead: room noise sits near the floor, close speech near 1.
_DB_FLOOR = -54.0
_DB_CEIL = -16.0


def display_level(level: float) -> float:
    """Map a full-scale RMS fraction (0..1) to a perceptual 0..1 wave amplitude."""
    if level <= 0.0:
        return 0.0
    db = 20.0 * math.log10(level)
    return max(0.0, min(1.0, (db - _DB_FLOOR) / (_DB_CEIL - _DB_FLOOR)))


class RecordingSnackbar(QWidget):
    """Frameless, always-on-top, translucent, click-through status pill.

    Lives at the bottom-center of the primary screen. Shows a pulsing colored
    dot plus a label; while recording, also an elapsed timer and a live wave
    driven by the microphone level. Never takes focus or appears in the taskbar.
    """

    _MARGIN = 48  # px above the screen's available bottom edge
    _PAD_X = 18  # horizontal inner padding
    _PAD_Y = 12  # vertical inner padding
    _DOT_R = 5  # dot radius
    _GAP = 12  # gap between dot and text
    _RADIUS = 16  # pill corner radius
    _WAVE_H = 44  # wave area height
    _WAVE_GAP = 8  # gap between title row / wave / status line
    _MIN_REC_W = 300  # recording pill never narrower than this
    _FRAME_MS = 16  # wave animation tick (~60 fps)
    _WAVE_POINTS = 96  # samples per wave curve
    # (cycles across the width, phase speed rad/s, relative amplitude, opacity, pen width)
    _WAVE_LAYERS = (
        (1.15, 2.1, 0.80, 0.30, 1.5),
        (1.75, -3.2, 0.60, 0.45, 1.5),
        (1.45, 4.0, 1.00, 1.00, 2.2),
    )

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
        self._pulse = 1.0  # 0.25..1.0, drives dot alpha
        self._input_label: str | None = None
        self._input_level = 0.0
        self._has_callback_data = False

        # Wave state: smoothed amplitude chases the latest level; phase scrolls.
        self._wave_amp = 0.0
        self._wave_phase = 0.0
        self._elapsed = QElapsedTimer()
        self._last_tick_ms = 0
        self._tick = QTimer(self)
        self._tick.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick.setInterval(self._FRAME_MS)
        self._tick.timeout.connect(self._advance_wave)

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
        if not self._tick.isActive():  # the wave tick repaints anyway
            self.update()

    pulse = Property(float, _get_pulse, _set_pulse)

    # --- public test/inspection helpers ---------------------------------
    def current_label(self) -> str:
        return self._label

    def is_pulsing(self) -> bool:
        from PySide6.QtCore import QAbstractAnimation

        return self._pulse_anim.state() == QAbstractAnimation.State.Running

    def is_animating_wave(self) -> bool:
        return self._tick.isActive()

    # --- show / hide ----------------------------------------------------
    def set_input_status(self, device_label: str, level: float, has_callback_data: bool) -> None:
        """Display fixed full-scale RMS (0..1), not gain or recognition confidence."""
        if self._label != "Recording":
            return
        relayout = device_label != self._input_label
        self._input_label = device_label
        self._input_level = max(0.0, min(level, 1.0)) if has_callback_data else 0.0
        self._has_callback_data = has_callback_data
        if relayout:
            self._resize_to_content()
            self._reposition()
        self.update()

    def input_status_text(self) -> str:
        if self._input_label is None:
            return ""
        observation = "Input level" if self._has_callback_data else "Waiting for samples"
        prefix = f"{observation} - "
        fm = QFontMetrics(self._status_font())
        device_width = max(0, self.width() - 2 * self._PAD_X - fm.horizontalAdvance(prefix))
        device_label = fm.elidedText(self._input_label, Qt.TextElideMode.ElideRight, device_width)
        return prefix + device_label

    def input_level(self) -> float:
        return self._input_level

    def show_state(self, label: str, dot_rgb: tuple[int, int, int]) -> None:
        """Show (or update) the pill with *label* and dot color *dot_rgb*."""
        self._label = label
        self._dot = QColor(*dot_rgb)
        recording = label == "Recording"
        self._input_label = "Input device unknown" if recording else None
        self._input_level = 0.0
        self._has_callback_data = False
        self._wave_amp = 0.0
        if recording:
            self._elapsed.start()
            self._last_tick_ms = 0
            self._tick.start()
        else:
            self._tick.stop()
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
        self._tick.stop()
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

    # --- wave animation -------------------------------------------------
    def _advance_wave(self) -> None:
        now = self._elapsed.elapsed() if self._elapsed.isValid() else 0
        dt = max(0.0, min((now - self._last_tick_ms) / 1000.0, 0.1))
        self._last_tick_ms = now
        target = display_level(self._input_level) if self._has_callback_data else 0.0
        # Fast attack, slower release: syllables pop, pauses settle gently.
        rate = 22.0 if target > self._wave_amp else 5.0
        self._wave_amp += (target - self._wave_amp) * (1.0 - math.exp(-rate * dt))
        # Louder speech also scrolls the wave faster.
        self._wave_phase = (self._wave_phase + dt * (1.0 + 1.6 * self._wave_amp)) % (2 * math.tau)
        self.update()

    # --- layout ---------------------------------------------------------
    def _status_font(self) -> QFont:
        font = QFont(self.font())
        font.setWeight(QFont.Weight.Normal)
        if font.pixelSize() > 0:
            font.setPixelSize(max(9, round(font.pixelSize() * 0.85)))
        else:
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        return font

    def _timer_font(self) -> QFont:
        font = theme.mono_font()
        font.setPixelSize(QFontMetrics(self._status_font()).ascent())
        return font

    def _title_height(self) -> int:
        return max(QFontMetrics(self.font()).height(), 2 * self._DOT_R)

    def _resize_to_content(self) -> None:
        fm = QFontMetrics(self.font())
        text_w = fm.horizontalAdvance(self._label) + 4  # slack: avoid right side-bearing clip
        width = self._PAD_X + (2 * self._DOT_R) + self._GAP + text_w + self._PAD_X
        height = self._PAD_Y + self._title_height() + self._PAD_Y
        if self._input_label is not None:
            sfm = QFontMetrics(self._status_font())
            prefix_w = sfm.horizontalAdvance("Waiting for samples - ")
            status_padding = 4 + 2 * self._PAD_X
            # Match the separate prefix/device advances used by input_status_text.
            status_w = prefix_w + sfm.horizontalAdvance(self._input_label) + status_padding
            max_status_w = prefix_w + sfm.horizontalAdvance("M" * 32) + status_padding
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                max_status_w = min(
                    max_status_w, screen.availableGeometry().width() - 2 * self._MARGIN
                )
            width = max(
                width,
                self._MIN_REC_W,
                prefix_w + status_padding,
                min(status_w, max_status_w),
            )
            height += 2 * self._WAVE_GAP + self._WAVE_H + sfm.height()
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
        title_height = self._title_height()
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
        title_rect = QRect(text_x, self._PAD_Y, self.width() - text_x - self._PAD_X, title_height)
        painter.drawText(
            title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label
        )
        if self._input_label is not None:
            if self._elapsed.isValid():
                seconds = self._elapsed.elapsed() // 1000
                painter.setFont(self._timer_font())
                painter.setPen(QColor(theme.TEXT_2))
                painter.drawText(
                    title_rect,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    f"{seconds // 60}:{seconds % 60:02d}",
                )
            wave_y = self._PAD_Y + title_height + self._WAVE_GAP
            inner_w = self.width() - 2 * self._PAD_X
            self._paint_wave(painter, QRectF(self._PAD_X, wave_y, inner_w, self._WAVE_H))
            status_font = self._status_font()
            painter.setFont(status_font)
            painter.setPen(QColor(theme.TEXT_3))
            painter.drawText(
                QRect(
                    self._PAD_X,
                    wave_y + self._WAVE_H + self._WAVE_GAP,
                    inner_w,
                    QFontMetrics(status_font).height(),
                ),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.input_status_text(),
            )
        painter.end()

    def _paint_wave(self, painter: QPainter, area: QRectF) -> None:
        """Layered sine waves under a bell envelope, scaled by the smoothed level."""
        mid = area.center().y()
        half_h = area.height() / 2 - 2.0
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if not self._has_callback_data:
            # No samples yet: a dim resting line rather than a fake wave.
            pen = QPen(theme.qcolor(theme.LINE_STRONG), 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(area.left(), mid), QPointF(area.right(), mid))
            return

        # A small idle ripple keeps the wave alive between words.
        amp = half_h * (0.06 + 0.94 * self._wave_amp)
        n = self._WAVE_POINTS
        for cycles, speed, rel_amp, opacity, pen_w in self._WAVE_LAYERS:
            phase = self._wave_phase * speed
            curve = QPainterPath()
            fill = QPainterPath()
            fill.moveTo(area.left(), mid)
            for i in range(n + 1):
                t = i / n
                envelope = math.sin(math.pi * t) ** 2  # pin both ends to the midline
                y = mid - amp * rel_amp * envelope * math.sin(math.tau * cycles * t + phase)
                point = QPointF(area.left() + t * area.width(), y)
                if i == 0:
                    curve.moveTo(point)
                else:
                    curve.lineTo(point)
                fill.lineTo(point)
            fill.closeSubpath()

            # Fade both ends so the wave dissolves into the card.
            grad = QLinearGradient(area.left(), 0.0, area.right(), 0.0)
            edge = QColor(self._dot)
            edge.setAlphaF(0.0)
            core = QColor(self._dot)
            core.setAlphaF(opacity)
            grad.setColorAt(0.0, edge)
            grad.setColorAt(0.5, core)
            grad.setColorAt(1.0, edge)

            glow = QLinearGradient(grad)
            core.setAlphaF(opacity * 0.18)
            glow.setColorAt(0.5, core)
            painter.fillPath(fill, glow)

            pen = QPen(grad, pen_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(curve, pen)
