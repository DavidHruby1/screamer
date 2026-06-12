import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMenu

from src.main import _TrayApp


def make_tray_app():
    app = QApplication.instance() or QApplication([])
    tray_app = _TrayApp.__new__(_TrayApp)
    QObject.__init__(tray_app)
    tray_app._menu = QMenu()
    return tray_app


class TrayMenuTests(unittest.TestCase):
    def test_startup_mode_suppresses_incomplete_config_settings_dialog(self):
        from src.config import AppConfig

        app = QApplication.instance() or QApplication([])
        del app

        patches = [
            patch("src.main.load_config", return_value=AppConfig()),
            patch("src.main.import_from_env", side_effect=lambda cfg: cfg),
            patch("src.main.save_config"),
            patch("src.main.validate_config", return_value=[object()]),
            patch("src.main.AudioRecorder"),
            patch.object(_TrayApp, "_build_tray"),
            patch.object(_TrayApp, "_build_hotkey"),
            patch.object(_TrayApp, "_apply_state"),
            patch("src.main.has_plaintext_secrets", return_value=False),
            patch.object(_TrayApp, "_open_settings"),
        ]

        started = [p.start() for p in patches]
        try:
            _TrayApp(startup_mode=True)
            started[-1].assert_not_called()

            _TrayApp(startup_mode=False)
            started[-1].assert_called_once_with()
        finally:
            for p in reversed(patches):
                p.stop()

    def _startup_patches(self, import_side_effect):
        from src.config import AppConfig

        return [
            patch("src.main.load_config", return_value=AppConfig()),
            patch("src.main.import_from_env", side_effect=import_side_effect),
            patch("src.main.save_config"),
            patch("src.main.validate_config", return_value=[]),
            patch("src.main.AudioRecorder"),
            patch.object(_TrayApp, "_build_tray"),
            patch.object(_TrayApp, "_build_hotkey"),
            patch.object(_TrayApp, "_apply_state"),
            patch.object(_TrayApp, "_open_settings"),
            patch("src.main.has_plaintext_secrets", return_value=False),
        ]

    def test_startup_save_skipped_when_env_adds_nothing(self):
        patches = self._startup_patches(lambda cfg: cfg)
        started = [p.start() for p in patches]
        try:
            _TrayApp(startup_mode=True)
            save_config_mock = started[2]
            save_config_mock.assert_not_called()
        finally:
            for p in reversed(patches):
                p.stop()

    def test_startup_save_runs_when_env_imports_values(self):
        def fake_import(cfg):
            cfg.stt_api_key = "imported"
            return cfg

        patches = self._startup_patches(fake_import)
        started = [p.start() for p in patches]
        try:
            _TrayApp(startup_mode=True)
            save_config_mock = started[2]
            save_config_mock.assert_called_once()
        finally:
            for p in reversed(patches):
                p.stop()

    def test_startup_save_runs_when_plaintext_secrets_linger(self):
        patches = self._startup_patches(lambda cfg: cfg)
        patches[-1] = patch("src.main.has_plaintext_secrets", return_value=True)
        started = [p.start() for p in patches]
        try:
            _TrayApp(startup_mode=True)
            save_config_mock = started[2]
            save_config_mock.assert_called_once()
        finally:
            for p in reversed(patches):
                p.stop()

    def test_disable_while_recording_discards_audio(self):
        from src.icons import TrayState

        tray_app = make_tray_app()
        tray_app._recording = True
        tray_app._recorder = Mock()
        states = []
        tray_app._apply_state = lambda s: states.append(s)
        finalized = []
        tray_app._finalize_recording = lambda: finalized.append(True)

        tray_app._toggle_enabled(False)

        self.assertFalse(tray_app._recording)
        tray_app._recorder.stop.assert_called_once()
        self.assertEqual(finalized, [])
        self.assertEqual(states, [TrayState.IDLE])

    def test_disable_while_processing_cancels_worker(self):
        import threading

        tray_app = make_tray_app()
        tray_app._recording = False
        tray_app._worker = Mock()
        tray_app._cancel_event = threading.Event()

        tray_app._toggle_enabled(False)

        self.assertTrue(tray_app._cancel_event.is_set())

    def test_choice_submenu_uses_widget_actions(self):
        from PySide6.QtWidgets import QWidgetAction

        tray_app = make_tray_app()
        tray_app._add_choice_submenu(
            "Record Mode",
            [("hold", "Hold to talk"), ("toggle", "Toggle")],
            "hold",
            lambda key, rebuild_menu=True: None,
        )

        submenu = tray_app._menu.actions()[0].menu()
        self.assertTrue(all(isinstance(a, QWidgetAction) for a in submenu.actions()))

    def test_radio_selection_skips_menu_rebuild(self):
        tray_app = make_tray_app()
        called = []

        tray_app._add_choice_submenu(
            "Record Mode",
            [("hold", "Hold to talk"), ("toggle", "Toggle")],
            "hold",
            lambda key, rebuild_menu=True: called.append((key, rebuild_menu)),
        )

        submenu = tray_app._menu.actions()[0].menu()
        second_action = submenu.actions()[1]
        radio = second_action.defaultWidget()

        radio.setChecked(True)

        self.assertEqual(called, [("toggle", False)])

    def test_persistent_checkbox_emits_toggled(self):
        tray_app = make_tray_app()
        called = []

        checkbox = tray_app._add_persistent_checkbox(
            "AI Rewrite",
            False,
            lambda checked: called.append(checked),
        )

        checkbox.setChecked(True)

        self.assertEqual(called, [True])

    def test_set_post_key_rebuilds_by_default_but_can_skip(self):
        from src.config import AppConfig

        tray_app = make_tray_app()
        tray_app._config = AppConfig()

        rebuilds = []
        tray_app._rebuild_menu = lambda: rebuilds.append("rebuilt")

        with patch("src.main.save_config"):
            tray_app._set_post_key("enter")
            self.assertEqual(rebuilds, ["rebuilt"])

            rebuilds.clear()
            tray_app._set_post_key("tab", rebuild_menu=False)
            self.assertEqual(rebuilds, [])

    def test_set_hotkey_rebuilds_by_default_but_can_skip(self):
        from src.config import AppConfig

        tray_app = make_tray_app()
        tray_app._config = AppConfig()

        rebuilds = []
        restarts = []
        tray_app._rebuild_menu = lambda: rebuilds.append("rebuilt")
        tray_app._restart_hotkey = lambda: restarts.append("restarted")

        with patch("src.main.save_config"):
            tray_app._set_hotkey("ctrl+alt+key:0x20")
            self.assertEqual(rebuilds, ["rebuilt"])
            self.assertEqual(restarts, ["restarted"])

            rebuilds.clear()
            restarts.clear()
            tray_app._set_hotkey("ctrl+shift+key:0x20", rebuild_menu=False)
            self.assertEqual(rebuilds, [])
            self.assertEqual(restarts, ["restarted"])

    def test_set_recording_mode_rebuilds_by_default_but_can_skip(self):
        from src.config import AppConfig

        tray_app = make_tray_app()
        tray_app._config = AppConfig()

        rebuilds = []
        restarts = []
        tray_app._rebuild_menu = lambda: rebuilds.append("rebuilt")
        tray_app._restart_hotkey = lambda: restarts.append("restarted")

        with patch("src.main.save_config"):
            tray_app._set_recording_mode("hold")
            self.assertEqual(rebuilds, ["rebuilt"])
            self.assertEqual(restarts, ["restarted"])

            rebuilds.clear()
            restarts.clear()
            tray_app._set_recording_mode("toggle", rebuild_menu=False)
            self.assertEqual(rebuilds, [])
            self.assertEqual(restarts, ["restarted"])


if __name__ == "__main__":
    unittest.main()
