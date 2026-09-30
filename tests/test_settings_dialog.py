import os
import contextlib
import io
import runpy
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
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


class LanguageSettingsTests(unittest.TestCase):
    def test_apply_saves_active_and_favorites_and_cancel_discards_later_edits(self) -> None:
        from src.config import load_config

        cfg = AppConfig(stt_api_key="key", stt_base_url="https://example.test/v1", stt_model="m")
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=False),
            patch("src.settings_dialog.is_supported", return_value=False),
        ):
            dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
            try:
                self.assertEqual(dlg._stt_lang.currentData(), "")
                self.assertEqual([dlg._stt_lang.itemData(i) for i in range(3)], ["", "cs", "en"])
                dlg._stt_favorite_input.setText(" DE ")
                dlg._on_favorite_add()
                dlg._stt_lang.setCurrentIndex(dlg._stt_lang.findData("de"))
                dlg._on_apply()
                self.assertEqual(load_config().stt_language, "de")
                self.assertEqual(load_config().stt_language_favorites, ["de"])

                dlg._stt_favorite_input.setText("fr")
                dlg._on_favorite_add()
                dlg._stt_lang.setCurrentIndex(dlg._stt_lang.findData("fr"))
                dlg.reject()
                self.assertEqual(load_config().stt_language, "de")
                self.assertEqual(load_config().stt_language_favorites, ["de"])
                self.assertEqual(cfg.stt_language_favorites, [])
            finally:
                dlg.deleteLater()

    def test_ok_saves_active_language_and_favorites(self) -> None:
        from src.config import load_config

        cfg = AppConfig(stt_api_key="key", stt_base_url="https://example.test/v1", stt_model="m")
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=False),
            patch("src.settings_dialog.is_supported", return_value=False),
        ):
            dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
            try:
                dlg._stt_favorite_input.setText("pt-BR")
                dlg._on_favorite_add()
                dlg._stt_lang.setCurrentIndex(dlg._stt_lang.findData("pt-br"))
                dlg.accept()
                self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(load_config().stt_language, "pt-br")
                self.assertEqual(load_config().stt_language_favorites, ["pt-br"])
            finally:
                dlg.deleteLater()

    def test_remove_active_additional_favorite_selects_auto_but_builtin_stays(self) -> None:
        cfg = AppConfig(stt_language="de", stt_language_favorites=["cs", "de"])
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
        try:
            dlg._stt_favorites.setCurrentRow(1)
            dlg._on_favorite_remove()
            self.assertEqual(dlg._stt_lang.currentData(), "")
            self.assertEqual(dlg._stt_lang.findData("de"), -1)
            dlg._stt_lang.setCurrentIndex(dlg._stt_lang.findData("cs"))
            dlg._stt_favorites.setCurrentRow(0)
            dlg._on_favorite_remove()
            self.assertEqual(dlg._stt_lang.currentData(), "cs")
            self.assertEqual(dlg._stt_favorites.count(), 0)
            dlg._collect()
            self.assertEqual(dlg.get_config().stt_language_favorites, [])
        finally:
            dlg.deleteLater()

    def test_edit_preserves_selection_and_invalid_or_duplicate_input_is_rejected(self) -> None:
        cfg = AppConfig(stt_language="de", stt_language_favorites=["de", "fr"])
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
        try:
            dlg._stt_favorites.setCurrentRow(0)
            dlg._stt_favorite_input.setText("pt-BR")
            dlg._on_favorite_edit()
            self.assertEqual(dlg._stt_lang.currentData(), "pt-br")
            self.assertEqual(dlg._stt_favorites.item(0).data(Qt.ItemDataRole.UserRole), "pt-br")

            with patch("src.settings_dialog.QMessageBox.warning") as warning:
                dlg._stt_favorite_input.setText("en!")
                dlg._on_favorite_edit()
                dlg._stt_favorite_input.setText("fr")
                dlg._on_favorite_edit()
                warning.assert_called()
            self.assertEqual(dlg._stt_favorites.item(0).data(Qt.ItemDataRole.UserRole), "pt-br")
            self.assertEqual(dlg._stt_lang.currentData(), "pt-br")
        finally:
            dlg.deleteLater()

    def test_unknown_saved_active_code_is_visible_without_becoming_a_favorite(self) -> None:
        dlg = SettingsDialog(
            AppConfig(stt_language="zh_CN", stt_language_favorites=["de"]),
            devices=[],
            calibrate_fn=None,
        )
        try:
            self.assertEqual(dlg._stt_lang.currentText(), "zh_CN")
            self.assertEqual(dlg._stt_favorites.count(), 1)
            dlg._collect()
            self.assertEqual(dlg.get_config().stt_language, "zh_CN")
            self.assertEqual(dlg.get_config().stt_language_favorites, ["de"])
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
