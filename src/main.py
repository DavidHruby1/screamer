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
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QMenu,
    QRadioButton,
    QSystemTrayIcon,
    QWidgetAction,
)

from src.audio import AudioRecorder, default_input_device_id, list_devices, resolve_device
from src.config import (
    HOTKEY_OPTIONS,
    POST_KEY_OPTIONS,
    AppConfig,
    Hotkey,
    import_from_env,
    load_config,
    save_config,
    validate_config,
)
from src import http_client
from src.hotkey import HotkeyListener, HotkeyMode
from src.icons import TrayState, get_icon_pixmap
from src.injector import type_text
from src.rewrite import rewrite
from src.settings_dialog import SettingsDialog
from src.snackbar import RecordingSnackbar, snackbar_content_for
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

    def __init__(self, startup_mode: bool = False) -> None:
        super().__init__()

        self._config = load_config()
        self._config = import_from_env(self._config)
        save_config(self._config)

        self._recorder = AudioRecorder()
        self._bridge = SignalBridge()
        self._cancel_event = threading.Event()
        self._worker: _WorkerThread | None = None
        self._settings_dlg: SettingsDialog | None = None
        self._settings_hotkey_capture_paused = False
        self._recording = False
        self._enabled = True

        self._snackbar = RecordingSnackbar()

        self._build_tray()
        self._build_hotkey()
        self._apply_state(TrayState.IDLE)

        # Auto-open settings on manual launch only if startup config is incomplete.
        if not startup_mode and validate_config(self._config):
            log.info("Incomplete configuration; opening settings on startup")
            self._open_settings()

    # ------------------------------------------------------------------
    # Device / calibrate helpers (passed to SettingsDialog)
    # ------------------------------------------------------------------

    def _get_device_list(self) -> list[tuple[int, str]]:
        """Return list of (device_id, name) for the settings dialog."""
        try:
            default_id = default_input_device_id()
            devices = []
            for d in list_devices():
                name = d.name
                if d.id == default_id:
                    name = f"{name} (Default input)"
                devices.append((d.id, name))
            return devices
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

    def _add_choice_submenu(
        self,
        title: str,
        options: list[tuple[str, str]],
        current: str,
        on_select: Any,
    ) -> None:
        submenu = QMenu(title, self._menu)
        self._menu.addMenu(submenu)
        group = QButtonGroup(submenu)
        group.setExclusive(True)

        for key, label in options:
            radio = QRadioButton(label)
            radio.setChecked(key == current)

            action = QWidgetAction(submenu)
            action.setDefaultWidget(radio)
            submenu.addAction(action)

            group.addButton(radio)

            radio.toggled.connect(
                lambda checked, k=key: checked and on_select(k, rebuild_menu=False)
            )

    def _add_persistent_checkbox(
        self,
        label: str,
        checked: bool,
        on_changed: Any,
    ) -> QCheckBox:
        # Use QWidgetAction instead of checkable QAction so clicking the control
        # does not trigger QMenu's default close-on-action behavior.
        checkbox = QCheckBox(label)
        checkbox.setChecked(checked)

        action = QWidgetAction(self._menu)
        action.setDefaultWidget(checkbox)
        self._menu.addAction(action)

        checkbox.toggled.connect(on_changed)
        return checkbox

    def _rebuild_menu(self) -> None:
        """Rebuild the context menu from current config."""
        self._menu.clear()
        c = self._config

        self._add_persistent_checkbox("Enabled", self._enabled, self._toggle_enabled)

        self._menu.addSeparator()

        self._add_choice_submenu(
            "Record Mode",
            [("hold", "Hold to talk"), ("toggle", "Toggle")],
            c.recording_mode,
            self._set_recording_mode,
        )
        self._add_choice_submenu("Hotkey", HOTKEY_OPTIONS, c.hotkey, self._set_hotkey)
        self._add_choice_submenu("Post-type Key", POST_KEY_OPTIONS, c.post_type_key, self._set_post_key)

        self._menu.addSeparator()
        self._add_persistent_checkbox("AI Rewrite", c.llm_enabled, self._toggle_rewrite)

        # Settings / Exit.
        self._menu.addSeparator()
        self._menu.addAction("Settings...", self._open_settings)
        self._menu.addAction("Exit", self._exit)

    # ------------------------------------------------------------------
    # Hotkey
    # ------------------------------------------------------------------

    def _make_listener(self) -> None:
        """Create and start a HotkeyListener from current config, storing it on self."""
        mode = HotkeyMode.TOGGLE if self._config.recording_mode == "toggle" else HotkeyMode.HOLD
        hotkey = Hotkey.parse(self._config.hotkey) or Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        self._hotkey = HotkeyListener(hotkey, mode, self._bridge)
        self._hotkey.start()

    def _build_hotkey(self) -> None:
        self._bridge.hotkey_pressed.connect(self._on_hotkey_pressed)
        self._bridge.hotkey_released.connect(self._on_hotkey_released)
        self._bridge.error_occurred.connect(self._on_error)
        self._make_listener()

    def _restart_hotkey(self) -> None:
        self._hotkey.stop()
        self._make_listener()

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

        content = snackbar_content_for(state.value)
        if content is None:
            self._snackbar.hide_state()
        else:
            self._snackbar.show_state(*content)

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
        # Result slots run first (queued in emission order), then the QThread
        # object frees itself — otherwise one worker leaks per dictation.
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    # ------------------------------------------------------------------
    # Hotkey callbacks (called from hotkey thread via SignalBridge → Qt main)
    # ------------------------------------------------------------------

    def _on_hotkey_pressed(self) -> None:
        if not self._enabled:
            return

        if self._is_hotkey_capture_active() and not self._recording:
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
        if self._is_hotkey_capture_active() and not self._recording:
            return
        if self._recording:
            self._finalize_recording()

    def _is_hotkey_capture_active(self) -> bool:
        return self._settings_dlg is not None and self._settings_dlg.is_hotkey_capture_active()

    def _on_settings_hotkey_capture_active_changed(self, active: bool) -> None:
        if active:
            if self._settings_hotkey_capture_paused or self._recording:
                return
            self._hotkey.stop()
            self._settings_hotkey_capture_paused = True
            return

        if not self._settings_hotkey_capture_paused:
            return
        self._make_listener()
        self._settings_hotkey_capture_paused = False

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

    def _set_recording_mode(self, mode: str, rebuild_menu: bool = True) -> None:
        self._config.recording_mode = mode
        save_config(self._config)
        self._restart_hotkey()
        if rebuild_menu:
            self._rebuild_menu()
        log.info("Recording mode set to %s", mode)

    def _set_hotkey(self, key: str, rebuild_menu: bool = True) -> None:
        self._config.hotkey = key
        save_config(self._config)
        self._restart_hotkey()
        if rebuild_menu:
            self._rebuild_menu()
        log.info("Hotkey set to %s", key)

    def _set_post_key(self, key: str, rebuild_menu: bool = True) -> None:
        self._config.post_type_key = key
        save_config(self._config)
        if rebuild_menu:
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
        dlg.hotkey_capture_active_changed.connect(self._on_settings_hotkey_capture_active_changed)
        dlg.applied.connect(self._sync_settings_from_disk)
        result = SettingsDialog.DialogCode.Rejected
        try:
            result = dlg.exec()
            if result == SettingsDialog.DialogCode.Accepted:
                save_config(dlg.get_config())
        finally:
            self._settings_dlg = None

            # Always reload from disk — Apply may have written new values,
            # and the user may have changed fields before Cancel.
            self._sync_settings_from_disk()

        if result == SettingsDialog.DialogCode.Accepted:
            log.info("Settings updated from dialog")
        else:
            log.info("Settings dialog closed (cancelled); reloaded from disk")

    def _sync_settings_from_disk(self) -> None:
        old_hotkey = self._config.hotkey
        old_mode = self._config.recording_mode
        self._config = load_config()
        if self._config.hotkey != old_hotkey or self._config.recording_mode != old_mode:
            self._restart_hotkey()
        self._rebuild_menu()

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
        self._snackbar.hide_state()
        QApplication.instance().quit()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    import argparse
    import sys

    from src.config import setup_logging

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--startup", action="store_true")
    args, _unknown = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    setup_logging()

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    tray_app = _TrayApp(startup_mode=args.startup)
    log.info("Screamer started")

    try:
        app.exec()
    finally:
        try:
            http_client.close()
        except Exception:
            log.exception("HTTP client shutdown failed")


if __name__ == "__main__":
    main()
