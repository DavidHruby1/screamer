import os
import contextlib
import io
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QDialog

from src.config import AppConfig, load_config, save_config
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


class PromptSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        from src.config import RewriteProfile, encode_rewrite_catalog

        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", tmp))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))
        self.enterContext(patch("src.settings_dialog.is_supported", return_value=False))
        self.prompt = " \tSaved prompt\r\nKeep whitespace.  \r\n\r\n"
        self.cfg = AppConfig(
            stt_api_key="key",
            stt_base_url="https://example.test/v1",
            stt_model="stt",
            llm_api_key="test",
            llm_base_url="https://example.test/v1",
            llm_model="test",
            rewrite_catalog=encode_rewrite_catalog(
                [RewriteProfile("legacy", "Saved rewrite prompt", "custom", self.prompt)], []
            ),
            rewrite_manual_profile_id="legacy",
        )
        save_config(self.cfg)
        self.dlg = SettingsDialog(self.cfg, devices=[])
        self.addCleanup(self.dlg.deleteLater)

    def _saved_prompt(self) -> str:
        from src.config import resolve_rewrite_profile

        return resolve_rewrite_profile(load_config(), None).prompt

    def test_unchanged_apply_and_ok_preserve_saved_prompt_exactly(self) -> None:
        self.dlg._mode_toggle.setChecked(True)
        self.dlg._on_apply()
        self.dlg._on_apply()
        self.dlg.accept()
        self.assertEqual(self.dlg.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self._saved_prompt(), self.prompt)
        self.assertEqual(load_config().recording_mode, "toggle")

    def test_edit_then_cancel_keeps_saved_prompt(self) -> None:
        self.assertEqual(self.dlg._rewrite_profile_combo.currentData(), "legacy")
        self.dlg._llm_prompt.setPlainText("Discarded.")
        self.dlg.reject()
        self.assertEqual(self._saved_prompt(), self.prompt)

    def test_failed_apply_keeps_saved_prompt_and_undo_drops_the_edit(self) -> None:
        for failure in ("validation", "save"):
            with self.subTest(failure=failure):
                save_config(self.cfg)
                dlg = SettingsDialog(self.cfg, devices=[])
                self.addCleanup(dlg.deleteLater)
                dlg._llm_prompt.insertPlainText("Temporary edit. ")
                if failure == "validation":
                    dlg._stt_model.clear()
                with (
                    patch(
                        "src.config.os.replace", side_effect=OSError("read-only disk")
                    ) as replace,
                    patch("src.settings_dialog.QMessageBox.warning") as warning,
                ):
                    dlg._on_apply()
                warning.assert_called_once()
                self.assertEqual(replace.call_count, 0 if failure == "validation" else 1)
                self.assertEqual(self._saved_prompt(), self.prompt)
                dlg._llm_prompt.undo()
                dlg._stt_model.setText("stt")
                dlg._on_apply()
                # Undo restores the text; Qt normalizes line endings once it was edited.
                self.assertEqual(self._saved_prompt(), self.prompt.replace("\r\n", "\n"))


class MicrophoneSettingsTests(unittest.TestCase):
    def test_refresh_preserves_unavailable_device_on_apply_and_explicit_reselection(self) -> None:
        cfg = AppConfig(
            stt_api_key="k",
            stt_base_url="https://example.test/v1",
            stt_model="m",
            audio_device_id=42,
            audio_device_name="USB mic",
        )
        refresh = Mock(return_value=[(3, "Laptop mic (Default input)")])
        dlg = SettingsDialog(cfg, devices=[(1, "Laptop mic")], refresh_devices_fn=refresh)
        self.addCleanup(dlg.deleteLater)
        refresh.assert_not_called()
        dlg._refresh_devices_btn.click()
        self.assertIn("Unavailable", dlg._device_combo.currentText())
        with (
            patch("src.settings_dialog.save_config") as save,
            patch.object(dlg, "_sync_startup_or_warn", return_value=True),
        ):
            dlg._on_apply()
        self.assertEqual(
            (save.call_args.args[0].audio_device_id, save.call_args.args[0].audio_device_name),
            (42, "USB mic"),
        )
        dlg._device_combo.setCurrentIndex(dlg._device_combo.findData(3))
        dlg._collect()
        self.assertEqual(
            (dlg.get_config().audio_device_id, dlg.get_config().audio_device_name),
            (3, "Laptop mic"),
        )
        dlg.reject()
        self.assertEqual((cfg.audio_device_id, cfg.audio_device_name), (42, "USB mic"))

    def test_refresh_recognizes_unique_exact_replug_without_substring_substitution(self) -> None:
        cfg = AppConfig(audio_device_id=42, audio_device_name="USB mic")
        dlg = SettingsDialog(
            cfg, devices=[(1, "USB mic Pro")], refresh_devices_fn=lambda: [(7, "USB mic")]
        )
        self.addCleanup(dlg.deleteLater)
        self.assertIn("Unavailable", dlg._device_combo.currentText())
        dlg.refresh_devices()
        self.assertEqual(dlg._device_combo.currentData(), 7)
        self.assertNotIn("Unavailable", dlg._device_combo.currentText())
        dlg._collect()
        self.assertEqual(dlg.get_config().audio_device_name, "USB mic")

    def test_refresh_failure_keeps_current_choice_and_reports_failure(self) -> None:
        for outcome in ([], OSError("enumeration failed")):
            with self.subTest(outcome=outcome):
                refresh = (
                    Mock(side_effect=outcome)
                    if isinstance(outcome, Exception)
                    else Mock(return_value=outcome)
                )
                dlg = SettingsDialog(
                    AppConfig(audio_device_id=2, audio_device_name="USB mic"),
                    devices=[(2, "USB mic")],
                    refresh_devices_fn=refresh,
                )
                try:
                    with patch("src.settings_dialog.QMessageBox.warning") as warning:
                        dlg.refresh_devices()
                    warning.assert_called_once()
                    dlg._collect()
                    self.assertEqual(
                        (dlg.get_config().audio_device_id, dlg.get_config().audio_device_name),
                        (2, "USB mic"),
                    )
                finally:
                    dlg.deleteLater()

    def test_conflicting_or_duplicate_names_are_not_silently_selected(self) -> None:
        for devices in ([(2, "Other mic"), (3, "USB mic")], [(2, "USB mic"), (3, "USB mic")]):
            with self.subTest(devices=devices):
                dlg = SettingsDialog(
                    AppConfig(audio_device_id=2, audio_device_name="USB mic"), devices=devices
                )
                try:
                    self.assertIn("Unavailable", dlg._device_combo.currentText())
                    self.assertTrue(dlg._device_combo.itemText(1).startswith("[2]"))
                    self.assertTrue(dlg._device_combo.itemText(2).startswith("[3]"))
                    dlg._collect()
                    self.assertEqual(
                        (dlg.get_config().audio_device_id, dlg.get_config().audio_device_name),
                        (2, "USB mic"),
                    )
                finally:
                    dlg.deleteLater()

    def test_unavailable_input_cannot_calibrate_another_default(self) -> None:
        calibrate = Mock()
        dlg = SettingsDialog(
            AppConfig(audio_device_id=42, audio_device_name="USB mic"),
            devices=[(1, "Laptop mic")],
            calibrate_fn=calibrate,
        )
        self.addCleanup(dlg.deleteLater)
        with patch("src.settings_dialog.QMessageBox.warning") as warning:
            dlg._on_calibrate()
        warning.assert_called_once()
        calibrate.assert_not_called()
        self.assertIsNone(dlg._calib_thread)

    def test_calibration_revalidates_current_identity_before_using_selected_id(self) -> None:
        from src.audio import AudioDevice

        for name in ("USB mic", "Reassigned mic"):
            with self.subTest(current_name=name):
                calibrate = Mock(return_value=7.5)
                dlg = SettingsDialog(
                    AppConfig(audio_device_id=2, audio_device_name="USB mic"),
                    devices=[(2, "USB mic")],
                    calibrate_fn=calibrate,
                )
                try:
                    with (
                        patch("src.audio.list_devices", return_value=[AudioDevice(2, name, 1)]),
                        patch("src.settings_dialog.QMessageBox") as message,
                    ):
                        dlg._on_calibrate()
                        self.assertTrue(dlg._calib_thread.wait(5000))
                        QApplication.processEvents()
                    if name == "USB mic":
                        calibrate.assert_called_once_with(2)
                        self.assertEqual(dlg._rms_spin.value(), 7.5)
                    else:
                        calibrate.assert_not_called()
                        message.warning.assert_called_once()
                finally:
                    dlg.deleteLater()


def _check_favorite(dlg, code: str, checked: bool = True) -> None:
    for i in range(dlg._stt_favorites.count()):
        item = dlg._stt_favorites.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == code:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            return
    raise AssertionError(f"{code} is not offered as a favorite")


def _favorite_items(dlg) -> dict[str, bool]:
    return {
        dlg._stt_favorites.item(i).data(Qt.ItemDataRole.UserRole): (
            dlg._stt_favorites.item(i).checkState() == Qt.CheckState.Checked
        )
        for i in range(dlg._stt_favorites.count())
    }


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
                self.assertEqual(dlg._stt_lang.count(), 3)
                self.assertFalse(any(_favorite_items(dlg).values()))
                _check_favorite(dlg, "de")
                dlg._stt_lang.setCurrentIndex(dlg._stt_lang.findData("de"))
                dlg._on_apply()
                self.assertEqual(load_config().stt_language, "de")
                self.assertEqual(load_config().stt_language_favorites, ["de"])

                _check_favorite(dlg, "fr")
                dlg._stt_lang.setCurrentIndex(dlg._stt_lang.findData("fr"))
                dlg.reject()
                self.assertEqual(load_config().stt_language, "de")
                self.assertEqual(load_config().stt_language_favorites, ["de"])
                self.assertEqual(cfg.stt_language_favorites, [])
            finally:
                dlg.deleteLater()

    def test_saved_custom_favorite_is_offered_and_round_trips(self) -> None:
        from src.config import COMMON_LANGUAGES, load_config

        cfg = AppConfig(
            stt_api_key="key",
            stt_base_url="https://example.test/v1",
            stt_model="m",
            stt_language="pt-br",
            stt_language_favorites=["pt-br"],
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=False),
            patch("src.settings_dialog.is_supported", return_value=False),
        ):
            dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
            try:
                items = _favorite_items(dlg)
                self.assertEqual(
                    list(items), [code for code, _label in COMMON_LANGUAGES] + ["pt-br"]
                )
                self.assertEqual([code for code, on in items.items() if on], ["pt-br"])
                dlg.accept()
                self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(load_config().stt_language, "pt-br")
                self.assertEqual(load_config().stt_language_favorites, ["pt-br"])
            finally:
                dlg.deleteLater()

    def test_unchecking_favorites_updates_choices_but_keeps_active_code(self) -> None:
        cfg = AppConfig(stt_language="de", stt_language_favorites=["de", "fr"])
        dlg = SettingsDialog(cfg, devices=[], calibrate_fn=None)
        try:
            self.assertNotEqual(dlg._stt_lang.findData("fr"), -1)
            _check_favorite(dlg, "fr", False)
            self.assertEqual(dlg._stt_lang.findData("fr"), -1)
            _check_favorite(dlg, "de", False)
            # The active language stays selectable even when it is no longer a favorite.
            self.assertEqual(dlg._stt_lang.currentData(), "de")
            dlg._collect()
            self.assertEqual(dlg.get_config().stt_language_favorites, [])
            self.assertEqual(dlg.get_config().stt_language, "de")
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
            self.assertEqual([c for c, on in _favorite_items(dlg).items() if on], ["de"])
            self.assertNotIn("zh_CN", _favorite_items(dlg))
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


class SidebarNavigationTests(unittest.TestCase):
    """The sidebar replaced QTabWidget visually; tab indexes must keep their meaning."""

    def test_tab_indexes_text_and_validation_jump_are_preserved(self) -> None:
        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=None)
        self.addCleanup(dlg.deleteLater)
        tabs = dlg._tabs
        self.assertEqual(
            [tabs.tabText(i) for i in range(tabs.count())],
            ["General", "STT", "LLM", "Audio", "Vocabulary", "Profiles"],
        )
        tabs.setCurrentIndex(2)
        self.assertEqual(tabs.currentIndex(), 2)
        tabs.setTabText(4, "Vocabulary ⚠")
        self.assertEqual(tabs.tabText(4), "Vocabulary ⚠")
        self.assertIn("⚠", tabs.nav.item(4).text())
        with patch("src.settings_dialog.QMessageBox"):
            self.assertFalse(dlg._show_validation_issue())  # empty STT config
        self.assertEqual(tabs.currentIndex(), 1)

    def test_toggle_switch_keeps_checkbox_behavior(self) -> None:
        from PySide6.QtWidgets import QCheckBox

        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=None)
        self.addCleanup(dlg.deleteLater)
        self.assertIsInstance(dlg._sound_check, QCheckBox)
        dlg._sound_check.setChecked(False)
        dlg._sound_check.click()
        self.assertTrue(dlg._sound_check.isChecked())
        self.assertFalse(dlg._llm_group.isHidden())
