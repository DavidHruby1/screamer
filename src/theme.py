"""Hush: Screamer's visual design system (tokens, fonts, stylesheet, window polish).

Appearance only — nothing here changes application behavior. Imports cleanly on
any OS; the Win32 title-bar polish is guarded at runtime.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path
from string import Template

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPalette,
)
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QMenu, QWidget

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# --- Color tokens ---------------------------------------------------------
# Surfaces: ink black with a faint blue undertone, stepped by elevation.
CANVAS = "#0E0F12"
SURFACE = "#15171B"
RAISED = "#1C1F24"
HOVER = "#23272D"
LINE = "#262A30"
LINE_STRONG = "#353A42"
TEXT = "#ECEDEE"
TEXT_2 = "#A3A8B0"
TEXT_3 = "#6B717A"

# Signal colors: each carries exactly one meaning.
EMBER = "#FF7A4D"  # brand + live recording
EMBER_HOVER = "#FF8F68"
EMBER_PRESSED = "#E86A3F"
EMBER_TEXT = "#FFB394"  # ember text on dark surfaces
ON_EMBER = "#1A0E09"
TIDE = "#7CC4E8"  # processing
MOSS = "#5FD4A0"  # success
ROSE = "#F0607A"  # error / destructive
AMBER = "#F2B759"  # warning


def rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


# --- Typography -----------------------------------------------------------

UI_FAMILIES = ["Geist", "Segoe UI Variable Text", "Segoe UI", "Inter", "sans-serif"]
MONO_FAMILIES = ["Geist Mono", "Cascadia Mono", "Consolas", "monospace"]
_fonts_loaded = False


def load_fonts() -> None:
    """Register the bundled Geist families once (missing files fall back silently)."""
    global _fonts_loaded
    # QFontDatabase needs a live QGuiApplication; without one, stay unloaded.
    if _fonts_loaded or QGuiApplication.instance() is None:
        return
    _fonts_loaded = True
    for path in sorted((ASSETS_DIR / "fonts").glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(path)) < 0:
            log.warning("Could not load bundled font %s", path.name)


def ui_font(
    pixel_size: int = 13,
    weight: QFont.Weight = QFont.Weight.Normal,
    letter_spacing: float = 0.0,
) -> QFont:
    font = QFont()
    font.setFamilies(UI_FAMILIES)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    if letter_spacing:
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0 + letter_spacing)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def mono_font(pixel_size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


# --- Brand mark -----------------------------------------------------------

# Three rounded bars on a 16-unit grid: (x, top, bottom). Reads as a waveform.
_MARK_BARS = ((2.0, 4.5, 11.5), (6.5, 2.0, 14.0), (11.0, 3.5, 12.5))
_MARK_BAR_WIDTH = 3.0


def draw_mark(painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Paint the Screamer mark into *rect* (square)."""
    scale = min(rect.width(), rect.height()) / 16.0
    ox = rect.x() + (rect.width() - 16.0 * scale) / 2
    oy = rect.y() + (rect.height() - 16.0 * scale) / 2
    width = _MARK_BAR_WIDTH * scale
    path = QPainterPath()
    for x, top, bottom in _MARK_BARS:
        path.addRoundedRect(
            QRectF(ox + x * scale, oy + top * scale, width, (bottom - top) * scale),
            width / 2,
            width / 2,
        )
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillPath(path, color)
    painter.restore()


# --- Palette + stylesheet -------------------------------------------------


def _palette() -> QPalette:
    pal = QPalette()
    roles = QPalette.ColorRole
    for role, value in (
        (roles.Window, CANVAS),
        (roles.WindowText, TEXT),
        (roles.Base, RAISED),
        (roles.AlternateBase, SURFACE),
        (roles.Text, TEXT),
        (roles.Button, RAISED),
        (roles.ButtonText, TEXT),
        (roles.BrightText, TEXT),
        (roles.ToolTipBase, RAISED),
        (roles.ToolTipText, TEXT),
        (roles.PlaceholderText, TEXT_3),
        (roles.Link, EMBER),
        (roles.Light, LINE_STRONG),
        (roles.Midlight, LINE),
        (roles.Mid, LINE),
        (roles.Dark, CANVAS),
        (roles.Shadow, "#000000"),
        (roles.HighlightedText, TEXT),
    ):
        pal.setColor(role, QColor(value))
    pal.setColor(roles.Highlight, qcolor(EMBER, 0.35))
    disabled = QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        pal.setColor(disabled, role, QColor(TEXT_3))
    return pal


_QSS = Template(
    """
QDialog, QMessageBox, QInputDialog { background: $canvas; }
QLabel { background: transparent; }
QToolTip {
    background: $raised; color: $text; border: 1px solid $line_strong; padding: 6px 8px;
}

QFrame#hushSidebar { background: $surface; border: none; border-right: 1px solid $line; }
QFrame#hushFooter { background: $canvas; border: none; border-top: 1px solid $line; }
QWidget#hushPage, QWidget#hushPageBody { background: $canvas; }
QScrollArea#hushScroll { background: transparent; border: none; }
QFrame#hushDivider { background: $line; border: none; }

QLabel[hush="title"] { color: $text; }
QLabel[hush="subtitle"] { color: $text_2; }
QLabel[hush="section"] { color: $text_3; }
QLabel[hush="brand"] { color: $text; }
QLabel[hush="hint"] { color: $text_2; }
QLabel[hush="meta"] { color: $text_2; }
QLabel[hush="error"] { color: $rose; }
QLabel[hush="warning"] {
    color: $amber; background: rgba(242, 183, 89, 0.08);
    border: 1px solid rgba(242, 183, 89, 0.28); border-radius: 8px; padding: 10px 12px;
}

QLineEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox {
    background: $raised; color: $text; border: 1px solid $line; border-radius: 7px;
    padding: 6px 10px; selection-background-color: rgba(255, 122, 77, 0.35);
    selection-color: $text;
}
QLineEdit, QAbstractSpinBox, QComboBox { min-height: 20px; }
QPlainTextEdit { padding: 6px 8px; }
QLineEdit:hover, QPlainTextEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {
    border-color: $line_strong;
}
QLineEdit:focus, QPlainTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus,
QComboBox:on {
    border-color: rgba(255, 122, 77, 0.75);
}
QLineEdit:disabled, QPlainTextEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {
    color: $text_3; background: $surface; border-color: $line;
}
QLineEdit[hushRecording="true"] {
    color: $ember_text; background: rgba(255, 122, 77, 0.08); border-color: $ember;
}

QComboBox { padding-right: 30px; combobox-popup: 0; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 28px; border: none; background: transparent;
}
QComboBox::down-arrow { image: url("$chevron_down"); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background: $raised; color: $text; border: 1px solid $line_strong; padding: 4px;
    outline: 0; selection-background-color: $hover; selection-color: $text;
}
QComboBox QAbstractItemView::item { min-height: 28px; padding: 0 8px; border-radius: 6px; }
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {
    background: $hover; color: $text;
}

QAbstractSpinBox { padding-right: 26px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border; width: 24px; border: none; background: transparent;
}
QAbstractSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 7px; }
QAbstractSpinBox::down-button {
    subcontrol-position: bottom right; border-bottom-right-radius: 7px;
}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: $hover; }
QAbstractSpinBox::up-arrow { image: url("$chevron_up"); width: 10px; height: 10px; }
QAbstractSpinBox::down-arrow { image: url("$chevron_down"); width: 10px; height: 10px; }

QPushButton {
    background: $raised; color: $text; border: 1px solid $line; border-radius: 7px;
    padding: 7px 14px; min-height: 18px;
}
QPushButton:hover { background: $hover; border-color: $line_strong; }
QPushButton:pressed { background: $line; }
QPushButton:focus { border-color: rgba(255, 122, 77, 0.6); }
QPushButton:disabled { color: $text_3; background: $surface; border-color: $line; }
QPushButton:checked {
    color: $ember_text; background: rgba(255, 122, 77, 0.10); border-color: $ember;
}
QPushButton[hush="primary"], QDialogButtonBox QPushButton:default,
QMessageBox QPushButton:default {
    background: $ember; color: $on_ember; border: 1px solid $ember; font-weight: 600;
}
QPushButton[hush="primary"]:hover, QDialogButtonBox QPushButton:default:hover,
QMessageBox QPushButton:default:hover {
    background: $ember_hover; border-color: $ember_hover;
}
QPushButton[hush="primary"]:pressed, QDialogButtonBox QPushButton:default:pressed,
QMessageBox QPushButton:default:pressed {
    background: $ember_pressed; border-color: $ember_pressed;
}
QPushButton[hush="primary"]:focus, QDialogButtonBox QPushButton:default:focus,
QMessageBox QPushButton:default:focus {
    border-color: $ember_text;
}
QPushButton[hush="primary"]:disabled, QDialogButtonBox QPushButton:default:disabled {
    background: rgba(255, 122, 77, 0.30); border-color: transparent; color: $on_ember;
}
QPushButton[hush="ghost"] { background: transparent; border-color: transparent; color: $text_2; }
QPushButton[hush="ghost"]:hover { background: $hover; color: $text; }
QPushButton[hush="ghost"]:pressed { background: $line; }
QPushButton[hush="ghost"]:disabled { background: transparent; color: $text_3; }
QPushButton[hush="danger"] { color: $rose; }
QPushButton[hush="danger"]:hover {
    background: rgba(240, 96, 122, 0.10); border-color: rgba(240, 96, 122, 0.40);
}
QPushButton[hush="danger"]:disabled { color: $text_3; }
QDialogButtonBox { dialogbuttonbox-buttons-have-icons: 0; }
QDialogButtonBox QPushButton { min-width: 64px; }

QCheckBox, QRadioButton { spacing: 10px; background: transparent; color: $text; }
QCheckBox:disabled, QRadioButton:disabled { color: $text_3; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3A4048;
    background: $raised;
}
QCheckBox::indicator:hover { border-color: $text_3; }
QCheckBox::indicator:checked { background: $ember; border-color: $ember; image: url("$check"); }
QCheckBox::indicator:disabled { background: $surface; border-color: $line; }
QRadioButton::indicator { width: 16px; height: 16px; image: url("$radio_off"); }
QRadioButton::indicator:checked { image: url("$radio_on"); }

QGroupBox {
    background: $surface; border: 1px solid $line; border-radius: 12px;
    margin-top: 28px; color: $text_2;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left; left: 0; top: 2px;
    padding: 0; color: $text_2; background: transparent;
}
QGroupBox QGroupBox { background: $canvas; }

QListWidget, QListView {
    background: $surface; color: $text; border: 1px solid $line; border-radius: 10px;
    padding: 4px; outline: 0;
}
QListWidget::item, QListView::item { padding: 6px 10px; border-radius: 6px; }
QListWidget::item:hover, QListView::item:hover { background: $hover; }
QListWidget::item:selected, QListView::item:selected {
    background: rgba(255, 122, 77, 0.14); color: $text;
}
QListWidget:disabled { color: $text_3; }
QListWidget#hushNav { background: transparent; border: none; padding: 0; }
QListWidget#hushNav::item {
    padding: 8px 12px; margin: 1px 0; color: $text_2; border-radius: 8px;
}
QListWidget#hushNav::item:hover { background: $hover; color: $text; }
QListWidget#hushNav::item:selected { background: rgba(255, 122, 77, 0.12); color: $ember_text; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $line_strong; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:horizontal { background: $line_strong; border-radius: 3px; min-width: 28px; }
QScrollBar::handle:hover { background: $text_3; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenu {
    background: $surface; color: $text; border: 1px solid $line_strong; padding: 6px;
}
QMenu[hush="round"] { border-radius: 12px; }
QMenu::item { padding: 7px 30px 7px 12px; border-radius: 7px; background: transparent; }
QMenu::item:selected { background: $hover; }
QMenu::item:disabled { color: $text_3; }
QMenu::separator { height: 1px; background: $line; margin: 5px 8px; }
QMenu::right-arrow { image: url("$chevron_right"); width: 12px; height: 12px; }
QMenu QCheckBox, QMenu QRadioButton {
    padding: 7px 12px; border-radius: 7px; background: transparent;
}
QMenu QCheckBox:hover, QMenu QRadioButton:hover { background: $hover; }
"""
)


def stylesheet() -> str:
    icons = ASSETS_DIR / "icons"
    return _QSS.substitute(
        canvas=CANVAS,
        surface=SURFACE,
        raised=RAISED,
        hover=HOVER,
        line=LINE,
        line_strong=LINE_STRONG,
        text=TEXT,
        text_2=TEXT_2,
        text_3=TEXT_3,
        ember=EMBER,
        ember_hover=EMBER_HOVER,
        ember_pressed=EMBER_PRESSED,
        ember_text=EMBER_TEXT,
        on_ember=ON_EMBER,
        rose=ROSE,
        amber=AMBER,
        **{
            name.replace("-", "_"): (icons / f"{name}.svg").as_posix()
            for name in (
                "chevron-down",
                "chevron-up",
                "chevron-right",
                "check",
                "radio-on",
                "radio-off",
            )
        },
    )


def set_role(widget: QWidget, role: str) -> QWidget:
    """Tag *widget* with a Hush style role (matched by ``[hush="role"]`` selectors)."""
    widget.setProperty("hush", role)
    # Fresh widgets pick the role up on first polish; repolishing them early would
    # discard fonts set right after this call.
    if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
    return widget


def polish_menu(menu: QMenu) -> QMenu:
    """Give a top-level popup menu rounded, shadow-free corners."""
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setProperty("hush", "round")
    return menu


# --- Native window polish (Windows) ---------------------------------------

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _colorref(hex_color: str) -> int:
    r, g, b = rgb(hex_color)
    return r | (g << 8) | (b << 16)


def polish_native_window(widget: QWidget) -> None:
    """Dark title bar matching the canvas on Windows 10/11; no-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(widget.winId()))
        for attribute, value in (
            (_DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (_DWMWA_CAPTION_COLOR, _colorref(CANVAS)),
            (_DWMWA_BORDER_COLOR, _colorref(LINE)),
            (_DWMWA_TEXT_COLOR, _colorref(TEXT)),
        ):
            data = ctypes.c_int(value)
            # Older builds reject unknown attributes with an HRESULT; that is fine.
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
    except Exception:
        log.debug("Native title bar polish unavailable", exc_info=True)


# Qt style sheets ignore padding-left on a spin box's inner editor, so the text
# would sit on the border; inset it to line up with QLineEdit/QComboBox text.
_SPIN_TEXT_INSET = 9


class _WindowPolisher(QObject):
    """Insets spin box text and, on Windows, polishes native title bars on first show."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Polish and isinstance(watched, QAbstractSpinBox):
            editor = watched.lineEdit()
            if editor is not None:
                editor.setTextMargins(_SPIN_TEXT_INSET, 0, 0, 0)
        elif (
            sys.platform == "win32"
            and event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
            and watched.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog)
            and not watched.property("_hushNativePolished")
        ):
            watched.setProperty("_hushNativePolished", True)
            polish_native_window(watched)
        return False


_polisher: _WindowPolisher | None = None


def apply_theme(app: QApplication) -> None:
    """Install fonts, palette, stylesheet and native polish on *app* (idempotent)."""
    global _polisher
    if not isinstance(app, QApplication):
        return  # e.g. a test double standing in for the application
    load_fonts()
    app.setStyle("Fusion")
    app.setFont(ui_font(13))
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
    if _polisher is None:
        _polisher = _WindowPolisher(app)
        app.installEventFilter(_polisher)
