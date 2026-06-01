"""System tray application — composition root, state machine, worker lifecycle.

Entry point: ``python -m src.main``

No public exports. Nothing imports main.py.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal, QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from src.audio import AudioRecorder, resolve_device, list_devices
from src.config import (
    HOTKEY_OPTIONS,
    POST_KEY_OPTIONS,
    AppConfig,
    import_from_env,
    load_config,
    save_config,
    validate_config,
)
from src.hotkey import HotkeyListener, HotkeyMode
from src.icons import TrayState, get_icon_pixmap
from src.injector import type_text
from src.rewrite import rewrite
from src.settings_dialog import SettingsDialog
from src.stt import transcribe
from src.utils import AppError, PipelineResult, ScreamerError, SignalBridge

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker thread — runs the full pipeline off the Qt main thread.
# ---------------------------------------------------------------------------


class _WorkerThread(QThread):
    """Daemon thread: transcribe → rewrite → type.

    Communicates results back to the Qt main thread via explicit signals.
    Checks cancel_event before each blocking step.
    Carries transcription warnings through to the final result.
    """

    succeeded = Signal(object)  # PipelineResult
    failed = Signal(Exception)
    cancelled = Signal()

    def __init__(
        self,
        audio_wav: bytes,
        config: AppConfig,
        cancel_event: threading.Event,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._audio_wav = audio_wav
        self._config = config
        self._cancel = cancel_event

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                self.cancelled.emit()
                return

            stt_result = transcribe(self._audio_wav, self._config)
            all_warnings = list(stt_result.warnings)

            if self._cancel.is_set():
                self.cancelled.emit()
                return

            result = rewrite(stt_result.text, self._config)
            all_warnings.extend(result.warnings)

            if self._cancel.is_set():
                self.cancelled.emit()
                return

            post_key = self._config.post_type_key
            type_text(result.text, post_key if post_key != "none" else None)

            self.succeeded.emit(PipelineResult(text=result.text, warnings=all_warnings))
        except Exception as e:
            self.failed.emit(e)


# ---------------------------------------------------------------------------
# Tray application
# ---------------------------------------------------------------------------


class _TrayApp(QObject):
    """Owns tray icon, state machine, hotkey listener, and worker lifecycle."""

    def __init__(self) -> None:
        super().__init__()

        self._config = load_config()
        self._config = import_from_env(self._config)
        save_config(self._config)

        self._recorder = AudioRecorder()
        self._bridge = SignalBridge()
        self._cancel_event = threading.Event()
        self._worker: _WorkerThread | None = None
        self._settings_dlg: SettingsDialog | None = None
        self._recording = False
        self._enabled = True

        self._build_tray()
        self._build_hotkey()
        self._apply_state(TrayState.IDLE)

        # Auto-open settings if startup config is incomplete.
        if validate_config(self._config):
            log.info("Incomplete configuration; opening settings on startup")
            self._open_settings()

    # ------------------------------------------------------------------
    # Device / calibrate helpers (passed to SettingsDialog)
    # ------------------------------------------------------------------

    def _get_device_list(self) -> list[tuple[int, str]]:
        """Return list of (device_id, name) for the settings dialog."""
        try:
            return [(d.id, d.name) for d in list_devices()]
        except Exception as e:
            log.warning("Could not enumerate audio devices: %s", e)
            return []

    def _calibrate(self, device_id: int | None) -> float:
        """Run RMS calibration for the given device. Returns threshold."""
        recorder = AudioRecorder(device_id=device_id)
        return recorder.calibrate(2.0)

    # ------------------------------------------------------------------
    # Tray construction
    # ------------------------------------------------------------------

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon(get_icon_pixmap(TrayState.IDLE)))
        self._tray.setToolTip("Screamer — Idle")
        self._menu = QMenu()
        self._tray.setContextMenu(self._menu)
        self._rebuild_menu()
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _rebuild_menu(self) -> None:
        """Rebuild the context menu from current config."""
        self._menu.clear()
        c = self._config

        # Enabled toggle.
        self._act_enabled = self._menu.addAction("Enabled")
        self._act_enabled.setCheckable(True)
        self._act_enabled.setChecked(self._enabled)
        self._act_enabled.triggered.connect(self._toggle_enabled)

        self._menu.addSeparator()

        # Record Mode submenu.
        mode_menu = self._menu.addMenu("Record Mode")
        self._act_hold = mode_menu.addAction("Hold to talk")
        self._act_hold.setCheckable(True)
        self._act_hold.setChecked(c.recording_mode == "hold")
        self._act_hold.triggered.connect(lambda: self._set_recording_mode("hold"))

        self._act_toggle = mode_menu.addAction("Toggle")
        self._act_toggle.setCheckable(True)
        self._act_toggle.setChecked(c.recording_mode == "toggle")
        self._act_toggle.triggered.connect(lambda: self._set_recording_mode("toggle"))

        # Hotkey submenu.
        hotkey_menu = self._menu.addMenu("Hotkey")

        self._hotkey_actions: dict[str, Any] = {}
        for key, label in HOTKEY_OPTIONS:
            act = hotkey_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == c.hotkey)
            act.triggered.connect(lambda checked, k=key: self._set_hotkey(k))
            self._hotkey_actions[key] = act

        # Post-type Key submenu.
        post_menu = self._menu.addMenu("Post-type Key")

        self._post_actions: dict[str, Any] = {}
        for key, label in POST_KEY_OPTIONS:
            act = post_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == c.post_type_key)
            act.triggered.connect(lambda checked, k=key: self._set_post_key(k))
            self._post_actions[key] = act

        # AI Rewrite toggle.
        self._menu.addSeparator()
        self._act_rewrite = self._menu.addAction("AI Rewrite")
        self._act_rewrite.setCheckable(True)
        self._act_rewrite.setChecked(c.llm_enabled)
        self._act_rewrite.triggered.connect(self._toggle_rewrite)

        # Settings / Exit.
        self._menu.addSeparator()
        self._menu.addAction("Settings...", self._open_settings)
        self._menu.addAction("Exit", self._exit)

    # ------------------------------------------------------------------
    # Hotkey
    # ------------------------------------------------------------------

    def _build_hotkey(self) -> None:
        mode = HotkeyMode.TOGGLE if self._config.recording_mode == "toggle" else HotkeyMode.HOLD
        self._hotkey = HotkeyListener(self._config.hotkey, mode, self._bridge)
        self._bridge.hotkey_pressed.connect(self._on_hotkey_pressed)
        self._bridge.hotkey_released.connect(self._on_hotkey_released)
        self._bridge.error_occurred.connect(self._on_error)
        self._hotkey.start()

    def _restart_hotkey(self) -> None:
        self._hotkey.stop()
        mode = HotkeyMode.TOGGLE if self._config.recording_mode == "toggle" else HotkeyMode.HOLD
        self._hotkey = HotkeyListener(self._config.hotkey, mode, self._bridge)
        self._hotkey.start()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _apply_state(self, state: TrayState) -> None:
        self._tray.setIcon(QIcon(get_icon_pixmap(state)))
        labels = {
            TrayState.IDLE: "Idle",
            TrayState.RECORDING: "Recording...",
            TrayState.PROCESSING: "Processing...",
        }
        self._tray.setToolTip(f"Screamer — {labels[state]}")

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        """Begin a new recording session."""
        self._apply_state(TrayState.RECORDING)
        device_id = resolve_device(self._config.audio_device_id, self._config.audio_device_name)
        self._recorder = AudioRecorder(device_id=device_id)
        self._recorder.rms_threshold = self._config.rms_threshold
        try:
            self._recorder.start()
            self._recording = True
        except ScreamerError as e:
            self._recording = False
            self._on_error(e.code)

    def _finalize_recording(self) -> None:
        """Stop recording and start the processing worker."""
        self._recording = False
        self._apply_state(TrayState.PROCESSING)

        try:
            audio_wav = self._recorder.stop()
        except ScreamerError as e:
            self._on_error(e.code)
            self._apply_state(TrayState.IDLE)
            return

        if not audio_wav:
            self._apply_state(TrayState.IDLE)
            return

        self._cancel_event.clear()
        self._worker = _WorkerThread(audio_wav, self._config, self._cancel_event, self)
        self._worker.succeeded.connect(self._on_worker_succeeded)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.cancelled.connect(self._on_worker_cancelled)
        self._worker.start()

    # ------------------------------------------------------------------
    # Hotkey callbacks (called from hotkey thread via SignalBridge → Qt main)
    # ------------------------------------------------------------------

    def _on_hotkey_pressed(self) -> None:
        if not self._enabled:
            return

        if self._worker is not None:
            return  # Already processing; ignore.

        if self._recording:
            # Toggle mode: second press → finalize and process.
            self._finalize_recording()
        else:
            self._start_recording()

    def _on_hotkey_released(self) -> None:
        # Hold mode: release during recording → finalize and process.
        if self._recording:
            self._finalize_recording()

    # ------------------------------------------------------------------
    # Worker result
    # ------------------------------------------------------------------

    def _on_worker_succeeded(self, result: PipelineResult) -> None:
        self._worker = None
        for warning in result.warnings:
            self._on_error(warning)
        self._apply_state(TrayState.IDLE)

    def _on_worker_failed(self, error: Exception) -> None:
        self._worker = None
        if isinstance(error, ScreamerError):
            self._on_error(error.code, error.detail)
        else:
            self._on_error(AppError.STT_FAILED, str(error))
        self._apply_state(TrayState.IDLE)

    def _on_worker_cancelled(self) -> None:
        self._worker = None
        self._apply_state(TrayState.IDLE)

    # ------------------------------------------------------------------
    # Error → balloon
    # ------------------------------------------------------------------

    def _on_error(self, code: AppError, detail: str | None = None) -> None:
        msg = code.value
        if detail:
            msg = f"{msg}\n{detail}"
        log.error("AppError: %s — %s", code.name, detail or "")
        self._tray.showMessage("Screamer", msg, QSystemTrayIcon.MessageIcon.Warning, 5000)

    # ------------------------------------------------------------------
    # Tray actions
    # ------------------------------------------------------------------

    def _toggle_enabled(self, checked: bool) -> None:
        self._enabled = checked
        if not checked and self._recording:
            self._finalize_recording()
        log.info("Screamer %s", "enabled" if checked else "disabled")

    def _set_recording_mode(self, mode: str) -> None:
        self._config.recording_mode = mode
        save_config(self._config)
        self._restart_hotkey()
        self._rebuild_menu()
        log.info("Recording mode set to %s", mode)

    def _set_hotkey(self, key: str) -> None:
        self._config.hotkey = key
        save_config(self._config)
        self._restart_hotkey()
        self._rebuild_menu()
        log.info("Hotkey set to %s", key)

    def _set_post_key(self, key: str) -> None:
        self._config.post_type_key = key
        save_config(self._config)
        self._rebuild_menu()
        log.info("Post-type key set to %s", key)

    def _toggle_rewrite(self, checked: bool) -> None:
        self._config.llm_enabled = checked
        save_config(self._config)
        log.info("AI rewrite %s", "enabled" if checked else "disabled")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_settings()

    def _open_settings(self) -> None:
        if self._settings_dlg is not None:
            return  # Already open.

        devices = self._get_device_list()
        dlg = SettingsDialog(
            self._config,
            devices=devices,
            calibrate_fn=self._calibrate,
        )
        self._settings_dlg = dlg
        result = dlg.exec()
        if result == SettingsDialog.DialogCode.Accepted:
            save_config(dlg.get_config())
        self._settings_dlg = None

        # Always reload from disk — Apply may have written new values,
        # and the user may have changed fields before Cancel.
        self._config = load_config()
        self._restart_hotkey()
        self._rebuild_menu()

        if result == SettingsDialog.DialogCode.Accepted:
            log.info("Settings updated from dialog")
        else:
            log.info("Settings dialog closed (cancelled); reloaded from disk")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _exit(self) -> None:
        log.info("Exit requested")

        # 1. Stop hotkey listener (prevents new recordings).
        self._hotkey.stop()

        # 2. Cancel worker if running.
        if self._worker is not None:
            self._cancel_event.set()
            self._worker.wait(5000)
            if self._worker.isRunning():
                log.warning("Worker did not stop within 5s; terminating")
                self._worker.terminate()
            self._worker = None

        # 3. Stop audio if recording.
        try:
            if self._recorder.is_recording:
                self._recorder.stop()
        except Exception:
            pass

        # 4. Save settings.
        save_config(self._config)

        # 5. Quit Qt.
        self._tray.hide()
        QApplication.instance().quit()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    from src.config import setup_logging

    setup_logging()

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    tray_app = _TrayApp()
    log.info("Screamer started")

    app.exec()


if __name__ == "__main__":
    main()
