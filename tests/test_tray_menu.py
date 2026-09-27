import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMenu

from src.main import _TrayApp


def make_tray_app():
    QApplication.instance() or QApplication([])
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
