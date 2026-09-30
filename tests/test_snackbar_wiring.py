import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from src.icons import TrayState
from src.main import _TrayApp
from src.audio import AudioDeviceIdentity, CaptureSnapshot
from src.config import AppConfig
from src.snackbar import RecordingSnackbar
from src.utils import AppError, ScreamerError


def _bare_app():
    """Construct a _TrayApp without running its real __init__/Qt build steps."""
    QApplication.instance() or QApplication([])
    app = _TrayApp.__new__(_TrayApp)
    QObject.__init__(app)
    app._tray = MagicMock()  # _apply_state calls setIcon/setToolTip
    app._snackbar = MagicMock()  # what we assert on
    return app


class SnackbarWiringTests(unittest.TestCase):
    def test_recording_state_shows_snackbar_with_content(self):
        app = _bare_app()
        app._apply_state(TrayState.RECORDING)
        app._snackbar.show_state.assert_called_once_with("Recording", (229, 57, 53))
        app._snackbar.hide_state.assert_not_called()

    def test_processing_state_shows_amber(self):
        app = _bare_app()
        app._apply_state(TrayState.PROCESSING)
        app._snackbar.show_state.assert_called_once_with("Processing", (255, 179, 0))

    def test_idle_state_hides_snackbar(self):
        app = _bare_app()
        app._apply_state(TrayState.IDLE)
        app._snackbar.hide_state.assert_called_once_with()
        app._snackbar.show_state.assert_not_called()

    def test_recording_timer_polls_actual_snapshot_and_never_runs_when_inactive(self):
        app = _bare_app()
        app._snackbar = RecordingSnackbar()
        self.addCleanup(app._snackbar.close)
        app._config = AppConfig()
        app._recording = False
        app._recording_timer = QTimer(app)
        app._level_timer = QTimer(app)
        app._level_timer.timeout.connect(app._poll_recording_level)
        capture = CaptureSnapshot(16383.5, True, None, AudioDeviceIdentity(2, "Actual USB mic"))
        with (
            patch("src.main.resolve_device", return_value=None),
            patch("src.main.AudioRecorder") as recorder,
        ):
            recorder.return_value.snapshot.return_value = capture
            recorder.return_value.stop.return_value = b""
            app._start_recording()
            self.assertTrue(app._level_timer.isActive())
            app._level_timer.start(0)
            QApplication.processEvents()
            self.assertEqual(app._snackbar.input_level(), 0.5)
            self.assertEqual(
                app._snackbar.input_status_text(), "Input level - System Default (Actual USB mic)"
            )
            app._hotkey_restart_pending = False
            app._finalize_recording()
            self.assertFalse(app._level_timer.isActive())
            recorder.return_value.snapshot.reset_mock()
            app._poll_recording_level()
            recorder.return_value.snapshot.assert_not_called()

    def test_fatal_capture_stops_polling_and_reports_once_without_processing(self):
        app = _bare_app()
        app._config = AppConfig(audio_device_id=2, audio_device_name="USB mic")
        app._recording_timer = QTimer(app)
        app._level_timer = QTimer(app)
        app._hotkey_restart_pending = False
        app._on_error = MagicMock()
        capture = CaptureSnapshot(
            1200.0, True, "Input stream stopped unexpectedly.", AudioDeviceIdentity(2, "USB mic")
        )
        with (
            patch("src.main.resolve_device", return_value=2),
            patch("src.main.AudioRecorder") as recorder,
            patch("src.main._WorkerThread") as worker,
        ):
            recorder.return_value.snapshot.return_value = capture
            recorder.return_value.stop.side_effect = ScreamerError(
                AppError.MIC_DISCONNECTED, capture.capture_error
            )
            app._start_recording()
            app._poll_recording_level()
            app._poll_recording_level()
            self.assertFalse(app._recording)
            self.assertFalse(app._level_timer.isActive())
            app._on_error.assert_called_once_with(AppError.MIC_DISCONNECTED, capture.capture_error)
            recorder.return_value.stop.assert_called_once()
            worker.assert_not_called()
