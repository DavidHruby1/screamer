import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QLockFile


ENTRY_CHECK = """
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import src.main as main
main.APP_DIR = sys.argv[1]
app = Mock()
events = []
def start(*, startup_mode):
    assert Path(main.APP_DIR, 'screamer.lock').exists()
    events.append('started')
with patch.object(main, 'QApplication', return_value=app), \
     patch.object(main, 'QMessageBox'), \
     patch('src.config.setup_logging', side_effect=lambda: events.append('logging')), \
     patch.object(main, '_TrayApp', side_effect=start), \
     patch.object(main.http_client, 'close'):
    main.main(['--startup'])
print(','.join(events) or 'blocked-before-config')
"""


class SingleInstanceTests(unittest.TestCase):
    def run_child(self, script, directory):
        return subprocess.run(
            [sys.executable, "-c", script, directory],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )

    def test_second_process_cannot_load_settings_or_install_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = QLockFile(str(Path(directory, "screamer.lock")))
            lock.setStaleLockTime(0)
            self.assertTrue(lock.tryLock(0))
            try:
                blocked = self.run_child(ENTRY_CHECK, directory)
                self.assertEqual(blocked.stdout.strip(), "blocked-before-config")
                self.assertTrue(Path(directory, "screamer.lock").exists())
            finally:
                lock.unlock()
            started = self.run_child(ENTRY_CHECK, directory)
            self.assertEqual(started.stdout.strip(), "logging,started")
            self.assertFalse(Path(directory, "screamer.lock").exists())

    def test_lock_from_dead_process_can_be_recovered_without_expiring_live_lock(self):
        owner = """
import os, sys
from pathlib import Path
from PySide6.QtCore import QLockFile
lock = QLockFile(str(Path(sys.argv[1], 'screamer.lock')))
lock.setStaleLockTime(0)
assert lock.tryLock(0)
os._exit(0)
"""
        with tempfile.TemporaryDirectory() as directory:
            self.run_child(owner, directory)
            self.assertTrue(Path(directory, "screamer.lock").exists())
            started = self.run_child(ENTRY_CHECK, directory)
            self.assertEqual(started.stdout.strip(), "logging,started")
            self.assertFalse(Path(directory, "screamer.lock").exists())

    def test_external_qt_quit_waits_for_worker_and_commits_retained_raw_before_unlock(self):
        script = """
import base64, sys, threading
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QMenu, QApplication
import src.main as main
from src.config import AppConfig
from src.results import HistoryStore
from src.utils import PipelineResult
main.APP_DIR = sys.argv[1]
store = HistoryStore(Path(main.APP_DIR, 'history.enc'))
cfg = AppConfig(history_enabled=True, output_mode='copy')
started = threading.Event()
release = threading.Event()
trays = []
real_tray = main._TrayApp
def build_tray(self):
    self._tray = Mock()
    self._menu = QMenu()
def build_hotkey(self):
    self._hotkey = Mock()
def transcribe(_audio, _config):
    started.set()
    assert release.wait(5)
    return PipelineResult('raw completed after Qt quit')
def create(*, startup_mode):
    tray = real_tray(startup_mode=startup_mode)
    trays.append(tray)
    session = main._Session(cfg, None, 'external-quit', '2026-09-30T12:00:00+00:00')
    tray._start_worker(b'known WAV', session)
    assert started.wait(5)
    threading.Timer(0.05, release.set).start()
    QTimer.singleShot(0, QApplication.instance().quit)
    return tray
with patch.object(main, 'load_config', return_value=cfg), \
     patch.object(main, 'import_from_env', side_effect=lambda cfg: cfg), \
     patch.object(main, 'has_plaintext_secrets', return_value=False), \
     patch.object(main, 'save_config'), \
     patch('src.config.setup_logging'), \
     patch('src.config.protect_bytes', side_effect=base64.b64encode), \
     patch('src.config.unprotect_bytes', side_effect=base64.b64decode), \
     patch.object(main, 'HistoryStore', return_value=store), \
     patch.object(main, 'AudioRecorder'), \
     patch.object(real_tray, '_build_tray', build_tray), \
     patch.object(real_tray, '_build_hotkey', build_hotkey), \
     patch.object(real_tray, '_apply_state'), \
     patch.object(main, 'transcribe', side_effect=transcribe), \
     patch.object(main, '_TrayApp', side_effect=create):
    main.main(['--startup'])
    assert trays[0]._worker is None
    assert trays[0]._exiting
    entry = store.load()[0]
    assert entry.raw_text == 'raw completed after Qt quit'
    assert entry.last_delivery.reason == 'suppressed'
    assert not entry.last_delivery.copied
lock = QLockFile(str(Path(main.APP_DIR, 'screamer.lock')))
assert lock.tryLock(0)
lock.unlock()
print('safe-shutdown-and-checkpoint')
"""
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_child(script, directory)
            self.assertEqual(result.stdout.strip(), "safe-shutdown-and-checkpoint")


if __name__ == "__main__":
    unittest.main()
