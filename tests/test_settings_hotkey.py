import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from src.config import AppConfig, Hotkey, MOUSE_X1
from src.settings_dialog import (
    HotkeyCaptureEdit,
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


if __name__ == "__main__":
    unittest.main()
