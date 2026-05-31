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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config import (
    AppConfig,
    DEFAULT_LLM_SYSTEM_PROMPT,
    import_from_env,
    load_config,
    reset_config,
    save_config,
)
from src.utils import APP_NAME

log = logging.getLogger(__name__)

# Keys available for hotkey selection (shared with tray menu in main.py).
HOTKEY_OPTIONS: list[tuple[str, str]] = [
    ("scroll_lock", "Scroll Lock"),
    ("ctrl", "Ctrl"),
    ("alt", "Alt"),
    ("pause", "Pause"),
    ("f13", "F13"),
    ("f14", "F14"),
]

# Post-type key options (shared with tray menu in main.py).
POST_KEY_OPTIONS: list[tuple[str, str]] = [
    ("none", "None"),
    ("enter", "Enter"),
    ("tab", "Tab"),
    ("space", "Space"),
    ("backspace", "Backspace"),
]

# Type alias for device list items: (id, display_name).
DeviceItem = tuple[int, str]


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

        self._stt_key = _password_field_with_toggle()
        form.addRow("API Key:", self._stt_key)

        self._stt_url = QLineEdit()
        self._stt_url.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL:", self._stt_url)

        self._stt_model = QLineEdit()
        form.addRow("Model:", self._stt_model)

        self._stt_lang = QLineEdit()
        self._stt_lang.setPlaceholderText("auto-detect")
        form.addRow("Language:", self._stt_lang)

        self._stt_headers = QLineEdit()
        self._stt_headers.setPlaceholderText('{"X-Custom": "value"}')
        form.addRow("Custom Headers:", self._stt_headers)

        # --- Fallback ---
        self._stt_fb_check = QCheckBox("Enable fallback STT provider")
        self._stt_fb_check.toggled.connect(self._on_stt_fallback_toggled)
        form.addRow(self._stt_fb_check)

        self._stt_fb_group = QGroupBox("Fallback STT")
        fb_form = QFormLayout(self._stt_fb_group)

        self._stt_fb_key = _password_field_with_toggle()
        fb_form.addRow("API Key:", self._stt_fb_key)

        self._stt_fb_url = QLineEdit()
        fb_form.addRow("Base URL:", self._stt_fb_url)

        self._stt_fb_model = QLineEdit()
        fb_form.addRow("Model:", self._stt_fb_model)

        self._stt_fb_headers = QLineEdit()
        self._stt_fb_headers.setPlaceholderText('{"X-Custom": "value"}')
        fb_form.addRow("Custom Headers:", self._stt_fb_headers)

        self._stt_fb_group.setVisible(False)
        form.addRow(self._stt_fb_group)

        self._tabs.addTab(tab, "STT")

    # --- LLM tab -------------------------------------------------------

    def _build_llm_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self._llm_check = QCheckBox("Enable AI rewrite")
        self._llm_check.toggled.connect(self._on_llm_toggled)
        form.addRow(self._llm_check)

        self._llm_group = QGroupBox("LLM Settings")
        llm_form = QFormLayout(self._llm_group)

        self._llm_key = _password_field_with_toggle()
        llm_form.addRow("API Key:", self._llm_key)

        self._llm_url = QLineEdit()
        llm_form.addRow("Base URL:", self._llm_url)

        self._llm_model = QLineEdit()
        llm_form.addRow("Model:", self._llm_model)

        self._llm_headers = QLineEdit()
        self._llm_headers.setPlaceholderText('{"X-Custom": "value"}')
        llm_form.addRow("Custom Headers:", self._llm_headers)

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
        self._llm_fb_check.toggled.connect(self._on_llm_fallback_toggled)
        llm_form.addRow(self._llm_fb_check)

        self._llm_fb_group = QGroupBox("Fallback LLM")
        fb_form = QFormLayout(self._llm_fb_group)

        self._llm_fb_key = _password_field_with_toggle()
        fb_form.addRow("API Key:", self._llm_fb_key)

        self._llm_fb_url = QLineEdit()
        fb_form.addRow("Base URL:", self._llm_fb_url)

        self._llm_fb_model = QLineEdit()
        fb_form.addRow("Model:", self._llm_fb_model)

        self._llm_fb_headers = QLineEdit()
        self._llm_fb_headers.setPlaceholderText('{"X-Custom": "value"}')
        fb_form.addRow("Custom Headers:", self._llm_fb_headers)

        self._llm_fb_group.setVisible(False)
        llm_form.addRow(self._llm_fb_group)

        self._llm_group.setVisible(False)
        form.addRow(self._llm_group)

        self._tabs.addTab(tab, "LLM")

    # --- Audio tab -----------------------------------------------------

    def _build_audio_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self._device_combo = QComboBox()
        self._populate_devices()
        form.addRow("Input Device:", self._device_combo)

        self._calibrate_btn = QPushButton("Recalibrate RMS Threshold")
        self._calibrate_btn.clicked.connect(self._on_calibrate)
        if self._calibrate_fn is None:
            self._calibrate_btn.setEnabled(False)
        form.addRow(self._calibrate_btn)

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

    # ------------------------------------------------------------------
    # Audio tab helpers
    # ------------------------------------------------------------------

    def _populate_devices(self) -> None:
        """Fill the device combo from the pre-fetched device list."""
        self._device_combo.addItem("(System Default)", None)
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
            self._rms_label.setText(f"Threshold: {threshold:.1f}")
        except Exception as e:
            QMessageBox.warning(self, "Calibration Failed", str(e))

    # ------------------------------------------------------------------
    # Dynamic reveals
    # ------------------------------------------------------------------

    def _on_stt_fallback_toggled(self, checked: bool) -> None:
        self._stt_fb_group.setVisible(checked)

    def _on_llm_toggled(self, checked: bool) -> None:
        self._llm_group.setVisible(checked)

    def _on_llm_fallback_toggled(self, checked: bool) -> None:
        self._llm_fb_group.setVisible(checked)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_and_accept(self) -> None:
        """Validate required fields, then accept."""
        self._collect()
        cfg = self._working

        if not cfg.stt_api_key and not (cfg.stt_fallback_enabled and cfg.stt_fallback_api_key):
            QMessageBox.warning(
                self,
                "Missing Configuration",
                "At least one STT API key is required.",
            )
            self._tabs.setCurrentIndex(1)  # Switch to STT tab.
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
        save_config(self._working)
        log.info("Settings applied")

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def accept(self) -> None:
        self._collect()
        super().accept()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _password_field_with_toggle() -> QLineEdit:
    """Password QLineEdit that toggles echo mode on focus-in/focus-out."""
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field._showing = False

    original_focus_in = field.focusInEvent
    original_focus_out = field.focusOutEvent

    def focus_in(event):
        field.setEchoMode(QLineEdit.EchoMode.Normal)
        field._showing = True
        original_focus_in(event)

    def focus_out(event):
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field._showing = False
        original_focus_out(event)

    field.focusInEvent = focus_in
    field.focusOutEvent = focus_out
    return field


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
