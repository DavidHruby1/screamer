import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.config import AppConfig, load_config, save_config
from src.settings_dialog import SettingsDialog

_app = QApplication.instance() or QApplication([])


class RoadmapSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", directory))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))
        self.enterContext(patch("src.settings_dialog.is_supported", return_value=False))
        self.cfg = AppConfig(stt_base_url="http://127.0.0.1:8000/v1", stt_model="local")
        save_config(self.cfg)
        self.dialog = SettingsDialog(self.cfg, devices=[])
        self.addCleanup(self.dialog.deleteLater)

    def test_vocabulary_and_provider_flags_apply_without_mutating_live_config(self):
        for term in ("Screamer", "QThread"):
            self.dialog._vocabulary_input.setText(term)
            self.dialog._vocabulary_add_btn.click()
        self.dialog._stt_prompt_primary_check.setChecked(True)
        self.dialog._stt_prompt_fallback_check.setChecked(False)
        self.dialog._on_apply()
        restored = load_config()
        self.assertEqual(restored.vocabulary_entries, ["Screamer", "QThread"])
        self.assertTrue(restored.stt_prompt_primary_enabled)
        self.assertFalse(restored.stt_prompt_fallback_enabled)
        self.assertEqual(self.cfg.vocabulary_entries, [])
        self.assertFalse(self.cfg.stt_prompt_primary_enabled)

    def test_cancel_discards_output_history_and_vocabulary_draft(self):
        self.dialog._vocabulary_input.setText("Only in draft")
        self.dialog._vocabulary_add_btn.click()
        self.dialog._output_mode_combo.setCurrentIndex(
            self.dialog._output_mode_combo.findData("copy")
        )
        with patch(
            "src.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            self.dialog._history_check.setChecked(True)
        self.dialog._history_limit_spin.setValue(3)
        self.dialog.reject()
        restored = load_config()
        self.assertEqual(restored.vocabulary_entries, [])
        self.assertEqual(restored.output_mode, "type")
        self.assertFalse(restored.history_enabled)
        self.assertEqual(restored.history_limit, 100)

    def test_history_clear_is_a_separate_explicit_action_even_after_cancel(self):
        requests = []
        self.dialog.clear_history_requested.connect(lambda: requests.append(True))
        with patch(
            "src.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.No
        ):
            self.dialog._clear_history_btn.click()
        self.assertEqual(requests, [])
        with patch(
            "src.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ):
            self.dialog._clear_history_btn.click()
        self.assertEqual(requests, [True])
        self.dialog.reject()
        self.assertEqual(requests, [True])

    def test_keyless_settings_roundtrip_does_not_invent_auth(self):
        self.dialog._on_apply()
        restored = load_config()
        self.assertEqual(restored.stt_api_key, "")
        self.assertEqual(restored.stt_base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual(restored.stt_model, "local")

    def test_replacements_add_update_remove_apply_and_cancel(self):
        d = self.dialog
        d._replacement_from.setText(" screamer ")
        d._replacement_to.setText("Screamer")
        d._on_replacement_add()
        d._replacement_from.setText("q thread")
        d._replacement_to.setText("QThread")
        d._on_replacement_add()
        self.assertEqual(d._replacements, [("screamer", "Screamer"), ("q thread", "QThread")])
        self.assertEqual(d._replacement_from.text(), "")
        # Same heard text (any case) updates the existing pair.
        d._replacement_from.setText("SCREAMER")
        d._replacement_to.setText("Screamer app")
        d._on_replacement_add()
        self.assertEqual(d._replacements, [("q thread", "QThread"), ("SCREAMER", "Screamer app")])
        with patch("src.settings_dialog.QMessageBox.warning") as warning:
            d._replacement_from.setText("  ")
            d._on_replacement_add()
            d._replacement_from.setText("line\nbreak")
            d._on_replacement_add()
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(len(d._replacements), 2)
        d._on_apply()
        self.assertEqual(
            load_config().replacements, [("q thread", "QThread"), ("SCREAMER", "Screamer app")]
        )
        self.assertEqual(self.cfg.replacements, [])
        d._replacement_list.setCurrentRow(0)
        d._on_replacement_remove()
        self.assertEqual(d._replacements, [("SCREAMER", "Screamer app")])
        d.reject()
        self.assertEqual(len(load_config().replacements), 2)

    def test_audio_and_paste_preferences_apply(self):
        d = self.dialog
        self.assertTrue(d._trim_check.isChecked())
        self.assertFalse(d._mute_check.isChecked())
        d._trim_check.setChecked(False)
        d._mute_check.setChecked(True)
        d._output_mode_combo.setCurrentIndex(d._output_mode_combo.findData("paste"))
        d._on_apply()
        restored = load_config()
        self.assertFalse(restored.trim_silence)
        self.assertTrue(restored.mute_while_recording)
        self.assertEqual(restored.output_mode, "paste")


if __name__ == "__main__":
    unittest.main()
