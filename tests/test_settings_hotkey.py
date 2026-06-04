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
