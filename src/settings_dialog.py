"""4-tab settings dialog: General, STT, LLM, Audio.

Edits a copy of AppConfig; original untouched until accept().
Standalone mode: ``python -m src.settings_dialog`` launches the dialog for testing.
"""

from __future__ import annotations

import copy
import logging
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QDoubleSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config import (
    AppConfig,
    DEFAULT_LLM_SYSTEM_PROMPT,
    HOTKEY_OPTIONS,
    Hotkey,
    MOUSE_MIDDLE,
    MOUSE_X1,
    MOUSE_X2,
    POST_KEY_OPTIONS,
    import_from_env,
    load_config,
    reset_config,
    save_config,
    validate_config,
)
from src.utils import APP_NAME, log_duration
from src.startup import is_supported

log = logging.getLogger(__name__)

# Type alias for device list items: (id, display_name).
DeviceItem = tuple[int, str]


class PasswordField(QLineEdit):
    """Password line edit that reveals text only while focused."""

    def __init__(self) -> None:
        super().__init__()
        self.setEchoMode(QLineEdit.EchoMode.Password)

    def focusInEvent(self, event) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Password)
        super().focusOutEvent(event)


class SettingsDialog(QDialog):
    """4-tab settings dialog editing a copy of *config*.

    *devices*: list of ``(device_id, display_name)`` for the Audio tab combo.
        Pass an empty list if audio is unavailable.
    *calibrate_fn*: ``fn(device_id) -> float`` that runs RMS calibration.
        ``None`` disables the calibrate button.
    """

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        devices: list[DeviceItem] | None = None,
        calibrate_fn: Callable[[int | None], float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.setMinimumWidth(520)

        self._devices = devices if devices is not None else []
        self._calibrate_fn = calibrate_fn

        # Edit a deep copy so the original is untouched until accept.
        self._working = copy.deepcopy(config)

        self._build_ui()
        self.layout().setSizeConstraint(QLayout.SetFixedSize)
        self._populate(self._working)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_config(self) -> AppConfig:
        """Return the edited config. Call after exec() returns Accepted."""
        return self._working

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        self._build_general_tab()
        self._build_stt_tab()
        self._build_llm_tab()
        self._build_audio_tab()

        # Bottom bar.
        btn_import = QPushButton("Import from .env")
        btn_import.clicked.connect(self._on_import_env)
        btn_reset = QPushButton("Reset to Defaults")
        btn_reset.clicked.connect(self._on_reset)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        self._button_box.accepted.connect(self._validate_and_accept)
        self._button_box.rejected.connect(self.reject)
        self._button_box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self._on_apply
        )

        bottom = QHBoxLayout()
        bottom.addWidget(btn_import)
        bottom.addWidget(btn_reset)
        bottom.addStretch()
        bottom.addWidget(self._button_box)
        root.addLayout(bottom)

    # --- General tab ---------------------------------------------------

    def _build_general_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self._captured_hotkey: Hotkey | None = None

        self._hotkey_combo = QComboBox()
        for key, label in HOTKEY_OPTIONS:
            self._hotkey_combo.addItem(label, key)
        self._hotkey_combo.addItem("Custom…", "__custom__")
        self._hotkey_combo.activated.connect(self._on_hotkey_preset_chosen)
        form.addRow("Hotkey:", self._hotkey_combo)

        self._hotkey_capture = HotkeyCaptureEdit()
        self._hotkey_capture.captured.connect(self._on_hotkey_captured)
        self._hotkey_capture.cancelled.connect(self._stop_hotkey_recording)
        self._hotkey_record_btn = QPushButton("Record")
        self._hotkey_record_btn.setCheckable(True)
        self._hotkey_record_btn.clicked.connect(self._on_hotkey_record_clicked)
        capture_row = QHBoxLayout()
        capture_row.addWidget(self._hotkey_capture, 1)
        capture_row.addWidget(self._hotkey_record_btn)
        form.addRow("", capture_row)

        self._hotkey_error = QLabel("")
        self._hotkey_error.setStyleSheet("color: #c0392b;")
        self._hotkey_error.setVisible(False)
        form.addRow("", self._hotkey_error)

        self._mode_hold = QRadioButton("Hold to talk")
        self._mode_toggle = QRadioButton("Toggle")
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._mode_hold)
        mode_row.addWidget(self._mode_toggle)
        form.addRow("Recording mode:", mode_row)

        self._post_key_combo = QComboBox()
        for key, label in POST_KEY_OPTIONS:
            self._post_key_combo.addItem(label, key)
        form.addRow("Post-type key:", self._post_key_combo)

        self._startup_check = QCheckBox("Start Screamer with Windows")
        if not is_supported():
            self._startup_check.setEnabled(False)
            self._startup_check.setToolTip("Windows only")
        form.addRow(self._startup_check)

        self._tabs.addTab(tab, "General")

    # --- Hotkey capture interaction -----------------------------------

    def _set_captured_hotkey(self, hotkey: Hotkey) -> None:
        """Store a validated hotkey and reflect it in combo + capture field."""
        self._captured_hotkey = hotkey
        self._hotkey_capture.show_hotkey(hotkey)
        self._hotkey_error.setVisible(False)
        canonical = hotkey.to_canonical()
        idx = _combo_index(self._hotkey_combo, canonical)
        self._hotkey_combo.setCurrentIndex(
            idx if idx >= 0 else _combo_index(self._hotkey_combo, "__custom__")
        )

    def _on_hotkey_preset_chosen(self, index: int) -> None:
        data = self._hotkey_combo.itemData(index)
        if data == "__custom__":
            self._start_hotkey_recording()
            return
        hotkey = Hotkey.parse(data)
        if hotkey is not None:
            self._set_captured_hotkey(hotkey)

    def _start_hotkey_recording(self) -> None:
        self._hotkey_record_btn.setChecked(True)
        self._hotkey_record_btn.setText("Cancel")
        self._hotkey_error.setVisible(False)
        self._hotkey_capture.start_recording()

    def _stop_hotkey_recording(self) -> None:
        self._hotkey_record_btn.setChecked(False)
        self._hotkey_record_btn.setText("Record")
        self._hotkey_capture.stop_recording()
        if self._captured_hotkey is not None:
            self._hotkey_capture.show_hotkey(self._captured_hotkey)

    def _on_hotkey_record_clicked(self, checked: bool) -> None:
        if checked:
            self._start_hotkey_recording()
        else:
            self._stop_hotkey_recording()

    def _on_hotkey_captured(self, hotkey: Hotkey) -> None:
        error = hotkey.validate()
        if error is not None:
            self._hotkey_error.setText(error)
            self._hotkey_error.setVisible(True)
            return  # stay in recording so the user can try again
        self._set_captured_hotkey(hotkey)
        self._stop_hotkey_recording()

    # --- STT tab -------------------------------------------------------

    def _build_stt_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self._stt_key, self._stt_url, self._stt_model, self._stt_headers = _add_provider_fields(
            form,
            base_url_placeholder="https://api.openai.com/v1",
        )

        self._stt_lang = QLineEdit()
        self._stt_lang.setPlaceholderText("auto-detect")
        form.addRow("Language:", self._stt_lang)

        # --- Fallback ---
        self._stt_fb_check = QCheckBox("Enable fallback STT provider")
        form.addRow(self._stt_fb_check)

        self._stt_fb_group = QGroupBox("Fallback STT")
        fb_form = QFormLayout(self._stt_fb_group)

        self._stt_fb_key, self._stt_fb_url, self._stt_fb_model, self._stt_fb_headers = (
            _add_provider_fields(fb_form)
        )

        self._stt_fb_group.setVisible(False)
        self._stt_fb_check.toggled.connect(self._stt_fb_group.setVisible)
        form.addRow(self._stt_fb_group)

        self._tabs.addTab(tab, "STT")

    # --- LLM tab -------------------------------------------------------

    def _build_llm_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self._llm_check = QCheckBox("Enable AI rewrite")
        form.addRow(self._llm_check)

        self._llm_group = QGroupBox("LLM Settings")
        llm_form = QFormLayout(self._llm_group)

        self._llm_key, self._llm_url, self._llm_model, self._llm_headers = _add_provider_fields(
            llm_form
        )

        self._llm_prompt = QPlainTextEdit()
        self._llm_prompt.setMaximumHeight(120)
        self._llm_prompt.setTabChangesFocus(True)
        llm_form.addRow("System Prompt:", self._llm_prompt)

        btn_reset_prompt = QPushButton("Reset to Default")
        btn_reset_prompt.clicked.connect(
            lambda: self._llm_prompt.setPlainText(DEFAULT_LLM_SYSTEM_PROMPT)
        )
        llm_form.addRow(btn_reset_prompt)

        # --- LLM Fallback ---
        self._llm_fb_check = QCheckBox("Enable fallback LLM provider")
        llm_form.addRow(self._llm_fb_check)

        self._llm_fb_group = QGroupBox("Fallback LLM")
        fb_form = QFormLayout(self._llm_fb_group)

        self._llm_fb_key, self._llm_fb_url, self._llm_fb_model, self._llm_fb_headers = (
            _add_provider_fields(fb_form)
        )

        self._llm_fb_group.setVisible(False)
        self._llm_fb_check.toggled.connect(self._llm_fb_group.setVisible)
        llm_form.addRow(self._llm_fb_group)

        self._llm_group.setVisible(False)
        self._llm_check.toggled.connect(self._llm_group.setVisible)
        form.addRow(self._llm_group)

        self._tabs.addTab(tab, "LLM")

    # --- Audio tab -----------------------------------------------------

    def _build_audio_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        hint = QLabel(
            "No external mic needed: use System Default for the built-in laptop mic, "
            "or pick the device named Microphone Array, Internal Mic, Realtek, DMIC, "
            "or Intel Smart Sound. Avoid Monitor, Stereo Mix, HDMI, and output devices."
        )
        hint.setWordWrap(True)
        form.addRow(hint)

        self._device_combo = QComboBox()
        self._populate_devices()
        form.addRow("Input Device:", self._device_combo)

        self._calibrate_btn = QPushButton("Recalibrate RMS Threshold")
        self._calibrate_btn.clicked.connect(self._on_calibrate)
        if self._calibrate_fn is None:
            self._calibrate_btn.setEnabled(False)
        form.addRow(self._calibrate_btn)

        self._rms_spin = QDoubleSpinBox()
        self._rms_spin.setRange(0.0, 32767.0)
        self._rms_spin.setDecimals(1)
        self._rms_spin.setSingleStep(1.0)
        self._rms_spin.setSpecialValueText("Disabled")
        form.addRow("RMS Threshold:", self._rms_spin)

        self._rms_label = QLabel("Threshold: —")
        form.addRow(self._rms_label)

        self._tabs.addTab(tab, "Audio")

    # ------------------------------------------------------------------
    # Populate / collect
    # ------------------------------------------------------------------

    def _populate(self, cfg: AppConfig) -> None:
        """Fill all widgets from *cfg*."""
        # General
        hotkey = Hotkey.parse(cfg.hotkey) or Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        self._set_captured_hotkey(hotkey)
        self._mode_hold.setChecked(cfg.recording_mode == "hold")
        self._mode_toggle.setChecked(cfg.recording_mode != "hold")
        idx = _combo_index(self._post_key_combo, cfg.post_type_key)
        self._post_key_combo.setCurrentIndex(max(idx, 0))
        self._startup_check.setChecked(cfg.start_with_windows)

        # STT
        self._stt_key.setText(cfg.stt_api_key)
        self._stt_url.setText(cfg.stt_base_url)
        self._stt_model.setText(cfg.stt_model)
        self._stt_lang.setText(cfg.stt_language)
        self._stt_headers.setText(cfg.stt_custom_headers)
        self._stt_fb_check.setChecked(cfg.stt_fallback_enabled)
        self._stt_fb_key.setText(cfg.stt_fallback_api_key)
        self._stt_fb_url.setText(cfg.stt_fallback_base_url)
        self._stt_fb_model.setText(cfg.stt_fallback_model)
        self._stt_fb_headers.setText(cfg.stt_fallback_custom_headers)

        # LLM
        self._llm_check.setChecked(cfg.llm_enabled)
        self._llm_key.setText(cfg.llm_api_key)
        self._llm_url.setText(cfg.llm_base_url)
        self._llm_model.setText(cfg.llm_model)
        self._llm_headers.setText(cfg.llm_custom_headers)
        self._llm_prompt.setPlainText(cfg.llm_system_prompt)
        self._llm_fb_check.setChecked(cfg.llm_fallback_enabled)
        self._llm_fb_key.setText(cfg.llm_fallback_api_key)
        self._llm_fb_url.setText(cfg.llm_fallback_base_url)
        self._llm_fb_model.setText(cfg.llm_fallback_model)
        self._llm_fb_headers.setText(cfg.llm_fallback_custom_headers)

        # Audio
        self._select_device(cfg)
        self._rms_spin.setValue(cfg.rms_threshold)
        self._rms_label.setText(f"Threshold: {cfg.rms_threshold:.1f}")

    def _collect(self) -> None:
        """Write widget values back into self._working."""
        cfg = self._working

        # General
        if self._captured_hotkey is not None:
            cfg.hotkey = self._captured_hotkey.to_canonical()
        cfg.recording_mode = "toggle" if self._mode_toggle.isChecked() else "hold"
        cfg.post_type_key = self._post_key_combo.currentData()
        cfg.start_with_windows = self._startup_check.isChecked()

        # STT
        cfg.stt_api_key = self._stt_key.text().strip()
        cfg.stt_base_url = self._stt_url.text().strip()
        cfg.stt_model = self._stt_model.text().strip()
        cfg.stt_language = self._stt_lang.text().strip()
        cfg.stt_custom_headers = self._stt_headers.text().strip()
        cfg.stt_fallback_enabled = self._stt_fb_check.isChecked()
        cfg.stt_fallback_api_key = self._stt_fb_key.text().strip()
        cfg.stt_fallback_base_url = self._stt_fb_url.text().strip()
        cfg.stt_fallback_model = self._stt_fb_model.text().strip()
        cfg.stt_fallback_custom_headers = self._stt_fb_headers.text().strip()

        # LLM
        cfg.llm_enabled = self._llm_check.isChecked()
        cfg.llm_api_key = self._llm_key.text().strip()
        cfg.llm_base_url = self._llm_url.text().strip()
        cfg.llm_model = self._llm_model.text().strip()
        cfg.llm_custom_headers = self._llm_headers.text().strip()
        cfg.llm_system_prompt = self._llm_prompt.toPlainText()
        cfg.llm_fallback_enabled = self._llm_fb_check.isChecked()
        cfg.llm_fallback_api_key = self._llm_fb_key.text().strip()
        cfg.llm_fallback_base_url = self._llm_fb_url.text().strip()
        cfg.llm_fallback_model = self._llm_fb_model.text().strip()
        cfg.llm_fallback_custom_headers = self._llm_fb_headers.text().strip()

        # Audio
        cfg.audio_device_id = self._device_combo.currentData()
        cfg.rms_threshold = self._rms_spin.value()
        cfg.audio_device_name = ""
        if cfg.audio_device_id is not None:
            text = self._device_combo.currentText()
            cfg.audio_device_name = _clean_device_name(
                text.split("] ", 1)[1] if "] " in text else text
            )

    # ------------------------------------------------------------------
    # Audio tab helpers
    # ------------------------------------------------------------------

    def _populate_devices(self) -> None:
        """Fill the device combo from the pre-fetched device list."""
        default_name = next(
            (
                _clean_device_name(dev_name)
                for _dev_id, dev_name in self._devices
                if dev_name.endswith(" (Default input)")
            ),
            "usually built-in laptop mic",
        )
        self._device_combo.addItem(f"System Default ({default_name})", None)
        for dev_id, dev_name in self._devices:
            self._device_combo.addItem(f"[{dev_id}] {dev_name}", dev_id)

        self._select_device(self._working)

    def _select_device(self, cfg: AppConfig) -> None:
        """Select saved device by current ID, then by stable device name."""
        self._device_combo.setCurrentIndex(0)
        saved_name = _clean_device_name(cfg.audio_device_name).lower()

        if cfg.audio_device_id is not None:
            for i in range(self._device_combo.count()):
                if self._device_combo.itemData(i) == cfg.audio_device_id:
                    item_name = _clean_device_name(self._device_combo.itemText(i).split("] ", 1)[-1])
                    if not saved_name or saved_name in item_name.lower():
                        self._device_combo.setCurrentIndex(i)
                        return

        if saved_name:
            for i in range(self._device_combo.count()):
                item_data = self._device_combo.itemData(i)
                item_name = _clean_device_name(self._device_combo.itemText(i).split("] ", 1)[-1])
                if item_data is not None and saved_name in item_name.lower():
                    self._device_combo.setCurrentIndex(i)
                    return

    def _on_calibrate(self) -> None:
        """Run RMS auto-calibration via the provided callback."""
        if self._calibrate_fn is None:
            return

        device_id = self._device_combo.currentData()
        try:
            QMessageBox.information(
                self,
                "Calibrating",
                "Silence please — measuring ambient noise for 2 seconds...",
            )
            threshold = self._calibrate_fn(device_id)
            self._working.rms_threshold = threshold
            self._rms_spin.setValue(threshold)
            self._rms_label.setText(f"Threshold: {threshold:.1f}")
        except Exception as e:
            QMessageBox.warning(self, "Calibration Failed", str(e))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_and_accept(self) -> None:
        """Validate required fields, then accept."""
        self._collect()
        if not self._show_validation_issue():
            return
        if not self._sync_startup_or_warn():
            return

        super().accept()

    # ------------------------------------------------------------------
    # Bottom bar actions
    # ------------------------------------------------------------------

    def _on_import_env(self) -> None:
        """Import .env into the working copy (empty fields only)."""
        with log_duration(log, "Settings import from .env"):
            self._collect()
            self._working = import_from_env(self._working)
            self._populate(self._working)
            log.info("Imported .env values into settings")

    def _on_reset(self) -> None:
        """Reset all fields to defaults."""
        with log_duration(log, "Settings reset to defaults"):
            self._working = reset_config()
            self._populate(self._working)
            log.info("Settings reset to defaults")

    def _on_apply(self) -> None:
        """Apply: collect and persist without closing."""
        with log_duration(log, "Settings apply"):
            self._collect()
            if not self._show_validation_issue():
                return
            if not self._sync_startup_or_warn():
                return
            save_config(self._working)
            log.info("Settings applied")

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def accept(self) -> None:
        self._collect()
        super().accept()

    def _show_validation_issue(self) -> bool:
        issue = next(iter(validate_config(self._working)), None)
        if issue is None:
            return True

        QMessageBox.warning(self, "Missing Configuration", issue.message)
        self._tabs.setCurrentIndex(issue.tab_index)
        return False

    def _sync_startup_or_warn(self) -> bool:
        from src.startup import sync_enabled
        from src.utils import ScreamerError

        if not is_supported():
            return True

        try:
            sync_enabled(self._working.start_with_windows)
            return True
        except ScreamerError as e:
            QMessageBox.warning(self, "Startup Setting Failed", str(e))
            return False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _add_provider_fields(
    form: QFormLayout,
    *,
    base_url_placeholder: str = "",
) -> tuple[QLineEdit, QLineEdit, QLineEdit, QLineEdit]:
    key = PasswordField()
    form.addRow("API Key:", key)

    url = QLineEdit()
    if base_url_placeholder:
        url.setPlaceholderText(base_url_placeholder)
    form.addRow("Base URL:", url)

    model = QLineEdit()
    form.addRow("Model:", model)

    headers = QLineEdit()
    headers.setPlaceholderText('{"X-Custom": "value"}')
    form.addRow("Custom Headers:", headers)

    return key, url, model, headers


def _mods_from_qt(modifiers) -> frozenset:
    """Map Qt.KeyboardModifiers to our canonical modifier-name set."""
    mods = set()
    if modifiers & Qt.ControlModifier:
        mods.add("ctrl")
    if modifiers & Qt.AltModifier:
        mods.add("alt")
    if modifiers & Qt.ShiftModifier:
        mods.add("shift")
    if modifiers & Qt.MetaModifier:
        mods.add("win")
    return frozenset(mods)


_QT_MOUSE_TO_CODE = {
    Qt.BackButton: MOUSE_X1,
    Qt.ForwardButton: MOUSE_X2,
    Qt.MiddleButton: MOUSE_MIDDLE,
}

# Qt key codes that are modifiers (ignored as a trigger during capture).
# Stored as ints so membership works regardless of enum/int return type.
_QT_MODIFIER_KEYS = frozenset(
    int(k) for k in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr)
)


def _mouse_button_to_code(button):
    """Map a Qt.MouseButton to a MOUSE_* code, or None if not bindable."""
    return _QT_MOUSE_TO_CODE.get(button)


class HotkeyCaptureEdit(QLineEdit):
    """Read-only field that records the next key/mouse chord while recording.

    Emits ``captured`` with a Hotkey on a complete chord. Keyboard chords finalize
    on the first non-modifier key; mouse chords finalize on a side/middle click.
    """

    captured = Signal(object)  # Hotkey
    cancelled = Signal()       # Esc pressed during recording

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        self._recording = True
        self.setText("press keys or a mouse button…")
        self.setFocus(Qt.OtherFocusReason)
        self.grabKeyboard()
        self.grabMouse()

    def stop_recording(self) -> None:
        self._recording = False
        self.releaseMouse()
        self.releaseKeyboard()

    def show_hotkey(self, hotkey: Hotkey) -> None:
        self.setText(hotkey.to_label())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        event.accept()
        if int(event.key()) == int(Qt.Key_Escape):
            self.cancelled.emit()
            return
        if event.isAutoRepeat() or int(event.key()) in _QT_MODIFIER_KEYS:
            return
        vk = event.nativeVirtualKey()
        if not vk:
            return
        self.captured.emit(Hotkey(_mods_from_qt(event.modifiers()), "key", vk))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._recording:
            super().mousePressEvent(event)
            return
        code = _mouse_button_to_code(event.button())
        if code is None:
            event.accept()  # swallow left/right; only side/middle bind
            return
        event.accept()
        self.captured.emit(Hotkey(_mods_from_qt(event.modifiers()), "mouse", code))


def _combo_index(combo: QComboBox, data: str) -> int:
    for i in range(combo.count()):
        if combo.itemData(i) == data:
            return i
    return -1


def _clean_device_name(name: str) -> str:
    return name.removesuffix(" (Default input)").strip()


# ------------------------------------------------------------------
# Standalone mode
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    cfg = load_config()
    cfg = import_from_env(cfg)

    # In standalone mode, try to import audio for device listing.
    devices: list[DeviceItem] = []
    calibrate_fn = None
    try:
        from src.audio import AudioRecorder, list_devices as _list_devices

        for dev in _list_devices():
            devices.append((dev.id, dev.name))

        def _calibrate(device_id: int | None) -> float:
            recorder = AudioRecorder(device_id=device_id)
            return recorder.calibrate(2.0)

        calibrate_fn = _calibrate
    except Exception:
        pass

    dlg = SettingsDialog(cfg, devices=devices, calibrate_fn=calibrate_fn)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        new_cfg = dlg.get_config()
        save_config(new_cfg)
        print("Settings saved.")
        for fld in new_cfg.__dataclass_fields__:
            val = getattr(new_cfg, fld)
            if "key" in fld and val:
                print(f"  {fld} = ***")
            else:
                print(f"  {fld} = {val}")
    else:
        print("Cancelled.")
