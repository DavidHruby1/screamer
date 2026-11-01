"""First-dictation guide: microphone with live level, speech provider, practice field.

The tray app routes a dictation started while this window is focused into the
practice field instead of typing it, so a first transcript works without an LLM.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src import theme
from src.audio import AudioRecorder, resolve_device
from src.config import (
    LANGUAGE_OPTIONS,
    AppConfig,
    load_config,
    save_config,
    stt_provider_is_configured,
)
from src.settings_dialog import STT_PRESETS, ProviderFields, _hint, _section, _style_form
from src.utils import APP_NAME, ScreamerError

log = logging.getLogger(__name__)

DeviceItem = tuple[int, str]


class SetupWizard(QDialog):
    """Non-modal three-step guide. Emits ``saved`` after writing settings."""

    saved = Signal()
    settings_requested = Signal()

    def __init__(
        self,
        config: AppConfig,
        *,
        devices_fn: Callable[[], list[DeviceItem]] | None = None,
        hotkey_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Setup guide")
        self.setMinimumWidth(560)
        self._devices = devices_fn() if devices_fn is not None else []
        self._preview: AudioRecorder | None = None
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(60)
        self._level_timer.timeout.connect(self._poll_level)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 22)
        title = theme.set_role(QLabel("Your first dictation"), "title")
        title.setFont(theme.ui_font(22, QFont.Weight.DemiBold, -1.0))
        layout.addWidget(title)
        layout.addSpacing(12)

        body = QWidget()
        form = QFormLayout(body)
        _section(form, "1 · Microphone", first=True)
        self._device_combo = QComboBox()
        self._device_combo.addItem("System Default", None)
        for device_id, name in self._devices:
            self._device_combo.addItem(name, device_id)
        index = self._device_combo.findData(config.audio_device_id)
        self._device_combo.setCurrentIndex(max(index, 0))
        self._device_combo.currentIndexChanged.connect(self.start_preview)
        form.addRow("Microphone:", self._device_combo)
        self._level = QProgressBar()
        self._level.setRange(0, 100)
        self._level.setTextVisible(False)
        self._level.setMaximumHeight(10)
        form.addRow("Level:", self._level)
        self._mic_status = _hint(QLabel("Say something: the bar should move."), "meta")
        form.addRow(self._mic_status)

        _section(form, "2 · Speech provider")
        self._provider = ProviderFields(
            form, STT_PRESETS, key_hint="Leave empty for a local server"
        )
        self._provider.set_values(
            config.stt_api_key, config.stt_base_url, config.stt_model, config.stt_custom_headers
        )
        form.setRowVisible(self._provider.headers, False)  # Advanced; kept in Settings.
        if not config.stt_base_url:
            self._provider.preset.setCurrentIndex(1)
            self._provider._on_preset(1)
        self._language = QComboBox()
        for code, label in LANGUAGE_OPTIONS:
            self._language.addItem(label, code)
        self._language.setCurrentIndex(max(self._language.findData(config.stt_language), 0))
        form.addRow("Language:", self._language)
        self._save_btn = theme.set_role(QPushButton("Save"), "primary")
        self._save_btn.clicked.connect(self._on_save)
        self._save_status = _hint(QLabel(""), "meta")
        self._save_status.setWordWrap(True)
        save_row = QHBoxLayout()
        save_row.addWidget(self._save_btn)
        save_row.addWidget(self._save_status, 1)
        form.addRow("", save_row)

        _section(form, "3 · Try it")
        shortcut = hotkey_label or "the hotkey"
        practice_hint = QLabel(
            f"Keep this window focused, hold {shortcut}, say a sentence and release. "
            "The text appears below instead of in another app."
        )
        practice_hint.setWordWrap(True)
        form.addRow(_hint(practice_hint))
        self._practice = QPlainTextEdit()
        self._practice.setReadOnly(True)
        self._practice.setPlaceholderText("Your transcript will appear here.")
        self._practice.setFont(theme.ui_font(14))
        self._practice.setMaximumHeight(110)
        form.addRow(self._practice)
        _style_form(form)
        layout.addWidget(body, 1)

        buttons = QHBoxLayout()
        more = theme.set_role(QPushButton("All settings…"), "ghost")
        more.clicked.connect(self.settings_requested.emit)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        buttons.addWidget(more)
        buttons.addStretch()
        buttons.addWidget(done)
        layout.addLayout(buttons)
        self._update_practice_state(stt_provider_is_configured(config.stt_provider()))

    # ------------------------------------------------------------------
    # Microphone preview
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.start_preview()

    def start_preview(self, *_args) -> None:
        self.stop_preview()
        try:
            device_id = resolve_device(
                self._device_combo.currentData(),
                self._device_combo.currentText(),
                devices=self._devices,
            )
            recorder = AudioRecorder(device_id=device_id)
            recorder.retain_frames = False  # Only the level is needed; never buffer audio.
            recorder.start()
        except (ScreamerError, OSError) as e:
            log.warning("Microphone preview unavailable: %s", type(e).__name__)
            self._mic_status.setText("This microphone could not be opened. Pick another one.")
            return
        self._preview = recorder
        self._mic_status.setText("Say something: the bar should move.")
        self._level_timer.start()

    def ensure_preview(self) -> None:
        """Restart the level meter after a dictation released the microphone."""
        if self._preview is None and self.isVisible():
            self.start_preview()

    def stop_preview(self) -> None:
        self._level_timer.stop()
        recorder, self._preview = self._preview, None
        self._level.setValue(0)
        if recorder is not None:
            try:
                recorder.stop()
            except Exception:  # noqa: BLE001 — a preview has nothing worth reporting
                log.debug("Microphone preview stop failed", exc_info=True)

    def _poll_level(self) -> None:
        if self._preview is None:
            return
        capture = self._preview.snapshot()
        if capture.capture_error is not None:
            self.stop_preview()
            self._mic_status.setText("The microphone stopped. Pick it again.")
            return
        # Speech peaks around a few thousand RMS; scale so normal talking fills the bar.
        self._level.setValue(min(100, round(capture.level_rms / 30)))

    def done(self, result: int) -> None:
        self.stop_preview()
        self._provider.abort_test()  # The owner may be deleted right after ``finished``.
        super().done(result)

    # ------------------------------------------------------------------
    # Save and practice
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        provider = self._provider.provider()
        if not stt_provider_is_configured(provider):
            self._save_status.setText("Enter a base URL and model (or pick a provider).")
            return
        try:
            cfg = load_config()
            cfg.stt_api_key = provider.api_key
            cfg.stt_base_url = provider.base_url
            cfg.stt_model = provider.model
            cfg.stt_custom_headers = provider.custom_headers
            cfg.stt_language = self._language.currentData() or ""
            device_id = self._device_combo.currentData()
            cfg.audio_device_id = device_id
            cfg.audio_device_name = (
                self._device_combo.currentText() if device_id is not None else ""
            )
            save_config(cfg)
        except (ScreamerError, ValueError) as e:
            self._save_status.setText(f"Could not save: {e}")
            return
        self._save_status.setText("Saved.")
        self._update_practice_state(True)
        self.saved.emit()

    def _update_practice_state(self, ready: bool) -> None:
        self._practice.setEnabled(ready)
        if not ready:
            self._practice.setPlaceholderText("Save a speech provider first.")
        else:
            self._practice.setPlaceholderText("Your transcript will appear here.")

    def show_practice_result(self, text: str) -> None:
        self._practice.setPlainText(text)
        self._save_status.setText("It works! Close this guide and dictate anywhere.")
        self.start_preview()
