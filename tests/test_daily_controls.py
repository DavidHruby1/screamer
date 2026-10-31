import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.config import (
    AppConfig,
    AppMapping,
    Hotkey,
    encode_rewrite_catalog,
    normalize_executable_path,
    resolve_rewrite_profile,
)
from src.hotkey import HotkeyListener, HotkeyMode
from src.injector import InjectionReport, WindowIdentity
from src.recovery_dialog import RecoveryDialog
from src.results import DictationRecord
from src.settings_dialog import SettingsDialog
from src.utils import AppError, ScreamerError, SignalBridge
from tests.test_tray_menu import make_tray_app


class DailyControlsTests(unittest.TestCase):
    def test_tray_copies_selected_text_without_using_menu_focus_for_injection(self):
        tray = make_tray_app()
        tray._latest = DictationRecord("one", "2026-10-31", "raw", "clean", "succeeded")
        tray._select_history(tray._latest, "raw")
        tray._rebuild_menu()
        actions = {action.text(): action for action in tray._menu.actions()}
        self.assertNotIn("Insert last", actions)
        clipboard = QApplication.clipboard()
        self.addCleanup(clipboard.setText, clipboard.text())
        with (
            patch("src.main.get_foreground_target") as foreground,
            patch("src.main.type_text") as inject,
        ):
            actions["Copy last"].trigger()
        self.assertEqual(clipboard.text(), "raw")
        foreground.assert_not_called()
        inject.assert_not_called()

    def test_escape_and_autorepeat_outside_recording_preserve_history_selection(self):
        tray = make_tray_app()
        tray._latest = DictationRecord("new", "2026-10-31", "new raw", "new", "succeeded")
        older = DictationRecord("old", "2026-10-30", "old raw", "old", "succeeded")
        tray._select_history(older, "raw")
        bridge = SignalBridge()
        bridge.recovery_cancelled.connect(tray._on_escape)
        listener = HotkeyListener(Hotkey.parse("key:0x77"), HotkeyMode.HOLD, bridge)
        for _ in range(3):
            listener._on_kb_event(0x100, 0x1B)
        target = WindowIdentity(100, 200)
        with (
            patch("src.main.get_foreground_target", return_value=target),
            patch("src.main.type_text", return_value=InjectionReport(14, 0, False)) as inject,
        ):
            tray._insert_last()
        inject.assert_called_once_with("old raw", None, expected_target=target)

    def test_history_selection_handles_missing_or_broken_shortcut(self):
        for shortcut in ("", "broken"):
            with self.subTest(shortcut=shortcut):
                tray = make_tray_app()
                tray._config.recovery_hotkey = shortcut
                tray._latest = DictationRecord("one", "2026-10-31", "raw", "clean", "succeeded")
                dialog = RecoveryDialog()
                self.addCleanup(dialog.deleteLater)
                tray._recovery_dlg = dialog
                tray._select_history(tray._latest, "raw")
                self.assertEqual(tray._last_result()[1], "raw")
                self.assertIn("Raw text selected", dialog._notice.text())
                self.assertIn("Set the Insert last shortcut in Settings", dialog._notice.text())

    def test_history_notice_preserves_selected_variant_after_insertion_and_refresh(self):
        tray = make_tray_app()
        tray._latest = DictationRecord("one", "2026-10-31", "raw", "clean", "succeeded")
        dialog = RecoveryDialog()
        self.addCleanup(dialog.deleteLater)
        tray._recovery_dlg = dialog
        tray._select_history(tray._latest, "raw")
        with (
            patch("src.main.get_foreground_target", return_value=WindowIdentity(100, 200)),
            patch("src.main.type_text", return_value=InjectionReport(6, 0, False)),
        ):
            tray._insert_last()
        tray._refresh_recovery()
        self.assertIn("Raw text selected for insertion from 2026-10-31", dialog._notice.text())
        self.assertEqual(tray._last_result()[1], "raw")

    def test_empty_recording_has_no_stop_cue_or_worker(self):
        tray = make_tray_app()
        tray._recorder = Mock()
        tray._recorder.stop.return_value = b""
        tray._apply_state = Mock()
        tray._play_sound = Mock()
        with patch.object(tray, "_start_worker") as worker:
            tray._finalize_recording()
        tray._play_sound.assert_not_called()
        worker.assert_not_called()

    def test_cancel_with_recorder_error_only_plays_error_cue(self):
        tray = make_tray_app()
        tray._recording = True
        tray._recorder = Mock()
        tray._recorder.stop.side_effect = ScreamerError(AppError.MIC_UNAVAILABLE)
        tray._tray = Mock()
        tray._apply_state = Mock()
        tray._play_sound = Mock()
        tray._on_escape()
        tray._play_sound.assert_called_once_with("error")
        self.assertFalse(tray._recording)
        self.assertIsNone(tray._session)

    def test_escape_discards_audio_in_hold_and_toggle_even_after_trigger_release(self):
        for mode in HotkeyMode:
            with self.subTest(mode=mode):
                tray = make_tray_app()
                tray._apply_state = Mock()
                tray._play_sound = Mock()
                recorder = Mock()
                bridge = SignalBridge()
                bridge.hotkey_pressed.connect(tray._on_hotkey_pressed)
                bridge.hotkey_released.connect(tray._on_hotkey_released)
                bridge.recovery_cancelled.connect(tray._on_escape)
                listener = HotkeyListener(Hotkey.parse("key:0x77"), mode, bridge)
                tray._hotkey = listener
                with (
                    patch("src.main.get_foreground_target", return_value=None),
                    patch("src.main.resolve_device", return_value=None),
                    patch("src.main.AudioRecorder", return_value=recorder),
                    patch.object(tray, "_start_worker") as worker,
                ):
                    listener._trigger_down()
                    self.assertTrue(tray._recording)
                    if mode == HotkeyMode.TOGGLE:
                        listener._trigger_up()
                    listener._on_kb_event(0x100, 0x1B)
                    listener._trigger_up()
                    self.assertFalse(tray._recording)
                    self.assertIsNone(tray._session)
                    worker.assert_not_called()
                    recorder.stop.assert_called_once()
                    self.assertEqual(
                        [c.args[0] for c in tray._play_sound.call_args_list], ["start", "cancel"]
                    )

    def test_last_result_inserts_without_history_selection_and_can_be_copied(self):
        tray = make_tray_app()
        tray._latest = DictationRecord("one", "2026-10-31", "raw", "clean", "succeeded")
        target = WindowIdentity(100, 200)
        with (
            patch("src.main.get_foreground_target", return_value=target),
            patch("src.main.type_text", return_value=InjectionReport(10, 0, False)) as inject,
        ):
            tray._insert_last()
        inject.assert_called_once_with("clean", None, expected_target=target)
        tray._copy_last()
        self.assertTrue(tray._latest.last_delivery.copied)

    def test_sound_switch_disables_every_cue(self):
        tray = make_tray_app()
        sound = Mock(SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4)
        with (
            patch("src.main.platform.system", return_value="Windows"),
            patch.dict(sys.modules, winsound=sound),
        ):
            for enabled in (True, False):
                tray._config.sound_enabled = enabled
                sound.PlaySound.reset_mock()
                for cue in ("start", "stop", "cancel", "error"):
                    tray._play_sound(cue)
                self.assertEqual(sound.PlaySound.call_count, 4 if enabled else 0)

    def test_executable_name_mapping_and_existing_full_path_precedence(self):
        cfg = AppConfig(
            llm_enabled=True,
            rewrite_selection_mode="automatic",
            rewrite_catalog=encode_rewrite_catalog(
                [],
                [
                    AppMapping("editor.exe", "raw"),
                    AppMapping(r"C:\Tools\editor.exe", "clean"),
                ],
            ),
        )
        self.assertEqual(resolve_rewrite_profile(cfg, r"D:\Other\EDITOR.exe").id, "raw")
        self.assertEqual(resolve_rewrite_profile(cfg, r"C:\Tools\editor.exe").id, "clean")

    def test_executable_name_requires_name_and_suffix(self):
        for name in ("notepad", ".exe"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, r"\.exe"):
                normalize_executable_path(name)

    def test_populate_does_not_change_config_profile_selection_when_rewrite_is_off(self):
        cfg = AppConfig(llm_enabled=False, rewrite_selection_mode="automatic")
        dialog = SettingsDialog(cfg, devices=[])
        self.addCleanup(dialog.deleteLater)
        working = dialog.get_config()
        before = (working.rewrite_selection_mode, working.rewrite_manual_profile_id)
        dialog._populate(working)
        self.assertEqual(
            (working.rewrite_selection_mode, working.rewrite_manual_profile_id), before
        )
        self.assertEqual(dialog._rewrite_profile_combo.currentData(), "raw")

    def test_insert_shortcut_uses_capture_and_survives_collection(self):
        dialog = SettingsDialog(AppConfig(), devices=[])
        self.addCleanup(dialog.deleteLater)
        events = []
        dialog.hotkey_capture_active_changed.connect(events.append)
        dialog._start_insert_capture()
        self.assertTrue(dialog.is_hotkey_capture_active())
        hotkey = Hotkey.parse("ctrl+shift+key:0x56")
        dialog._on_insert_captured(hotkey)
        self.assertFalse(dialog.is_hotkey_capture_active())
        dialog._collect()
        self.assertEqual(dialog.get_config().recovery_hotkey, hotkey.to_canonical())
        self.assertEqual(events, [True, False])

    def test_cancel_insert_capture_without_previous_binding_leaves_empty_field(self):
        dialog = SettingsDialog(AppConfig(recovery_hotkey=""), devices=[])
        self.addCleanup(dialog.deleteLater)
        dialog._start_insert_capture()
        dialog._recovery_hotkey_input.cancelled.emit()
        self.assertFalse(dialog.is_hotkey_capture_active())
        self.assertEqual(dialog._recovery_hotkey_input.text(), "")
        self.assertEqual(dialog._recovery_hotkey_input.placeholderText(), "No shortcut set")
        dialog._collect()
        self.assertEqual(dialog.get_config().recovery_hotkey, "")

    def test_insert_capture_accepts_function_key_without_modifiers(self):
        dialog = SettingsDialog(AppConfig(), devices=[])
        self.addCleanup(dialog.deleteLater)
        dialog._start_insert_capture()
        dialog._on_insert_captured(Hotkey.parse("key:0x77"))
        dialog._collect()
        self.assertEqual(dialog.get_config().recovery_hotkey, "key:0x77")
