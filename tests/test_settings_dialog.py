import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication, QLineEdit

from src.config import AppConfig
from src.settings_dialog import PasswordField, SettingsDialog

_app = QApplication.instance() or QApplication([])


class AcceptValidationTests(unittest.TestCase):
    def test_accept_blocks_on_invalid_config(self) -> None:
        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=None)
        try:
            with patch("src.settings_dialog.QMessageBox"), patch(
                "src.settings_dialog.is_supported", return_value=False
            ):
                dlg.accept()
            self.assertEqual(dlg.result(), 0)
        finally:
            dlg.deleteLater()

    def test_accept_passes_with_valid_config(self) -> None:
        cfg = AppConfig(stt_api_key="k", stt_base_url="https://example.test/v1", stt_model="m")
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
        try:
            with patch("src.settings_dialog.QMessageBox"), patch(
                "src.settings_dialog.is_supported", return_value=False
            ):
                dlg.accept()
            self.assertEqual(dlg.result(), 1)
        finally:
            dlg.deleteLater()


class PasswordFieldTests(unittest.TestCase):
    def test_stays_masked_on_focus(self) -> None:
        field = PasswordField()
        field.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
        self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Password)

    def test_trailing_action_toggles_visibility(self) -> None:
        field = PasswordField()
        action = field.actions()[0]
        self.assertTrue(action.isCheckable())

        action.setChecked(True)
        self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Normal)

        action.setChecked(False)
        self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Password)


if __name__ == "__main__":
    unittest.main()
