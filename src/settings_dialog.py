"""4-tab settings dialog: General, STT, LLM, Audio.

Edits a copy of AppConfig; original untouched until accept().
Standalone mode: ``python -m src.settings_dialog`` launches the dialog for testing.
"""

from __future__ import annotations

import copy
import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
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
    POST_KEY_OPTIONS,
    import_from_env,
    load_config,
    reset_config,
    save_config,
    validate_config,
)
from src.utils import APP_NAME

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

        self._hotkey_combo = QComboBox()
        for key, label in HOTKEY_OPTIONS:
            self._hotkey_combo.addItem(label, key)
        form.addRow("Hotkey:", self._hotkey_combo)

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

        self._tabs.addTab(tab, "General")

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
        self._stt_fb_check.toggled.connect(
            lambda checked: self._set_dynamic_group_visible(self._stt_fb_group, checked)
        )
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
        self._llm_fb_check.toggled.connect(
            lambda checked: self._set_dynamic_group_visible(self._llm_fb_group, checked)
        )
        llm_form.addRow(self._llm_fb_group)

        self._llm_group.setVisible(False)
        self._llm_check.toggled.connect(
            lambda checked: self._set_dynamic_group_visible(self._llm_group, checked)
        )
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
        idx = _combo_index(self._hotkey_combo, cfg.hotkey)
        self._hotkey_combo.setCurrentIndex(max(idx, 0))
        self._mode_hold.setChecked(cfg.recording_mode == "hold")
        self._mode_toggle.setChecked(cfg.recording_mode != "hold")
        idx = _combo_index(self._post_key_combo, cfg.post_type_key)
        self._post_key_combo.setCurrentIndex(max(idx, 0))

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
        self._rms_spin.setValue(cfg.rms_threshold)
        self._rms_label.setText(f"Threshold: {cfg.rms_threshold:.1f}")

    def _collect(self) -> None:
        """Write widget values back into self._working."""
        cfg = self._working

        # General
        cfg.hotkey = self._hotkey_combo.currentData()
        cfg.recording_mode = "toggle" if self._mode_toggle.isChecked() else "hold"
        cfg.post_type_key = self._post_key_combo.currentData()

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
            cfg.audio_device_name = text.split("] ", 1)[1] if "] " in text else text

    # ------------------------------------------------------------------
    # Audio tab helpers
    # ------------------------------------------------------------------

    def _populate_devices(self) -> None:
        """Fill the device combo from the pre-fetched device list."""
        self._device_combo.addItem("System Default (usually built-in laptop mic)", None)
        for dev_id, dev_name in self._devices:
            self._device_combo.addItem(f"[{dev_id}] {dev_name}", dev_id)

        # Select the stored device.
        if self._working.audio_device_id is not None:
            for i in range(self._device_combo.count()):
                if self._device_combo.itemData(i) == self._working.audio_device_id:
                    self._device_combo.setCurrentIndex(i)
                    break

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
    # Dynamic reveals
    # ------------------------------------------------------------------

    def _set_dynamic_group_visible(self, group: QWidget, visible: bool) -> None:
        group.setVisible(visible)

        if self.layout() is not None:
            self.layout().activate()
        self.resize(self.sizeHint())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_and_accept(self) -> None:
        """Validate required fields, then accept."""
        self._collect()
        if not self._show_validation_issue():
            return

        super().accept()

    # ------------------------------------------------------------------
    # Bottom bar actions
    # ------------------------------------------------------------------

    def _on_import_env(self) -> None:
        """Import .env into the working copy (empty fields only)."""
        self._collect()
        self._working = import_from_env(self._working)
        self._populate(self._working)
        log.info("Imported .env values into settings")

    def _on_reset(self) -> None:
        """Reset all fields to defaults."""
        self._working = reset_config()
        self._populate(self._working)
        log.info("Settings reset to defaults")

    def _on_apply(self) -> None:
        """Apply: collect and persist without closing."""
        self._collect()
        if not self._show_validation_issue():
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


def _combo_index(combo: QComboBox, data: str) -> int:
    for i in range(combo.count()):
        if combo.itemData(i) == data:
            return i
    return -1


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
