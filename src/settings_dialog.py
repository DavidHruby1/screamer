"""4-tab settings dialog: General, STT, LLM, Audio.

Edits a copy of AppConfig; Apply and OK persist validated settings.
Standalone mode: ``python -m src.settings_dialog`` launches the dialog for testing.
"""

from __future__ import annotations

import copy
import logging
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QThread, Signal
from PySide6.QtGui import QAction, QIcon, QKeyEvent, QMouseEvent, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
    QListWidget,
    QListWidgetItem,
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
    LANGUAGE_OPTIONS,
    Hotkey,
    MOUSE_MIDDLE,
    MOUSE_X1,
    MOUSE_X2,
    POST_KEY_OPTIONS,
    import_from_env,
    language_choices,
    load_config,
    normalize_language_code,
    normalize_language_favorites,
    reset_config,
    save_config,
    validate_config,
)
from src.audio import resolve_device
from src.utils import APP_NAME, AppError, ScreamerError, log_duration
from src.startup import is_supported

log = logging.getLogger(__name__)

# Type alias for device list items: (id, display_name).
DeviceItem = tuple[int, str]

_CALIBRATE_LABEL = "Recalibrate RMS Threshold"


class _CalibrateThread(QThread):
    """Runs the blocking RMS calibration off the UI thread."""

    succeeded = Signal(float)
    failed = Signal(str)

    def __init__(
        self,
        fn: Callable[[int | None], float],
        device_id: int | None,
        device_name: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._fn = fn
        self._device_id = device_id
        self._device_name = device_name

    def run(self) -> None:
        try:
            device_id = resolve_device(self._device_id, self._device_name)
            self.succeeded.emit(self._fn(device_id))
        except Exception as e:  # surface any calibration failure to the dialog
            self.failed.emit(str(e))


def _eye_icon() -> QIcon:
    """Small glyph for the show/hide toggle (the project ships no icon assets)."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    # Default pen is black — invisible on dark themes; follow the palette.
    painter.setPen(QApplication.palette().color(QPalette.ColorRole.Text))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "\N{EYE}")
    painter.end()
    return QIcon(pixmap)


class PasswordField(QLineEdit):
    """Masked line edit with an explicit trailing show/hide toggle.

    Stays masked on focus; the secret is only revealed while the toggle is on.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setEchoMode(QLineEdit.EchoMode.Password)
        reveal = QAction(_eye_icon(), "Show", self)
        reveal.setCheckable(True)
        reveal.setToolTip("Show/hide value")
        reveal.toggled.connect(self._on_reveal_toggled)
        self.addAction(reveal, QLineEdit.ActionPosition.TrailingPosition)

    def _on_reveal_toggled(self, checked: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)


class SettingsDialog(QDialog):
    """4-tab settings dialog editing a copy of *config*.

    *devices*: list of ``(device_id, display_name)`` for the Audio tab combo.
        Pass an empty list if audio is unavailable.
    *calibrate_fn*: ``fn(device_id) -> float`` that runs RMS calibration.
        ``None`` disables the calibrate button.
    *refresh_devices_fn*: optional user-triggered input-device enumeration.
    """

    hotkey_capture_active_changed = Signal(bool)
    applied = Signal()

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        devices: list[DeviceItem] | None = None,
        calibrate_fn: Callable[[int | None], float] | None = None,
        refresh_devices_fn: Callable[[], list[DeviceItem]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.setMinimumWidth(520)

        self._devices = devices if devices is not None else []
        self._calibrate_fn = calibrate_fn
        self._refresh_devices_fn = refresh_devices_fn
        self._calib_thread: _CalibrateThread | None = None
        self._pending_result: int | None = None

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
        self._button_box.accepted.connect(self.accept)
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
        self._hotkey_record_btn = QPushButton("Register Hotkey")
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
        if self._hotkey_capture.is_recording():
            return
        self._hotkey_record_btn.setChecked(True)
        self._hotkey_record_btn.setText("Cancel")
        self._hotkey_error.setVisible(False)
        self.hotkey_capture_active_changed.emit(True)
        self._hotkey_capture.start_recording()

    def _stop_hotkey_recording(self) -> None:
        if not self._hotkey_capture.is_recording():
            return
        self._hotkey_record_btn.setChecked(False)
        self._hotkey_record_btn.setText("Register Hotkey")
        self._hotkey_capture.stop_recording()
        if self._captured_hotkey is not None:
            self._hotkey_capture.show_hotkey(self._captured_hotkey)
        self.hotkey_capture_active_changed.emit(False)

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

        self._stt_lang = QComboBox()
        form.addRow("Active language:", self._stt_lang)

        self._stt_favorites = QListWidget()
        self._stt_favorites.setMaximumHeight(120)
        self._stt_favorites.itemSelectionChanged.connect(self._on_favorite_selected)
        form.addRow("Favorite languages:", self._stt_favorites)

        self._stt_favorite_input = QLineEdit()
        self._stt_favorite_input.setPlaceholderText("Language code, e.g. de or pt-br")
        form.addRow("Favorite code:", self._stt_favorite_input)

        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self._on_favorite_add)
        btn_edit = QPushButton("Edit selected")
        btn_edit.clicked.connect(self._on_favorite_edit)
        btn_remove = QPushButton("Remove selected")
        btn_remove.clicked.connect(self._on_favorite_remove)
        favorites_buttons = QHBoxLayout()
        for button in (btn_add, btn_edit, btn_remove):
            favorites_buttons.addWidget(button)
        form.addRow("", favorites_buttons)

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

    def _refresh_language_choices(self, active: str | None = None) -> None:
        if active is None:
            active = self._stt_lang.currentData() or ""
        favorites = [
            self._stt_favorites.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._stt_favorites.count())
        ]
        self._stt_lang.clear()
        for code, label in language_choices(active, favorites):
            self._stt_lang.addItem(label, code)
        self._stt_lang.setCurrentIndex(_combo_index(self._stt_lang, active))

    def _on_favorite_selected(self) -> None:
        item = self._stt_favorites.currentItem()
        if item is not None:
            self._stt_favorite_input.setText(item.data(Qt.ItemDataRole.UserRole))

    def _favorite_code(self) -> str | None:
        try:
            return normalize_language_code(self._stt_favorite_input.text())
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Language", str(e))
            return None

    def _on_favorite_add(self) -> None:
        code = self._favorite_code()
        if code is None:
            return
        existing = [
            self._stt_favorites.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._stt_favorites.count())
        ]
        if code in existing:
            QMessageBox.warning(self, "Invalid Language", "That language is already a favorite.")
            return
        try:
            normalize_language_favorites([*existing, code])
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Language", str(e))
            return
        label = dict(LANGUAGE_OPTIONS).get(code, code)
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, code)
        self._stt_favorites.addItem(item)
        self._refresh_language_choices()

    def _on_favorite_edit(self) -> None:
        item = self._stt_favorites.currentItem()
        if item is None:
            return
        code = self._favorite_code()
        if code is None:
            return
        old = item.data(Qt.ItemDataRole.UserRole)
        if code != old and any(
            self._stt_favorites.item(i).data(Qt.ItemDataRole.UserRole) == code
            for i in range(self._stt_favorites.count())
        ):
            QMessageBox.warning(self, "Invalid Language", "That language is already a favorite.")
            return
        active = self._stt_lang.currentData()
        item.setData(Qt.ItemDataRole.UserRole, code)
        item.setText(dict(LANGUAGE_OPTIONS).get(code, code))
        self._refresh_language_choices(code if active == old else active)

    def _on_favorite_remove(self) -> None:
        row = self._stt_favorites.currentRow()
        if row < 0:
            return
        active = self._stt_lang.currentData()
        code = self._stt_favorites.takeItem(row).data(Qt.ItemDataRole.UserRole)
        self._stt_favorite_input.clear()
        if active == code and code not in dict(LANGUAGE_OPTIONS):
            active = ""
        self._refresh_language_choices(active)

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

        self._llm_reset_prompt_btn = QPushButton("Reset to Current Default")
        self._llm_reset_prompt_btn.clicked.connect(self._on_reset_prompt)
        llm_form.addRow(self._llm_reset_prompt_btn)
        prompt_hint = QLabel(
            "Saved prompts are kept unchanged on upgrade. Reset explicitly selects the current "
            "cleanup default. Models can still change meaning; review important dictation."
        )
        prompt_hint.setWordWrap(True)
        llm_form.addRow(prompt_hint)

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

    def _on_reset_prompt(self) -> None:
        self._llm_prompt.setPlainText(DEFAULT_LLM_SYSTEM_PROMPT)
        self._llm_prompt_baseline = (
            DEFAULT_LLM_SYSTEM_PROMPT,
            "default_v2",
            self._llm_prompt.toPlainText(),
        )

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
        self._refresh_devices_btn = QPushButton("Refresh")
        self._refresh_devices_btn.setEnabled(self._refresh_devices_fn is not None)
        self._refresh_devices_btn.clicked.connect(self.refresh_devices)
        device_row = QHBoxLayout()
        device_row.addWidget(self._device_combo, 1)
        device_row.addWidget(self._refresh_devices_btn)
        form.addRow("Input Device:", device_row)
        policy = QLabel(
            "An unavailable or ambiguous explicit input will not switch to System Default. "
            "Refresh and reselect after device changes. Device IDs/names do not prove physical identity."
        )
        policy.setWordWrap(True)
        form.addRow(policy)

        self._calibrate_btn = QPushButton(_CALIBRATE_LABEL)
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
        self._stt_favorites.clear()
        for code in cfg.stt_language_favorites:
            item = QListWidgetItem(dict(LANGUAGE_OPTIONS).get(code, code))
            item.setData(Qt.ItemDataRole.UserRole, code)
            self._stt_favorites.addItem(item)
        self._stt_favorite_input.clear()
        self._refresh_language_choices(cfg.stt_language)
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
        self._llm_prompt_baseline = (
            cfg.llm_system_prompt,
            cfg.llm_prompt_origin,
            self._llm_prompt.toPlainText(),
        )
        self._llm_fb_check.setChecked(cfg.llm_fallback_enabled)
        self._llm_fb_key.setText(cfg.llm_fallback_api_key)
        self._llm_fb_url.setText(cfg.llm_fallback_base_url)
        self._llm_fb_model.setText(cfg.llm_fallback_model)
        self._llm_fb_headers.setText(cfg.llm_fallback_custom_headers)

        # Audio
        self._select_device(cfg.audio_device_id, cfg.audio_device_name)
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
        cfg.stt_language = self._stt_lang.currentData()
        cfg.stt_language_favorites = [
            self._stt_favorites.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._stt_favorites.count())
        ]
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
        prompt = self._llm_prompt.toPlainText()
        original_prompt, origin, displayed_prompt = self._llm_prompt_baseline
        # Qt normalizes line endings; unrelated Apply/OK must keep the original string.
        if prompt != displayed_prompt:
            cfg.llm_system_prompt = prompt
            cfg.llm_prompt_origin = "user_saved"
        else:
            cfg.llm_system_prompt = original_prompt
            cfg.llm_prompt_origin = origin
        cfg.llm_fallback_enabled = self._llm_fb_check.isChecked()
        cfg.llm_fallback_api_key = self._llm_fb_key.text().strip()
        cfg.llm_fallback_base_url = self._llm_fb_url.text().strip()
        cfg.llm_fallback_model = self._llm_fb_model.text().strip()
        cfg.llm_fallback_custom_headers = self._llm_fb_headers.text().strip()

        # Audio
        cfg.audio_device_id, cfg.audio_device_name = self._selected_device()
        cfg.rms_threshold = self._rms_spin.value()

    # ------------------------------------------------------------------
    # Audio tab helpers
    # ------------------------------------------------------------------

    def _populate_devices(self) -> None:
        """Fill the device combo from the pre-fetched device list."""
        self._device_combo.clear()
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
            self._device_combo.setItemData(
                self._device_combo.count() - 1,
                _clean_device_name(dev_name),
                Qt.ItemDataRole.UserRole + 2,
            )

    def _selected_device(self) -> tuple[int | None, str]:
        device_id = self._device_combo.currentData()
        unavailable_name = self._device_combo.currentData(Qt.ItemDataRole.UserRole + 1)
        if unavailable_name is not None:
            return device_id, unavailable_name
        name = self._device_combo.currentData(Qt.ItemDataRole.UserRole + 2)
        return device_id, name if device_id is not None else ""

    def refresh_devices(self) -> None:
        """Refresh on request without replacing an unresolved explicit preference."""
        if self._refresh_devices_fn is None or self._pending_result is not None:
            return
        preferred_id, preferred_name = self._selected_device()
        try:
            devices = self._refresh_devices_fn()
            if not devices:
                raise ScreamerError(AppError.MIC_UNAVAILABLE)
        except Exception:
            QMessageBox.warning(
                self,
                "Microphone Refresh Failed",
                "No input-device list available. The selection was kept; try Refresh again.",
            )
            return
        self._devices = devices
        self._populate_devices()
        self._select_device(preferred_id, preferred_name)

    def _select_device(self, preferred_id: int | None, preferred_name: str) -> None:
        """Use the recorder policy; preserve unresolved identity until reselection."""
        for i in range(self._device_combo.count() - 1, -1, -1):
            if self._device_combo.itemData(i, Qt.ItemDataRole.UserRole + 1) is not None:
                self._device_combo.removeItem(i)
        self._device_combo.setCurrentIndex(0)
        try:
            device_id = resolve_device(preferred_id, preferred_name, devices=self._devices)
        except ScreamerError:
            self._device_combo.addItem(
                f"[{preferred_id}] {preferred_name} (Unavailable / reselect)",
                preferred_id,
            )
            index = self._device_combo.count() - 1
            self._device_combo.setItemData(index, preferred_name, Qt.ItemDataRole.UserRole + 1)
            self._device_combo.setCurrentIndex(index)
        else:
            self._device_combo.setCurrentIndex(self._device_combo.findData(device_id))

    def _on_calibrate(self) -> None:
        """Run RMS auto-calibration in a worker thread; keep the dialog responsive."""
        if (
            self._calibrate_fn is None
            or self._calib_thread is not None
            or self._pending_result is not None
        ):
            return

        device_id, device_name = self._selected_device()
        try:
            resolve_device(device_id, device_name, devices=self._devices)
        except ScreamerError as e:
            QMessageBox.warning(self, "Calibration Failed", str(e))
            return
        QMessageBox.information(
            self,
            "Calibrating",
            "Silence please — measuring ambient noise for 2 seconds...",
        )
        self._calibrate_btn.setEnabled(False)
        self._calibrate_btn.setText("Calibrating...")

        thread = _CalibrateThread(self._calibrate_fn, device_id, device_name, self)
        thread.succeeded.connect(self._on_calibrate_succeeded)
        thread.failed.connect(self._on_calibrate_failed)
        thread.finished.connect(self._on_calibrate_finished)
        self._calib_thread = thread
        thread.start()

    def _on_calibrate_succeeded(self, threshold: float) -> None:
        if self._pending_result is not None:
            return
        self._working.rms_threshold = threshold
        self._rms_spin.setValue(threshold)
        self._rms_label.setText(f"Threshold: {threshold:.1f}")

    def _on_calibrate_failed(self, message: str) -> None:
        if self._pending_result is not None:
            return
        QMessageBox.warning(self, "Calibration Failed", message)

    def _on_calibrate_finished(self) -> None:
        thread = self._calib_thread
        self._calib_thread = None
        self._calibrate_btn.setEnabled(True)
        self._calibrate_btn.setText(_CALIBRATE_LABEL)
        if thread is not None:
            thread.deleteLater()
        if self._pending_result is not None:
            result = self._pending_result
            self._pending_result = None
            super().done(result)

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
        if self._pending_result is not None:
            return
        with log_duration(log, "Settings apply"):
            self._stop_hotkey_recording()
            self._collect()
            if not self._show_validation_issue():
                return
            if not self._save_or_warn():
                return
            log.info("Settings applied")

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def accept(self) -> None:
        """Validate on every accept path (OK button, direct accept() calls)."""
        if self._pending_result is not None:
            return
        self._stop_hotkey_recording()
        self._collect()
        if not self._show_validation_issue():
            return
        if not self._save_or_warn():
            return
        super().accept()

    def reject(self) -> None:
        if self._pending_result is not None:
            return
        self._stop_hotkey_recording()
        super().reject()

    def done(self, result: int) -> None:
        if self._pending_result is not None:
            return
        self._stop_hotkey_recording()
        if self._calib_thread is not None:
            # A result may be queued before finished; keep the dialog alive until
            # the thread ends and ignore results after closing was requested.
            self._pending_result = result
            self._button_box.setEnabled(False)
            self._calibrate_btn.setEnabled(False)
            return
        super().done(result)

    def is_hotkey_capture_active(self) -> bool:
        return self._hotkey_capture.is_recording()

    def _show_validation_issue(self) -> bool:
        issue = next(iter(validate_config(self._working)), None)
        if issue is None:
            return True

        QMessageBox.warning(self, "Missing Configuration", issue.message)
        self._tabs.setCurrentIndex(issue.tab_index)
        if issue.tab_index == 2:
            self._llm_group.setVisible(True)
        return False

    def _save_or_warn(self) -> bool:
        from src.utils import ScreamerError

        try:
            save_config(self._working)
        except ScreamerError as e:
            QMessageBox.warning(self, "Settings Save Failed", e.code.value)
            return False
        self._llm_prompt_baseline = (
            self._working.llm_system_prompt,
            self._working.llm_prompt_origin,
            self._llm_prompt.toPlainText(),
        )
        self.applied.emit()
        return self._sync_startup_or_warn()

    def _sync_startup_or_warn(self) -> bool:
        from src.startup import sync_enabled
        from src.utils import ScreamerError

        if not is_supported():
            return True

        try:
            sync_enabled(self._working.start_with_windows)
            return True
        except ScreamerError as e:
            QMessageBox.warning(
                self,
                "Startup Setting Failed",
                f"Settings were saved, but Windows startup could not be updated.\n{e}",
            )
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
    cancelled = Signal()  # Esc pressed during recording

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self.setText("press the new hotkey…")
        self.setFocus(Qt.OtherFocusReason)
        app = QCoreApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.grabKeyboard()

    def stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        app = QCoreApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
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

    def eventFilter(self, watched, event) -> bool:
        if not self._recording or event.type() != QEvent.Type.MouseButtonPress:
            return False
        code = _mouse_button_to_code(event.button())
        if code is None:
            return False
        event.accept()
        self.captured.emit(Hotkey(_mods_from_qt(event.modifiers()), "mouse", code))
        return True


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
    import platform
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
        print("Settings saved.")
        if platform.system() != "Windows":
            print("Secret values are not persisted outside Windows (DPAPI unavailable).")
    else:
        print("Cancelled.")
