import os
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMenu

from src.main import _TrayApp


def make_tray_app():
    QApplication.instance() or QApplication([])
    tray_app = _TrayApp.__new__(_TrayApp)
    QObject.__init__(tray_app)
    tray_app._menu = QMenu()
    tray_app._settings_dlg = None
    tray_app._hotkey_restart_pending = False
    tray_app._exiting = False
    tray_app._recording_timer = QTimer(tray_app)
    return tray_app


class TrayMenuTests(unittest.TestCase):
    def test_five_minute_limit_processes_recorded_audio_once(self):
        with ExitStack() as stack:
            for patcher in self._startup_patches(lambda cfg: cfg):
                stack.enter_context(patcher)
            stack.enter_context(patch("src.main.resolve_device", return_value=None))
            worker = stack.enter_context(patch("src.main._WorkerThread"))
            tray = _TrayApp(startup_mode=True)
            tray._on_hotkey_pressed()
            tray._recorder.stop.return_value = b"recorded audio"

            self.assertTrue(tray._recording_timer.isActive())
            self.assertEqual(tray._recording_timer.interval(), 300_000)
            self.assertTrue(tray._recording_timer.isSingleShot())
            tray._recording_timer.start(0)
            QApplication.processEvents()

            self.assertFalse(tray._recording)
            self.assertFalse(tray._recording_timer.isActive())
            tray._recorder.stop.assert_called_once()
            worker.assert_called_once_with(
                b"recorded audio", tray._config, tray._cancel_event, tray
            )
            worker.return_value.start.assert_called_once()
            tray._on_hotkey_released()
            tray._recorder.stop.assert_called_once()

    def test_recording_limit_is_cancelled_on_manual_stop_disable_and_exit(self):
        for action in ("release", "disable", "exit"):
            with self.subTest(action=action), ExitStack() as stack:
                for patcher in self._startup_patches(lambda cfg: cfg):
                    stack.enter_context(patcher)
                stack.enter_context(patch("src.main.resolve_device", return_value=None))
                worker = stack.enter_context(patch("src.main._WorkerThread"))
                tray = _TrayApp(startup_mode=True)
                tray._hotkey = Mock()
                tray._finish_exit = Mock()
                tray._on_hotkey_pressed()
                tray._recorder.stop.return_value = b""
                tray._recording_timer.start(0)

                if action == "release":
                    tray._on_hotkey_released()
                elif action == "disable":
                    tray._toggle_enabled(False)
                else:
                    tray._exit()
                QApplication.processEvents()

                self.assertFalse(tray._recording_timer.isActive())
                tray._recorder.stop.assert_called_once()
                worker.assert_not_called()

    def test_capture_finishing_during_exit_does_not_restart_listener(self):
        tray = make_tray_app()
        tray._exiting = True
        tray._settings_hotkey_capture_paused = True
        tray._make_listener = Mock()
        tray._on_settings_hotkey_capture_active_changed(False)
        tray._make_listener.assert_not_called()
        self.assertFalse(tray._settings_hotkey_capture_paused)

    def test_opening_settings_does_not_interrupt_dictation(self):
        from src.utils import AppError

        tray = make_tray_app()
        tray._recording = True
        tray._on_error = Mock()
        with patch("src.main.SettingsDialog") as dialog:
            tray._open_settings()
        dialog.assert_not_called()
        self.assertTrue(tray._recording)
        tray._on_error.assert_called_once_with(AppError.DICTATION_ACTIVE)

    def test_exit_during_initial_settings_stops_outer_event_loop(self):
        from src.config import AppConfig

        app = QApplication.instance() or QApplication([])
        previous_quit_policy = app.quitOnLastWindowClosed()
        app.setQuitOnLastWindowClosed(False)
        self.addCleanup(app.setQuitOnLastWindowClosed, previous_quit_policy)
        with (
            patch("src.main.load_config", return_value=AppConfig()),
            patch("src.main.import_from_env", side_effect=lambda cfg: cfg),
            patch("src.main.has_plaintext_secrets", return_value=False),
            patch("src.main.save_config"),
            patch.object(
                _TrayApp,
                "_make_listener",
                autospec=True,
                side_effect=lambda self: setattr(self, "_hotkey", Mock()),
            ),
            patch.object(_TrayApp, "_get_device_list", return_value=[]),
        ):
            tray = _TrayApp.__new__(_TrayApp)
            timed_out = []
            dialog_was_open = []

            def request_exit():
                dialog_was_open.append(tray._settings_dlg is not None)
                exit_action = next(
                    action for action in tray._menu.actions() if action.text() == "Exit"
                )
                exit_action.trigger()

            # Also fires when the old constructor wrongly enters dlg.exec().
            QTimer.singleShot(0, lambda: QTimer.singleShot(0, request_exit))

            def timeout():
                timed_out.append(True)
                app.quit()

            safety_timer = QTimer()
            safety_timer.setSingleShot(True)
            safety_timer.timeout.connect(timeout)
            safety_timer.start(1000)
            _TrayApp.__init__(tray)
            app.exec()
            safety_timer.stop()
            self.assertEqual(dialog_was_open, [True])
            self.assertFalse(timed_out)
            self.assertTrue(tray._exiting)
            self.assertFalse(tray._tray.isVisible())

    def test_recording_start_failure_returns_to_idle(self):
        from src.config import AppConfig
        from src.icons import TrayState

        tray = make_tray_app()
        tray._config = AppConfig()
        tray._recording = False
        tray._apply_state = Mock()
        tray._on_error = Mock()
        with patch("src.main.resolve_device", side_effect=OSError("busy")):
            tray._start_recording()
        self.assertFalse(tray._recording)
        tray._apply_state.assert_called_once_with(TrayState.IDLE)

    def test_exit_still_quits_when_settings_save_fails(self):
        tray = make_tray_app()
        tray._worker = None
        tray._tray = Mock()
        tray._snackbar = Mock()
        tray._config = object()
        app = QApplication.instance()
        with (
            patch("src.main.save_config", side_effect=OSError("read-only disk")),
            patch.object(app, "quit") as quit_app,
        ):
            tray._finish_exit()
        tray._tray.hide.assert_called_once()
        quit_app.assert_called_once()

    def test_result_does_not_type_into_own_settings_dialog(self):
        import threading
        from src.config import AppConfig
        from src.utils import PipelineResult

        tray = make_tray_app()
        tray._settings_dlg = Mock()
        tray._cancel_event = threading.Event()
        tray._config = AppConfig()
        tray._enabled = True
        tray._exiting = False
        tray._apply_state = Mock()
        with patch("src.main.type_text") as type_text:
            tray._on_worker_succeeded(PipelineResult(text="secret"))
        type_text.assert_not_called()

    def test_hotkey_restart_waits_for_active_hold_recording_release(self):
        tray = make_tray_app()
        tray._recording = True
        tray._recorder = Mock()
        tray._apply_state = Mock()
        tray._hotkey = Mock()
        tray._make_listener = Mock()
        tray._restart_hotkey()
        tray._recorder.stop.assert_not_called()
        tray._hotkey.stop.assert_not_called()
        self.assertTrue(tray._recording)
        tray._recorder.stop.return_value = b""
        tray._on_hotkey_released()
        tray._recorder.stop.assert_called_once()
        tray._hotkey.stop.assert_called_once()
        tray._make_listener.assert_called_once()
        self.assertFalse(tray._recording)
        self.assertFalse(tray._hotkey_restart_pending)

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
            QApplication.processEvents()
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

    def test_result_queued_before_disable_does_not_type(self):
        import threading

        from src.config import AppConfig
        from src.utils import PipelineResult

        tray_app = make_tray_app()
        tray_app._recording = False
        tray_app._worker = Mock()
        tray_app._cancel_event = threading.Event()
        tray_app._config = AppConfig()
        tray_app._exiting = False
        tray_app._apply_state = Mock()

        tray_app._toggle_enabled(False)
        with patch("src.main.type_text") as type_text:
            tray_app._on_worker_succeeded(PipelineResult(text="must not type"))
        type_text.assert_not_called()

    def test_successful_result_types_on_main_thread(self):
        import threading

        from src.config import AppConfig
        from src.utils import PipelineResult

        tray_app = make_tray_app()
        tray_app._cancel_event = threading.Event()
        tray_app._config = AppConfig(post_type_key="enter")
        tray_app._enabled = True
        tray_app._exiting = False
        tray_app._apply_state = Mock()

        with patch("src.main.type_text") as type_text:
            tray_app._on_worker_succeeded(PipelineResult(text="typed"))
        type_text.assert_called_once_with("typed", "enter")

    def test_discard_reports_microphone_stop_error(self):
        from src.utils import AppError, ScreamerError

        tray_app = make_tray_app()
        tray_app._recording = True
        tray_app._recorder = Mock()
        tray_app._recorder.stop.side_effect = ScreamerError(AppError.MIC_DISCONNECTED)
        tray_app._on_error = Mock()
        tray_app._apply_state = Mock()

        tray_app._cancel_recording()

        tray_app._on_error.assert_called_once_with(AppError.MIC_DISCONNECTED, None)
        self.assertFalse(tray_app._recording)

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
