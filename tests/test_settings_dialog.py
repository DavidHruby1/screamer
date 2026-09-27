import os
import contextlib
import io
import runpy
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QDialog

from src.config import AppConfig
from src.utils import AppError, ScreamerError
from src.settings_dialog import PasswordField, SettingsDialog

_app = QApplication.instance() or QApplication([])


class AcceptValidationTests(unittest.TestCase):
    def test_startup_registration_failure_follows_save_and_notifies_owner(self) -> None:
        cfg = AppConfig(stt_api_key="k", stt_base_url="https://example.test/v1", stt_model="m")
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
        events = []
        dlg.applied.connect(lambda: events.append("applied"))

        def fail_registration(_enabled):
            events.append("registry")
            raise ScreamerError(AppError.STARTUP_REGISTRATION_FAILED)

        try:
            with (
                patch(
                    "src.settings_dialog.save_config",
                    side_effect=lambda _cfg: events.append("save"),
                ),
                patch("src.settings_dialog.is_supported", return_value=True),
                patch("src.startup.sync_enabled", side_effect=fail_registration),
                patch("src.settings_dialog.QMessageBox") as message,
            ):
                dlg.accept()
            self.assertEqual(events, ["save", "applied", "registry"])
            self.assertEqual(dlg.result(), 0)
            self.assertIn("Settings were saved", message.warning.call_args.args[2])
        finally:
            dlg.deleteLater()

    def test_standalone_dialog_does_not_print_credentials(self) -> None:
        import src.settings_dialog as module

        cfg = AppConfig(
            stt_api_key="key-must-not-appear",
            stt_custom_headers='{"X-Token":"header-must-not-appear"}',
            stt_base_url="https://user:password-must-not-appear@example.test/v1",
            stt_model="m",
        )
        output = io.StringIO()
        with (
            patch("src.config.load_config", return_value=cfg),
            patch("src.config.import_from_env", side_effect=lambda config: config),
            patch("src.audio.list_devices", return_value=[]),
            patch(
                "PySide6.QtWidgets.QApplication", side_effect=lambda _args: _app, wraps=QApplication
            ),
            patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted),
            contextlib.redirect_stdout(output),
        ):
            namespace = runpy.run_path(module.__file__, run_name="__main__")
        namespace["dlg"].deleteLater()
        self.assertIn("Settings saved", output.getvalue())
        self.assertNotIn("must-not-appear", output.getvalue())

    def test_accept_does_not_change_startup_when_save_fails(self) -> None:
        cfg = AppConfig(
            stt_api_key="k",
            stt_base_url="https://example.test/v1",
            stt_model="m",
            start_with_windows=True,
        )
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
        try:
            with (
                patch(
                    "src.settings_dialog.save_config",
                    side_effect=ScreamerError(AppError.KEY_STORAGE_FAILED),
                ),
                patch.object(dlg, "_sync_startup_or_warn") as sync,
                patch("src.settings_dialog.QMessageBox") as message,
            ):
                dlg.accept()
                sync.assert_not_called()
                message.warning.assert_called_once()
                self.assertEqual(dlg.result(), 0)
        finally:
            dlg.deleteLater()

    def test_unavailable_device_is_preserved_on_apply(self) -> None:
        cfg = AppConfig(
            stt_api_key="k",
            stt_base_url="https://example.test/v1",
            stt_model="m",
            audio_device_id=42,
            audio_device_name="Unplugged microphone",
        )
        dlg = SettingsDialog(cfg, devices=[(1, "Built-in microphone")], calibrate_fn=None)
        try:
            self.assertIn("Unavailable", dlg._device_combo.currentText())
            with (
                patch("src.settings_dialog.save_config") as save,
                patch.object(dlg, "_sync_startup_or_warn", return_value=True),
            ):
                dlg._on_apply()
            saved = save.call_args.args[0]
            self.assertEqual(
                (saved.audio_device_id, saved.audio_device_name), (42, "Unplugged microphone")
            )
        finally:
            dlg.deleteLater()

    def test_accept_blocks_on_invalid_config(self) -> None:
        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=None)
        try:
            with (
                patch("src.settings_dialog.QMessageBox"),
                patch("src.settings_dialog.is_supported", return_value=False),
                patch("src.settings_dialog.save_config"),
            ):
                dlg.accept()
            self.assertEqual(dlg.result(), 0)
        finally:
            dlg.deleteLater()

    def test_accept_passes_with_valid_config(self) -> None:
        cfg = AppConfig(stt_api_key="k", stt_base_url="https://example.test/v1", stt_model="m")
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
        try:
            with (
                patch("src.settings_dialog.QMessageBox"),
                patch("src.settings_dialog.is_supported", return_value=False),
                patch("src.settings_dialog.save_config"),
            ):
                dlg.accept()
            self.assertEqual(dlg.result(), 1)
        finally:
            dlg.deleteLater()


class CalibrateThreadTests(unittest.TestCase):
    def test_calibration_runs_off_ui_thread_and_updates_spin(self) -> None:
        import threading

        started = threading.Event()
        release = threading.Event()

        def calibrate(device_id):
            started.set()
            release.wait(5)
            return 7.5

        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=calibrate)
        try:
            with patch("src.settings_dialog.QMessageBox"):
                dlg._on_calibrate()
                self.assertFalse(dlg._calibrate_btn.isEnabled())
                thread = dlg._calib_thread
                self.assertIsNotNone(thread)
                self.assertTrue(started.wait(5))
                release.set()
                self.assertTrue(thread.wait(5000))
                QApplication.processEvents()
            self.assertIsNone(dlg._calib_thread)
            self.assertEqual(dlg._rms_spin.value(), 7.5)
            self.assertTrue(dlg._calibrate_btn.isEnabled())
        finally:
            release.set()
            dlg.deleteLater()

    def test_close_during_calibration_drops_late_result(self) -> None:
        import threading

        release = threading.Event()

        def slow_calibrate(device_id):
            release.wait(5)
            return 9.9

        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=slow_calibrate)
        try:
            with patch("src.settings_dialog.QMessageBox"):
                dlg._on_calibrate()
                thread = dlg._calib_thread
                before = dlg._rms_spin.value()
                dlg.reject()
                release.set()
                self.assertTrue(thread.wait(5000))
            QApplication.processEvents()
            self.assertEqual(dlg._rms_spin.value(), before)
        finally:
            release.set()
            dlg.deleteLater()

    def test_close_during_stalled_calibration_waits_without_blocking_ui(self) -> None:
        import threading

        started = threading.Event()
        release = threading.Event()

        def calibrate(device_id):
            started.set()
            release.wait(10)
            return 9.9

        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=calibrate)
        try:
            with patch("src.settings_dialog.QMessageBox"):
                dlg._on_calibrate()
                self.assertTrue(started.wait(5))
                thread = dlg._calib_thread
                before = dlg._rms_spin.value()
                dlg.reject()
                self.assertIsNotNone(dlg._calib_thread)
                self.assertEqual(dlg.result(), 0)
                self.assertFalse(dlg._button_box.isEnabled())
                release.set()
                self.assertTrue(thread.wait(5000))
                QApplication.processEvents()
            self.assertIsNone(dlg._calib_thread)
            self.assertEqual(dlg._rms_spin.value(), before)
        finally:
            release.set()
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
