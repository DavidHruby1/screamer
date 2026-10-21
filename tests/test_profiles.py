import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.config import (
    AppConfig,
    AppMapping,
    DEFAULT_LLM_SYSTEM_PROMPT,
    RewriteProfile,
    decode_rewrite_catalog,
    encode_rewrite_catalog,
    load_config,
    resolve_rewrite_profile,
    save_config,
)
from src.settings_dialog import SettingsDialog
from src.injector import InjectionReport, WindowIdentity
from src.main import _WorkerThread
from src.utils import PipelineResult
from tests.test_config import LEGACY_LLM_SYSTEM_PROMPT
from tests.test_tray_menu import make_tray_app

_app = QApplication.instance() or QApplication([])


class ProfileContractsTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", directory))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))

    def test_catalog_roundtrip_and_manual_automatic_global_off_precedence(self):
        custom = RewriteProfile("custom-1", "Coding", "custom", "Preserve identifiers.\r\n")
        catalog = encode_rewrite_catalog(
            [custom], [AppMapping("C:\\Tools\\Editor.exe", "custom-1")]
        )
        profiles, mappings = decode_rewrite_catalog(catalog)
        self.assertEqual(profiles, [custom])
        self.assertEqual(mappings, [AppMapping("c:\\tools\\editor.exe", "custom-1")])
        cfg = AppConfig(llm_enabled=True, rewrite_catalog=catalog, rewrite_manual_profile_id="raw")
        self.assertEqual(resolve_rewrite_profile(cfg, "C:\\Tools\\Editor.exe").id, "raw")
        cfg.rewrite_selection_mode = "automatic"
        self.assertEqual(resolve_rewrite_profile(cfg, "c:/TOOLS/EDITOR.exe"), custom)
        self.assertEqual(resolve_rewrite_profile(cfg, "C:\\Other\\Editor.exe").id, "clean")
        self.assertEqual(resolve_rewrite_profile(cfg, None).id, "clean")
        cfg.llm_enabled = False
        self.assertEqual(resolve_rewrite_profile(cfg, "C:\\Tools\\Editor.exe").id, "raw")
        cfg.llm_enabled = True
        self.assertEqual(resolve_rewrite_profile(cfg, "C:\\Tools\\Editor.exe"), custom)
        save_config(cfg)
        restored = load_config()
        self.assertEqual(restored.rewrite_selection_mode, "automatic")
        self.assertEqual(resolve_rewrite_profile(restored, "C:\\Tools\\Editor.exe"), custom)

    def test_every_saved_legacy_prompt_migrates_once_even_identical_or_empty(self):
        from src.config import _get_qsettings

        for prompt in (
            LEGACY_LLM_SYSTEM_PROMPT,
            DEFAULT_LLM_SYSTEM_PROMPT,
            "",
            " \tCustom\r\n\r\n",
        ):
            with self.subTest(prompt=prompt):
                settings = _get_qsettings()
                settings.clear()
                settings.setValue("llm_system_prompt", prompt)
                settings.setValue("llm_enabled", False)
                settings.sync()
                cfg = load_config()
                profiles, mappings = decode_rewrite_catalog(cfg.rewrite_catalog)
                self.assertEqual(len(profiles), 1)
                self.assertEqual(profiles[0].prompt, prompt)
                self.assertEqual(profiles[0].kind, "custom")
                self.assertEqual(cfg.rewrite_manual_profile_id, profiles[0].id)
                self.assertEqual(mappings, [])
                self.assertFalse(cfg.llm_enabled)
                save_config(cfg)
                restored = load_config()
                self.assertEqual(decode_rewrite_catalog(restored.rewrite_catalog)[0], profiles)
                self.assertEqual(restored.llm_system_prompt, prompt)

    def test_new_and_recorded_v2_defaults_do_not_create_legacy_profiles(self):
        cfg = load_config()
        self.assertEqual(decode_rewrite_catalog(cfg.rewrite_catalog), ([], []))
        self.assertFalse(cfg.llm_enabled)
        self.assertEqual(cfg.rewrite_manual_profile_id, "clean")
        save_config(cfg)
        self.assertEqual(decode_rewrite_catalog(load_config().rewrite_catalog), ([], []))

    def test_catalog_rejects_reserved_ids_duplicate_paths_and_dangling_mappings(self):
        custom = RewriteProfile("custom", "Custom", "custom", "Prompt")
        cases = (
            ([RewriteProfile("clean", "Changed clean", "custom", "Prompt")], []),
            ([custom, custom], []),
            ([custom], [AppMapping("editor.exe", "custom")]),
            ([custom], [AppMapping("C:\\Editor.exe", "missing")]),
            (
                [custom],
                [AppMapping("C:\\Editor.exe", "custom"), AppMapping("c:/EDITOR.exe", "clean")],
            ),
        )
        for profiles, mappings in cases:
            with self.subTest(profiles=profiles, mappings=mappings), self.assertRaises(ValueError):
                encode_rewrite_catalog(profiles, mappings)
        with self.assertRaises(ValueError):
            decode_rewrite_catalog('{"version":1,"version":1,"profiles":[],"mappings":[]}')

    def test_damaged_catalog_survives_unrelated_save_until_explicit_repair(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        damaged = '{"version": 99, "profiles": ["important original"]}'
        settings.setValue("rewrite_catalog", damaged)
        settings.sync()
        cfg = load_config()
        cfg.stt_language = "cs"
        save_config(cfg)
        self.assertEqual(_get_qsettings().value("rewrite_catalog"), damaged)
        with self.assertRaises(ValueError):
            resolve_rewrite_profile(cfg, None)

    def test_failed_first_migration_save_does_not_replace_legacy_disk_state(self):
        from src.config import _get_qsettings
        from src.utils import ScreamerError

        settings = _get_qsettings()
        settings.setValue("llm_system_prompt", "Exact old prompt.\r\n")
        settings.sync()
        cfg = load_config()
        with patch("src.config.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(ScreamerError):
                save_config(cfg)
        self.assertFalse(_get_qsettings().contains("rewrite_catalog"))
        self.assertEqual(load_config().llm_system_prompt, "Exact old prompt.\r\n")


class ProfileSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", directory))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))
        self.enterContext(patch("src.settings_dialog.is_supported", return_value=False))
        self.cfg = AppConfig(stt_base_url="http://localhost:8000/v1", stt_model="local")
        save_config(self.cfg)
        self.dialog = SettingsDialog(self.cfg, devices=[])
        self.addCleanup(self.dialog.deleteLater)

    def test_custom_profile_editor_apply_and_cancel_keep_separate_authority(self):
        self.dialog._rewrite_name_input.setText("Coding")
        self.dialog._rewrite_add_btn.click()
        self.dialog._llm_prompt.setPlainText("Keep technical identifiers.")
        selected = self.dialog._rewrite_profile_combo.currentData()
        self.dialog._rewrite_manual_combo.setCurrentIndex(
            self.dialog._rewrite_manual_combo.findData(selected)
        )
        self.dialog._on_apply()
        restored = load_config()
        profiles, _ = decode_rewrite_catalog(restored.rewrite_catalog)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "Coding")
        self.assertEqual(profiles[0].prompt, "Keep technical identifiers.")
        self.assertEqual(restored.rewrite_manual_profile_id, profiles[0].id)
        self.assertEqual(decode_rewrite_catalog(self.cfg.rewrite_catalog), ([], []))
        self.dialog._llm_prompt.setPlainText("Discard this edit.")
        self.dialog.reject()
        self.assertEqual(
            decode_rewrite_catalog(load_config().rewrite_catalog)[0][0].prompt,
            "Keep technical identifiers.",
        )

    def test_mapping_add_duplicate_reject_and_cancel(self):
        self.dialog._rewrite_path_input.setText("C:\\Tools\\Editor.exe")
        self.dialog._rewrite_mapping_add_btn.click()
        self.assertEqual(self.dialog._rewrite_mappings.count(), 1)
        self.dialog._rewrite_path_input.setText("c:/tools/EDITOR.exe")
        with patch("src.settings_dialog.QMessageBox.warning") as warning:
            self.dialog._rewrite_mapping_add_btn.click()
        warning.assert_called_once()
        self.assertEqual(self.dialog._rewrite_mappings.count(), 1)
        self.dialog.reject()
        self.assertEqual(decode_rewrite_catalog(load_config().rewrite_catalog)[1], [])


class ProfileSessionTests(unittest.TestCase):
    def setUp(self):
        self.tray = make_tray_app()
        self.tray._apply_state = Mock()
        self.tray._on_error = Mock()
        self.tray._recorder = Mock()
        self.tray._recorder.stop.return_value = b"wav"
        self.enterContext(patch("src.main.resolve_device", return_value=None))
        self.enterContext(patch("src.main.AudioRecorder", return_value=self.tray._recorder))
        self.enterContext(patch.object(_WorkerThread, "start"))

    def test_effective_profile_and_target_freeze_once_before_recording(self):
        custom = RewriteProfile("custom-1", "Coding", "custom", "Preserve identifiers.")
        self.tray._config = AppConfig(
            stt_language="cs",
            llm_enabled=True,
            rewrite_selection_mode="automatic",
            rewrite_catalog=encode_rewrite_catalog(
                [custom], [AppMapping("C:\\Tools\\editor.exe", custom.id)]
            ),
            post_type_key="enter",
            vocabulary_entries=["QThread"],
        )
        target = WindowIdentity(100, 200, "C:\\Tools\\editor.exe")
        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._start_recording()
        snapshot = self.tray._session_config
        self.assertEqual(self.tray._session.profile_id, custom.id)
        self.assertEqual(snapshot.llm_system_prompt, custom.prompt)
        self.tray._config.rewrite_selection_mode = "manual"
        self.tray._config.rewrite_manual_profile_id = "raw"
        self.tray._config.llm_enabled = False
        self.tray._config.stt_language = "en"
        self.tray._config.vocabulary_entries.clear()
        self.tray._finalize_recording()
        self.assertIs(self.tray._worker._config, snapshot)
        self.assertEqual(snapshot.stt_language, "cs")
        self.assertEqual(snapshot.vocabulary_entries, ["QThread"])
        with (
            patch("src.main.transcribe", return_value=PipelineResult("raw")),
            patch("src.main.rewrite", return_value=PipelineResult("clean")) as cleanup,
            patch("src.main.get_foreground_target", return_value=WindowIdentity(101, 200)),
            patch("src.main.type_text") as inject,
        ):
            self.tray._worker.run()
        cleanup.assert_called_once_with("raw", snapshot)
        inject.assert_not_called()
        self.assertEqual(self.tray._latest.profile_id, custom.id)
        self.assertEqual(self.tray._latest.language, "cs")
        self.assertEqual(self.tray._latest.target_executable, target.executable_path)
        self.assertEqual(self.tray._latest.last_delivery.text_state, "withheld")
        self.tray._on_worker_finished()

    def test_raw_profile_suppresses_llm_even_with_global_rewrite_enabled(self):
        self.tray._config = AppConfig(llm_enabled=True, rewrite_manual_profile_id="raw")
        target = WindowIdentity(100, 200)
        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._start_recording()
            self.assertEqual(self.tray._session.profile_id, "raw")
            self.tray._finalize_recording()
            with (
                patch("src.main.transcribe", return_value=PipelineResult("raw")),
                patch("src.main.rewrite") as cleanup,
                patch("src.main.type_text", return_value=InjectionReport(6, 0, False)),
            ):
                self.tray._worker.run()
        cleanup.assert_not_called()
        self.assertFalse(self.tray._session_config.llm_enabled)
        self.assertTrue(self.tray._config.llm_enabled)
        self.assertEqual(self.tray._latest.rewrite_status, "not_requested")
        self.tray._on_worker_finished()

    def test_failed_tray_profile_save_restores_visible_selection(self):
        self.tray._config.llm_enabled = True
        self.tray._rebuild_menu()
        menu = next(
            action.menu()
            for action in self.tray._menu.actions()
            if action.text() == "Rewrite Profile"
        )
        raw = next(
            action.defaultWidget()
            for action in menu.actions()
            if action.defaultWidget().text() == "Raw transcription"
        )
        with patch("src.main.save_config", side_effect=OSError("read-only")):
            raw.setChecked(True)
        self.assertEqual(self.tray._config.rewrite_manual_profile_id, "clean")
        menu = next(
            action.menu()
            for action in self.tray._menu.actions()
            if action.text() == "Rewrite Profile"
        )
        selected = [
            action.defaultWidget().text()
            for action in menu.actions()
            if action.defaultWidget().isChecked()
        ]
        self.assertEqual(selected, ["Clean dictation"])


if __name__ == "__main__":
    unittest.main()
