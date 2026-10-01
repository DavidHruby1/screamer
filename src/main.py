"""System tray application — composition root, state machine, worker lifecycle.

Entry point: ``python -m src.main``

No public exports. Nothing imports main.py.
"""

from __future__ import annotations

import copy
import logging
import os
import platform
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, QThread, QTimer, Qt, QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QMenu,
    QMessageBox,
    QRadioButton,
    QSystemTrayIcon,
    QWidgetAction,
)

from src.audio import AudioRecorder, default_input_device_id, list_devices, resolve_device
from src.config import (
    DEFAULT_LLM_SYSTEM_PROMPT,
    HOTKEY_OPTIONS,
    POST_KEY_OPTIONS,
    AppConfig,
    Hotkey,
    builtin_rewrite_profiles,
    decode_rewrite_catalog,
    has_plaintext_secrets,
    import_from_env,
    language_choices,
    load_config,
    resolve_rewrite_profile,
    save_config,
    validate_config,
)
from src import http_client
from src.hotkey import HotkeyListener, HotkeyMode
from src.icons import TrayState, get_icon_pixmap
from src.injector import WindowIdentity, get_foreground_target, type_text
from src.results import DictationRecord, DeliveryAttempt, HistoryStore, utc_now
from src.recovery_dialog import RecoveryDialog
from src.rewrite import rewrite
from src.settings_dialog import SettingsDialog
from src.snackbar import RecordingSnackbar, snackbar_content_for
from src.stt import transcribe
from src.utils import APP_DIR, AppError, ScreamerError, SignalBridge

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker thread — computes pipeline output off the Qt main thread.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Session:
    config: AppConfig
    target: WindowIdentity | None
    id: str
    started_at: str
    profile_id: str | None = None


@dataclass(frozen=True)
class _Terminal:
    session_id: str
    raw: str | None
    rewritten: str | None
    rewrite_status: str
    warnings: tuple[AppError, ...]
    cancelled: bool = False
    error: Exception | None = None


class _WorkerThread(QThread):
    """Thread: transcribe → rewrite; main thread owns final injection.

    Communicates results back to the Qt main thread via explicit signals.
    Checks cancel_event before each blocking step.
    Carries transcription warnings through to the final result.
    """

    raw_ready = Signal(str, str, object)
    terminal = Signal(object)

    def __init__(
        self,
        audio_wav: bytes,
        config: AppConfig,
        cancel_event: threading.Event,
        parent: QObject | None = None,
        *,
        session_id: str,
        raw_text: str | None = None,
        stt_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self._audio_wav = audio_wav
        self._config = config
        self._cancel = cancel_event
        self._session_id = session_id
        self._raw_text = raw_text
        self._stt_only = stt_only
        self._terminal_payload: _Terminal | None = None

    @property
    def terminal_payload(self) -> _Terminal | None:
        """Read on main after wait(); does not depend on queued signal delivery."""
        return self._terminal_payload

    def _publish_terminal(self, payload: _Terminal) -> None:
        self._terminal_payload = payload
        self.terminal.emit(payload)

    def run(self) -> None:
        raw = self._raw_text
        warnings = ()
        status = "pending" if self._config.llm_enabled and not self._stt_only else "not_requested"
        rewritten = None
        try:
            if self._cancel.is_set():
                self._publish_terminal(
                    _Terminal(self._session_id, raw, None, "cancelled", warnings, True)
                )
                return
            if raw is None:
                stt_result = transcribe(self._audio_wav, self._config)
                raw = stt_result.text
                if not raw.strip():
                    raise ScreamerError(AppError.STT_FAILED)
                warnings = tuple(stt_result.warnings)
                self._audio_wav = b""
                self.raw_ready.emit(self._session_id, raw, warnings)
            if self._cancel.is_set():
                self._publish_terminal(
                    _Terminal(self._session_id, raw, None, "cancelled", warnings, True)
                )
                return
            if self._config.llm_enabled and not self._stt_only:
                result = rewrite(raw, self._config)
                warnings += tuple(result.warnings)
                if not result.text.strip() or AppError.LLM_FAILED in result.warnings:
                    status = "failed"
                    if AppError.LLM_FAILED not in warnings:
                        warnings += (AppError.LLM_FAILED,)
                else:
                    status = "succeeded"
                    rewritten = result.text
            if self._cancel.is_set():
                self._publish_terminal(
                    _Terminal(self._session_id, raw, rewritten, "cancelled", warnings, True)
                )
                return
            self._publish_terminal(_Terminal(self._session_id, raw, rewritten, status, warnings))
        except Exception as e:
            if raw is not None:
                status = "failed"
                if AppError.LLM_FAILED not in warnings:
                    warnings += (AppError.LLM_FAILED,)
            self._publish_terminal(
                _Terminal(self._session_id, raw, None, status, warnings, error=e)
            )


# ---------------------------------------------------------------------------
# Tray application
# ---------------------------------------------------------------------------


class _TrayApp(QObject):
    """Owns tray icon, state machine, hotkey listener, and worker lifecycle."""

    def __init__(self, startup_mode: bool = False) -> None:
        super().__init__()

        self._config = load_config()
        imported = import_from_env(copy.deepcopy(self._config))
        if imported != self._config or has_plaintext_secrets():
            # Save when .env added values, or to purge plaintext secrets an
            # older version left in settings.ini (save_config removes them).
            self._config = imported
            save_config(self._config)

        self._recorder = AudioRecorder()
        self._bridge = SignalBridge()
        self._cancel_event = threading.Event()
        self._worker: _WorkerThread | None = None
        self._retired_worker: _WorkerThread | None = None
        self._session_config: AppConfig | None = None
        self._session: _Session | None = None
        self._terminal_handled = False
        self._thread_finished = False
        self._recovery_only = False
        self._rewrite_only = False
        self._stt_only = False
        self._pending_audio: tuple[bytes, _Session] | None = None
        self._latest: DictationRecord | None = None
        self._candidate: DictationRecord | None = None
        self._armed: tuple[DictationRecord, str] | None = None
        self._history = HistoryStore()
        self._committed: list[DictationRecord] = []
        self._committed_ids: set[str] = set()
        self._recovery_dlg: RecoveryDialog | None = None
        self._settings_dlg: SettingsDialog | None = None
        # Keep the most recently closed dialog alive for post-event-loop thread joins.
        self._last_settings_dlg: SettingsDialog | None = None
        self._settings_hotkey_capture_paused = False
        self._hotkey_restart_pending = False
        self._recording = False
        self._enabled = True
        self._exiting = False
        self._event_loop_stopped = False
        self._exit_complete = False

        self._recording_timer = QTimer(self)
        self._recording_timer.setSingleShot(True)
        self._recording_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._recording_timer.setInterval(5 * 60 * 1000)
        self._recording_timer.timeout.connect(self._finalize_recording)

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(100)
        self._level_timer.timeout.connect(self._poll_recording_level)

        self._snackbar = RecordingSnackbar()

        self._build_tray()
        self._load_history()
        self._build_hotkey()
        self._apply_state(TrayState.IDLE)

        # Auto-open settings on manual launch only if startup config is incomplete.
        if not startup_mode and validate_config(self._config):
            log.info("Incomplete configuration; opening settings on startup")
            QTimer.singleShot(0, self._open_settings)

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
    ) -> QMenu:
        submenu = QMenu(title, self._menu)
        submenu.setEnabled(self._settings_dlg is None)
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
        return submenu

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
        self._add_choice_submenu(
            "Language",
            language_choices(c.stt_language, c.stt_language_favorites),
            c.stt_language,
            self._set_language,
        )
        self._add_choice_submenu(
            "Post-type Key", POST_KEY_OPTIONS, c.post_type_key, self._set_post_key
        )

        self._menu.addSeparator()
        rewrite_checkbox = self._add_persistent_checkbox(
            "AI Rewrite" if c.llm_enabled else "AI Rewrite (off — Raw)",
            c.llm_enabled,
            self._toggle_rewrite,
        )
        rewrite_checkbox.setEnabled(self._settings_dlg is None)
        rewrite_checkbox.setToolTip(
            "Off always uses Raw without changing the selected profile. Raw never calls an LLM."
        )
        try:
            custom, _mappings = (
                decode_rewrite_catalog(c.rewrite_catalog)
                if c.rewrite_catalog is not None
                else ([], [])
            )
            resolve_rewrite_profile(c, None)  # Validate persisted selections, even when AI is off.
        except (TypeError, ValueError):
            submenu = self._add_choice_submenu(
                "Rewrite Profile — repair required",
                [],
                "",
                self._set_rewrite_profile,
            )
            submenu.setEnabled(False)
        else:
            options = [("__automatic__", "Automatic")]
            options.extend(
                (profile.id, profile.name) for profile in (*builtin_rewrite_profiles(), *custom)
            )
            current = (
                "__automatic__"
                if c.rewrite_selection_mode == "automatic"
                else c.rewrite_manual_profile_id
            )
            self._add_choice_submenu("Rewrite Profile", options, current, self._set_rewrite_profile)

        # Settings / Exit.
        self._menu.addSeparator()
        self._menu.addAction("Settings...", self._open_settings)
        self._menu.addAction("Recovery...", self._open_recovery)
        self._menu.addAction("Exit", self._exit)

    # ------------------------------------------------------------------
    # Hotkey
    # ------------------------------------------------------------------

    def _make_listener(self) -> None:
        """Create and start a HotkeyListener from current config, storing it on self."""
        mode = HotkeyMode.TOGGLE if self._config.recording_mode == "toggle" else HotkeyMode.HOLD
        hotkey = Hotkey.parse(self._config.hotkey) or Hotkey(
            frozenset({"ctrl", "alt"}), "key", 0x20
        )
        self._hotkey = HotkeyListener(
            hotkey,
            mode,
            self._bridge,
            recovery_hotkey=Hotkey.parse(self._config.recovery_hotkey),
        )
        self._hotkey.start()

    def _build_hotkey(self) -> None:
        self._bridge.hotkey_pressed.connect(self._on_hotkey_pressed)
        self._bridge.hotkey_released.connect(self._on_hotkey_released)
        self._bridge.error_occurred.connect(self._on_error)
        self._bridge.recovery_requested.connect(self._insert_armed)
        self._bridge.recovery_cancelled.connect(self._disarm)
        self._make_listener()

    def _restart_hotkey(self) -> None:
        if self._exiting:
            return
        if self._recording:
            # Keep the old binding alive until its release finishes this recording.
            self._hotkey_restart_pending = True
            return
        self._hotkey.stop()
        self._make_listener()
        self._hotkey_restart_pending = False

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

    @staticmethod
    def _apply_rewrite_profile(config: AppConfig, executable_path: str | None) -> str:
        """Resolve once and apply effective fields only to the supplied session snapshot."""
        profile = resolve_rewrite_profile(config, executable_path)
        if profile.kind == "raw":
            config.llm_enabled = False
        elif profile.kind == "clean":
            config.llm_system_prompt = DEFAULT_LLM_SYSTEM_PROMPT
        else:
            assert profile.prompt is not None  # Validated custom prompts may be empty.
            config.llm_system_prompt = profile.prompt
        return profile.id

    def _start_recording(self) -> None:
        """Begin a new recording session."""
        self._recording_timer.stop()
        self._level_timer.stop()
        try:
            # One snapshot for capture, network requests and final output. Capture the
            # foreground identity on Qt before any recording UI changes the focus.
            config = copy.deepcopy(self._config)
            target = get_foreground_target()
            profile_id = self._apply_rewrite_profile(
                config, target.executable_path if target else None
            )
            self._session_config = config
            self._session = _Session(config, target, uuid4().hex, utc_now(), profile_id)
        except Exception:
            self._recording = False
            self._session_config = None
            self._session = None
            self._apply_state(TrayState.IDLE)
            self._refresh_recovery()
            self._on_error(AppError.CONFIG_INVALID)
            return
        try:
            device_id = resolve_device(config.audio_device_id, config.audio_device_name)
            self._recorder = AudioRecorder(device_id=device_id)
            self._recorder.rms_threshold = config.rms_threshold
            self._recorder.start()
            self._pending_audio = None
            self._disarm()
            self._recording = True
            self._recording_timer.start()
            self._apply_state(TrayState.RECORDING)
            self._level_timer.start()
            self._refresh_recovery()
        except ScreamerError as e:
            self._recording = False
            self._session_config = None
            self._session = None
            self._apply_state(TrayState.IDLE)
            self._on_error(e.code, e.detail)
        except Exception as e:
            self._recording = False
            self._session_config = None
            self._session = None
            self._apply_state(TrayState.IDLE)
            self._on_error(AppError.MIC_UNAVAILABLE, str(e))

    def _poll_recording_level(self) -> None:
        if not self._recording:
            return
        capture = self._recorder.snapshot()
        if capture.capture_error is not None:
            # stop() reports the retained error once and discards incomplete audio.
            self._cancel_recording()
            return
        label = capture.device.name if capture.device is not None else "Input device unknown"
        assert self._session_config is not None
        if self._session_config.audio_device_id is None:
            label = f"System Default ({label})"
        self._snackbar.set_input_status(
            label, min(capture.level_rms / 32767.0, 1.0), capture.has_callback_data
        )

    def _finalize_recording(self) -> None:
        """Stop recording and start the processing worker."""
        self._recording_timer.stop()
        self._level_timer.stop()
        self._recording = False
        self._apply_state(TrayState.PROCESSING)

        try:
            audio_wav = self._recorder.stop()
        except ScreamerError as e:
            self._session_config = None
            self._session = None
            self._on_error(e.code, e.detail)
            self._apply_state(TrayState.IDLE)
            return
        finally:
            if self._hotkey_restart_pending:
                self._restart_hotkey()

        if not audio_wav:
            self._session_config = None
            self._session = None
            self._apply_state(TrayState.IDLE)
            return

        assert self._session is not None
        self._start_worker(audio_wav, self._session)

    def _cancel_recording(self) -> None:
        """Stop and discard the in-flight recording without processing it."""
        self._recording_timer.stop()
        self._level_timer.stop()
        self._recording = False
        self._session_config = None
        self._session = None
        try:
            self._recorder.stop()
        except ScreamerError as e:
            self._on_error(e.code, e.detail)
        if self._hotkey_restart_pending:
            self._restart_hotkey()
        self._apply_state(TrayState.IDLE)
        self._refresh_recovery()

    # ------------------------------------------------------------------
    # Hotkey callbacks (called from hotkey thread via SignalBridge → Qt main)
    # ------------------------------------------------------------------

    def _on_hotkey_pressed(self) -> None:
        if not self._enabled or self._exiting or self._settings_dlg is not None:
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
        if not self._exiting:
            self._make_listener()
        self._settings_hotkey_capture_paused = False

    # ------------------------------------------------------------------
    # Worker result
    # ------------------------------------------------------------------

    def _start_worker(
        self,
        audio: bytes,
        session: _Session,
        *,
        recovery_only: bool = False,
        raw_text: str | None = None,
        stt_only: bool = False,
    ) -> None:
        assert self._worker is None
        self._session = session
        self._session_config = session.config
        self._recovery_only = recovery_only
        self._rewrite_only = raw_text is not None
        self._stt_only = stt_only
        self._terminal_handled = self._thread_finished = False
        self._cancel_event.clear()
        worker = _WorkerThread(
            audio,
            session.config,
            self._cancel_event,
            self,
            session_id=session.id,
            raw_text=raw_text,
            stt_only=stt_only,
        )
        self._worker = worker
        worker.raw_ready.connect(self._on_raw_ready)
        worker.terminal.connect(self._on_terminal)
        worker.finished.connect(self._on_worker_finished)
        self._apply_state(TrayState.PROCESSING)
        self._refresh_recovery()
        worker.start()

    def _record(
        self, session: _Session, raw: str, warnings: tuple[AppError, ...]
    ) -> DictationRecord:
        return DictationRecord(
            id=session.id,
            created_at=session.started_at,
            raw_text=raw,
            rewrite_status="pending"
            if session.config.llm_enabled and not self._stt_only
            else "not_requested",
            warnings=warnings,
            language=session.config.stt_language,
            target_executable=session.target.executable_path if session.target else None,
            profile_id=session.profile_id,
        )

    def _on_raw_ready(self, session_id: str, raw: str, warnings: tuple[AppError, ...]) -> None:
        session = self._session
        if session is None or session.id != session_id:
            return
        self._pending_audio = None
        # Terminal can reconstruct raw if it is handled before this checkpoint.
        if self._terminal_handled:
            return
        self._retain(self._record(session, raw, warnings))

    def _on_terminal(self, result: _Terminal) -> None:
        session = self._session
        if session is None or session.id != result.session_id or self._terminal_handled:
            return
        if result.raw is not None:
            if not self._rewrite_only:
                self._pending_audio = None
            record = replace(
                self._record(session, result.raw, result.warnings),
                rewritten_text=result.rewritten,
                rewrite_status=result.rewrite_status,
            )
            if self._rewrite_only:
                # Candidate has a new identity; the source record remains untouched.
                self._candidate = record
                self._refresh_recovery()
            else:
                self._retain(record)
                if not self._recovery_only:
                    self._deliver(
                        record,
                        record.final_text,
                        session.config.output_mode,
                        session.target,
                        session.config.post_type_key,
                        allowed=self._automatic_allowed() and not result.cancelled,
                    )
            for warning in result.warnings:
                self._on_error(warning)
        elif result.error is not None and not self._exiting:
            assert self._worker is not None
            self._pending_audio = (self._worker._audio_wav, session)
        if result.error is not None:
            code = AppError.LLM_FAILED if result.raw else AppError.STT_FAILED
            self._on_error(result.error.code if isinstance(result.error, ScreamerError) else code)
        self._terminal_handled = True
        self._release_worker()

    def _on_worker_finished(self) -> None:
        self._thread_finished = True
        self._release_worker()

    def _release_worker(self) -> None:
        if not self._terminal_handled or not self._thread_finished:
            return
        worker = self._worker
        self._worker = None
        self._session = None
        self._session_config = None
        if worker is not None:
            if self._exiting:
                # finished precedes final thread teardown; join after Qt returns.
                self._retired_worker = worker
            else:
                worker.deleteLater()
        if not self._event_loop_stopped:
            self._apply_state(TrayState.IDLE)
        self._refresh_recovery()
        if self._exiting:
            self._finish_exit()

    def _automatic_allowed(self) -> bool:
        return (
            not self._cancel_event.is_set()
            and self._enabled
            and not self._exiting
            and self._settings_dlg is None
        )

    @staticmethod
    def _same_target(first: WindowIdentity | None, second: WindowIdentity | None) -> bool:
        return (
            first is not None
            and second is not None
            and first.hwnd == second.hwnd
            and first.process_id == second.process_id
        )

    def _deliver(
        self,
        record: DictationRecord,
        text: str,
        mode: str,
        target: WindowIdentity | None,
        post_key: str = "none",
        *,
        allowed: bool = True,
        copy_only: bool = False,
    ) -> None:
        attempt = DeliveryAttempt(utc_now(), mode)
        clipboard_failed = False
        if not allowed:
            attempt = replace(attempt, text_state="withheld", reason="suppressed")
        else:
            if mode in {"copy", "copy_and_type"} or copy_only:
                try:
                    clipboard = QApplication.clipboard()
                    clipboard.setText(text)
                    if (
                        platform.system() == "Windows"
                        and QApplication.platformName() == "windows"
                        and not clipboard.ownsClipboard()
                    ):
                        raise ScreamerError(AppError.CLIPBOARD_FAILED)
                    attempt = replace(
                        attempt, copied=True, reason="manual_copy" if copy_only else None
                    )
                except Exception:
                    clipboard_failed = True
                    attempt = replace(attempt, copied=False, reason="clipboard_failed")
                    self._on_error(AppError.CLIPBOARD_FAILED)
            if mode in {"type", "copy_and_type", "manual"} and not copy_only:
                current = get_foreground_target()
                if mode != "manual" and not self._automatic_allowed():
                    attempt = replace(attempt, text_state="withheld", reason="suppressed")
                elif (
                    current is None
                    or not self._same_target(target, current)
                    or current.process_id == os.getpid()
                ):
                    attempt = replace(
                        attempt,
                        text_state="withheld",
                        reason="no_target" if target is None else "target_changed",
                    )
                    self._on_error(AppError.OUTPUT_WITHHELD)
                else:
                    try:
                        report = type_text(
                            text, None if post_key == "none" else post_key, expected_target=target
                        )
                        post_state = (
                            "skipped"
                            if report.post_key_skipped
                            else "submitted"
                            if report.post_key_events_submitted
                            else "not_requested"
                        )
                        attempt = replace(
                            attempt,
                            text_state="events_submitted",
                            text_events_submitted=report.text_events_submitted,
                            post_key_state=post_state,
                            reason="post_key_skipped"
                            if report.post_key_skipped
                            else attempt.reason,
                        )
                        if report.post_key_skipped:
                            self._on_error(AppError.OUTPUT_WITHHELD)
                    except Exception as error:
                        count = getattr(error, "text_events_submitted", None)
                        expected_count = len(text.encode("utf-16-le", errors="surrogatepass"))
                        text_complete = count is not None and count == expected_count
                        attempt = replace(
                            attempt,
                            text_state="events_submitted" if text_complete else "reported_failed",
                            text_events_submitted=count,
                            post_key_state="reported_failed"
                            if text_complete and post_key != "none"
                            else "not_requested",
                            reason="post_key_failed"
                            if text_complete and post_key != "none"
                            else "injection_failed",
                        )
                        self._on_error(AppError.INJECTION_FAILED)
        if clipboard_failed:
            attempt = replace(attempt, reason="clipboard_failed")
        self._update_attempt(replace(record, last_delivery=attempt))

    def _load_history(self) -> None:
        self._committed = []
        if self._config.history_enabled:
            try:
                self._committed = self._history.trim(self._config.history_limit)
                self._committed_ids = {record.id for record in self._committed}
            except ScreamerError:
                self._on_error(AppError.HISTORY_STORAGE_FAILED)

    def _persist(self, record: DictationRecord) -> None:
        if not self._config.history_enabled:
            return
        try:
            self._committed = self._history.upsert(record, self._config.history_limit)
            self._committed_ids = {item.id for item in self._committed}
        except ScreamerError:
            self._on_error(AppError.HISTORY_STORAGE_FAILED)

    def _retain(self, record: DictationRecord) -> None:
        self._latest = record
        self._persist(record)
        self._refresh_recovery()

    def _update_attempt(self, record: DictationRecord) -> None:
        if self._candidate is not None and self._candidate.id == record.id:
            self._candidate = record
        else:
            # A failed commit must not lose an attempt on an older selected entry.
            self._latest = record
            self._persist(record)
        self._refresh_recovery()

    def _refresh_recovery(self) -> None:
        if self._recovery_dlg is None or self._event_loop_stopped:
            return
        records = {record.id: record for record in self._committed}
        for record in (self._latest, self._candidate):
            if record is not None:
                records[record.id] = record
        self._recovery_dlg.refresh(
            sorted(records.values(), key=lambda item: item.created_at, reverse=True),
            history_enabled=self._config.history_enabled,
            pending=self._pending_audio is not None,
            busy=self._worker is not None or self._recording or self._settings_dlg is not None,
            enabled=self._enabled and not self._exiting,
            shortcut=self._config.recovery_hotkey,
            candidate_id=self._candidate.id if self._candidate else None,
        )

    def _open_recovery(self) -> None:
        if self._exiting or self._settings_dlg is not None:
            return
        if self._recovery_dlg is None:
            dialog = self._recovery_dlg = RecoveryDialog()
            dialog.copy_requested.connect(self._copy_recovery)
            dialog.arm_requested.connect(self._arm_recovery)
            dialog.rewrite_requested.connect(self._rerun_rewrite)
            dialog.retry_requested.connect(self._retry_stt)
            dialog.discard_requested.connect(self._discard_audio)
            dialog.delete_requested.connect(self._delete_record)
            dialog.clear_requested.connect(self._clear_history)
        self._refresh_recovery()
        self._recovery_dlg.show()
        self._recovery_dlg.raise_()
        self._recovery_dlg.activateWindow()

    def _recovery_allowed(self) -> bool:
        return (
            self._enabled
            and not self._exiting
            and self._settings_dlg is None
            and not self._recording
            and self._worker is None
        )

    def _copy_recovery(self, record: DictationRecord, variant: str) -> None:
        if self._recovery_allowed():
            self._deliver(
                record,
                record.raw_text if variant == "raw" else record.final_text,
                "manual",
                None,
                copy_only=True,
            )

    def _arm_recovery(self, record: DictationRecord, variant: str) -> None:
        if not self._recovery_allowed():
            return
        self._armed = (record, record.raw_text if variant == "raw" else record.final_text)
        if self._recovery_dlg is not None:
            self._recovery_dlg.hide()

    def _disarm(self) -> None:
        self._armed = None

    def _insert_armed(self) -> None:
        armed = self._armed
        self._disarm()
        if armed is None or not self._recovery_allowed():
            return
        target = get_foreground_target()
        self._deliver(armed[0], armed[1], "manual", target)

    def _retry_stt(self) -> None:
        if not self._recovery_allowed() or self._pending_audio is None:
            return
        audio, old_session = self._pending_audio
        self._disarm()
        self._start_worker(audio, old_session, recovery_only=True, stt_only=True)

    def _discard_audio(self) -> None:
        if self._worker is None and not self._recording:
            self._pending_audio = None
            self._refresh_recovery()

    def _rerun_rewrite(self, record: DictationRecord) -> None:
        if not self._recovery_allowed():
            return
        try:
            config = copy.deepcopy(self._config)
            # Recovery has no external target: Automatic uses the current default.
            profile_id = self._apply_rewrite_profile(config, None)
            session = _Session(config, None, uuid4().hex, utc_now(), profile_id)
        except Exception:
            self._on_error(AppError.CONFIG_INVALID)
            return
        self._disarm()
        self._start_worker(b"", session, recovery_only=True, raw_text=record.raw_text)

    def _delete_record(self, record: DictationRecord) -> None:
        if self._worker is not None or self._recording or self._exiting:
            return
        if record.id in self._committed_ids:
            if not self._config.history_enabled:
                self._on_error(AppError.HISTORY_DISABLED)
                return
            try:
                committed = self._history.delete(record.id, self._config.history_limit)
            except ScreamerError:
                self._on_error(AppError.HISTORY_STORAGE_FAILED)
                return
            self._committed = committed
            self._committed_ids = {item.id for item in committed}
        if self._latest and self._latest.id == record.id:
            self._latest = None
        if self._candidate and self._candidate.id == record.id:
            self._candidate = None
        if self._armed and self._armed[0].id == record.id:
            self._disarm()
        self._refresh_recovery()

    def _clear_history(self) -> None:
        if self._worker is not None or self._recording or self._exiting:
            self._on_error(AppError.DICTATION_ACTIVE)
            return
        if (
            QMessageBox.question(
                self._settings_dlg or self._recovery_dlg,
                "Clear recovery",
                "Delete saved history and RAM text, including an unreadable history file? "
                "Backups are not securely erased.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        # The confirmation runs a nested event loop: a hotkey can start work.
        if self._worker is not None or self._recording or self._exiting:
            self._on_error(AppError.DICTATION_ACTIVE)
            return
        try:
            self._history.clear()
        except ScreamerError:
            self._on_error(AppError.HISTORY_STORAGE_FAILED)
            return
        self._committed = []
        self._committed_ids = set()
        self._latest = self._candidate = None
        self._disarm()
        self._refresh_recovery()

    # ------------------------------------------------------------------
    # Error → balloon
    # ------------------------------------------------------------------

    def _on_error(self, code: AppError, detail: str | None = None) -> None:
        msg = code.value
        if detail:
            msg = f"{msg}\n{detail}"
        # Provider/OS exception details may contain user text or credentials.
        log.error("AppError: %s", code.name)
        if not self._event_loop_stopped:
            self._tray.showMessage("Screamer", msg, QSystemTrayIcon.MessageIcon.Warning, 5000)

    # ------------------------------------------------------------------
    # Tray actions
    # ------------------------------------------------------------------

    def _toggle_enabled(self, checked: bool) -> None:
        self._enabled = checked
        if not checked:
            self._disarm()
            if self._recording:
                # Disabling means "stop"; don't transcribe and type the leftovers.
                self._cancel_recording()
            elif self._worker is not None:
                # Mid-processing: don't type into the focused window after the
                # user disabled us. The main-thread success slot checks again.
                self._cancel_event.set()
        log.info("Screamer %s", "enabled" if checked else "disabled")
        self._refresh_recovery()

    def _set_recording_mode(self, mode: str, rebuild_menu: bool = True) -> None:
        self._config.recording_mode = mode
        save_config(self._config)
        self._restart_hotkey()
        if rebuild_menu:
            self._rebuild_menu()
        log.info("Recording mode set to %s", mode)

    def _set_hotkey(self, key: str, rebuild_menu: bool = True) -> None:
        hotkey = Hotkey.parse(key)
        if hotkey is None or hotkey.validate() is not None:
            self._rebuild_menu()
            self._on_error(AppError.HOTKEY_INVALID)
            return
        if hotkey == Hotkey.parse(self._config.recovery_hotkey):
            self._rebuild_menu()
            self._on_error(AppError.HOTKEY_CONFLICT)
            return
        previous = self._config.hotkey
        self._config.hotkey = hotkey.to_canonical()
        try:
            save_config(self._config)
        except Exception:
            self._config.hotkey = previous
            self._rebuild_menu()
            self._on_error(AppError.KEY_STORAGE_FAILED)
            return
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

    def _set_language(self, code: str, rebuild_menu: bool = True) -> None:
        previous = self._config.stt_language
        self._config.stt_language = code
        try:
            save_config(self._config)
        except Exception:
            self._config.stt_language = previous
            self._rebuild_menu()  # A radio click already changed the visible choice.
            self._on_error(AppError.KEY_STORAGE_FAILED)
            return
        if rebuild_menu:
            self._rebuild_menu()
        log.info("STT language set to %s", code or "auto")

    def _set_rewrite_profile(self, selection: str, rebuild_menu: bool = True) -> None:
        previous = (self._config.rewrite_selection_mode, self._config.rewrite_manual_profile_id)
        if selection == "__automatic__":
            self._config.rewrite_selection_mode = "automatic"
        else:
            self._config.rewrite_selection_mode = "manual"
            self._config.rewrite_manual_profile_id = selection
        try:
            resolve_rewrite_profile(self._config, None)
            save_config(self._config)
        except Exception as error:
            self._config.rewrite_selection_mode, self._config.rewrite_manual_profile_id = previous
            self._rebuild_menu()  # A radio click already changed the visible selection.
            self._on_error(
                AppError.CONFIG_INVALID
                if isinstance(error, (TypeError, ValueError))
                else AppError.KEY_STORAGE_FAILED
            )
            return
        if rebuild_menu:
            self._rebuild_menu()

    def _toggle_rewrite(self, checked: bool) -> None:
        previous = self._config.llm_enabled
        self._config.llm_enabled = checked
        try:
            save_config(self._config)
        except Exception as error:
            self._config.llm_enabled = previous
            self._rebuild_menu()
            self._on_error(
                AppError.CONFIG_INVALID
                if isinstance(error, (TypeError, ValueError))
                else AppError.KEY_STORAGE_FAILED
            )
            return
        self._rebuild_menu()
        log.info("AI rewrite %s", "enabled" if checked else "disabled")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_settings()

    def _open_settings(self) -> None:
        if self._settings_dlg is not None or self._exiting:
            return  # Already open.
        if self._recording or self._worker is not None:
            self._on_error(AppError.DICTATION_ACTIVE)
            return

        devices = self._get_device_list()
        dlg = SettingsDialog(
            self._config,
            devices=devices,
            calibrate_fn=self._calibrate,
            refresh_devices_fn=self._get_device_list,
        )
        self._settings_dlg = dlg
        self._last_settings_dlg = dlg
        self._disarm()
        self._refresh_recovery()
        # exec() runs a nested event loop; tray edits must not race the dialog draft.
        self._rebuild_menu()
        dlg.hotkey_capture_active_changed.connect(self._on_settings_hotkey_capture_active_changed)
        dlg.applied.connect(self._on_settings_applied)
        dlg.clear_history_requested.connect(self._clear_history)
        result = SettingsDialog.DialogCode.Rejected
        try:
            result = dlg.exec()
        finally:
            self._settings_dlg = None

            # Reload after Apply/Cancel, but do not restart the listener or
            # reopen storage while an Exit is already finishing.
            if self._exiting:
                self._finish_exit()
            else:
                self._sync_settings_from_disk()

        if result == SettingsDialog.DialogCode.Accepted:
            log.info("Settings updated from dialog")
        else:
            log.info("Settings dialog closed (cancelled); reloaded from disk")

    def _on_settings_applied(self) -> None:
        self._sync_settings_from_disk(commit_retention=True)

    def _sync_settings_from_disk(self, *, commit_retention: bool = False) -> None:
        old_hotkey = self._config.hotkey
        old_mode = self._config.recording_mode
        old_recovery_hotkey = self._config.recovery_hotkey
        old_history_enabled = self._config.history_enabled
        self._config = load_config()
        if not self._exiting and (
            self._config.hotkey != old_hotkey
            or self._config.recording_mode != old_mode
            or self._config.recovery_hotkey != old_recovery_hotkey
        ):
            self._restart_hotkey()
        self._load_history()
        if commit_retention and self._config.history_enabled:
            try:
                self._committed = self._history.commit_retention(self._config.history_limit)
                self._committed_ids = {record.id for record in self._committed}
            except ScreamerError:
                self._on_error(AppError.HISTORY_STORAGE_FAILED)
        if (
            self._config.history_enabled
            and self._latest is not None
            and (commit_retention or not old_history_enabled)
        ):
            self._persist(self._latest)
        self._refresh_recovery()
        self._rebuild_menu()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _exit(self) -> None:
        if self._exiting:
            return
        log.info("Exit requested")
        self._exiting = True
        self._disarm()
        self._pending_audio = None
        if self._recovery_dlg is not None:
            self._recovery_dlg.close()
        self._recording_timer.stop()
        self._level_timer.stop()
        self._recording = False
        self._session_config = None

        # 1. Stop hotkey listener (prevents new recordings).
        self._hotkey.stop()

        # 2. Cancel worker; finished will complete shutdown without blocking UI.
        if self._worker is not None:
            self._cancel_event.set()

        # 3. Stop audio if recording.
        try:
            if self._recorder.is_recording:
                self._recorder.stop()
        except Exception:
            pass

        if self._settings_dlg is not None:
            self._settings_dlg.done(SettingsDialog.DialogCode.Rejected)
        self._finish_exit()

    def _finish_exit(self) -> None:
        if (
            self._worker is not None
            or self._settings_dlg is not None
            or self._event_loop_stopped
            or self._exit_complete
        ):
            return
        self._exit_complete = True
        # Save settings only after both threads have finished.
        try:
            save_config(self._config)
        except Exception:
            log.exception("Could not save settings during shutdown")

        # Quit Qt.
        self._tray.hide()
        self._snackbar.hide_state()
        QApplication.instance().quit()

    def _shutdown_after_event_loop(self) -> None:
        """Synchronous final safeguard, called while the instance lock is held.

        No queued finished signal is needed here. Cooperative HTTP cancellation
        waits for the real worker to end before handling its retained terminal.
        """
        self._event_loop_stopped = True
        self._exiting = True
        self._disarm()
        self._pending_audio = None
        self._recording_timer.stop()
        self._level_timer.stop()
        self._recording = False
        self._cancel_event.set()

        listener_thread = self._hotkey._thread
        if listener_thread is not None:
            try:
                self._hotkey.stop()
            except Exception:
                log.error("Could not request hotkey shutdown")
            finally:
                # stop() has a bounded join; releasing the lock needs actual exit.
                listener_thread.join()
        try:
            if self._recorder.is_recording:
                self._recorder.stop()
        except Exception:
            log.error("Could not stop capture during final shutdown")

        dialog = self._settings_dlg or self._last_settings_dlg
        if dialog is not None:
            dialog.done(SettingsDialog.DialogCode.Rejected)
            for thread in dialog.findChildren(QThread):
                thread.wait()
            # done() normally defers closing until a queued calibration callback.
            # All children are now stopped, so finish without an event-loop drain.
            QDialog.done(dialog, SettingsDialog.DialogCode.Rejected)
            self._settings_dlg = None

        worker = self._worker
        if worker is not None:
            worker.wait()
            payload = worker.terminal_payload
            if payload is not None and not self._terminal_handled:
                self._on_terminal(payload)
            self._thread_finished = True
            self._release_worker()
        if self._retired_worker is not None:
            self._retired_worker.wait()
            self._retired_worker.deleteLater()
            self._retired_worker = None
        # Also join any completed, deferred-delete children from earlier sessions.
        for thread in self.findChildren(QThread):
            thread.wait()

        if not self._exit_complete:
            try:
                save_config(self._config)
            except Exception:
                log.error("Could not save settings during final shutdown")
            self._exit_complete = True
        if self._recovery_dlg is not None:
            self._recovery_dlg.close()
        self._tray.hide()
        self._snackbar.hide_state()


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

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    directory = Path(APP_DIR).resolve()
    tray_app: _TrayApp | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        QMessageBox.warning(
            None, "Screamer", "Cannot access the user data directory. Check its permissions."
        )
        return
    instance_lock = QLockFile(str(directory / "screamer.lock"))
    # Never expire a live instance's lock based on elapsed time.
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        QMessageBox.information(
            None,
            "Screamer",
            "Screamer is already running, or its data directory cannot be locked. "
            "Use the existing tray icon or check directory permissions.",
        )
        return
    try:
        setup_logging()
        tray_app = _TrayApp(startup_mode=args.startup)  # noqa: F841 — keep ref alive for app lifetime
        log.info("Screamer started")
        app.exec()
    finally:
        if tray_app is not None:
            tray_app._shutdown_after_event_loop()
        try:
            http_client.close()
        except Exception:
            log.exception("HTTP client shutdown failed")
        instance_lock.unlock()


if __name__ == "__main__":
    main()
