import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from src.icons import TrayState
from src.main import _TrayApp


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
