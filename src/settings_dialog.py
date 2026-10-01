"""Settings dialog: General, STT, LLM, Audio, Vocabulary, Rewrite Profiles.

Edits a copy of AppConfig; Apply and OK persist validated settings.
Standalone mode: ``python -m src.settings_dialog`` launches the dialog for testing.
"""

from __future__ import annotations

import copy
import logging
import uuid
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
    QInputDialog,
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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config import (
    AppConfig,
    AppMapping,
    RewriteProfile,
    builtin_rewrite_profiles,
    decode_rewrite_catalog,
    encode_rewrite_catalog,
    migrate_rewrite_catalog,
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
    normalize_vocabulary_entries,
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
    """Settings dialog editing a copy of *config*.

    *devices*: list of ``(device_id, display_name)`` for the Audio tab combo.
        Pass an empty list if audio is unavailable.
    *calibrate_fn*: ``fn(device_id) -> float`` that runs RMS calibration.
        ``None`` disables the calibrate button.
    *refresh_devices_fn*: optional user-triggered input-device enumeration.
    """

    hotkey_capture_active_changed = Signal(bool)
    applied = Signal()
    clear_history_requested = Signal()

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
        self._profile_loading = False
        self._rewrite_error: str | None = None

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
        self._build_vocabulary_tab()
        self._build_profiles_tab()

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

        self._output_mode_combo = QComboBox()
        for value, label in (
            ("type", "Type into target application"),
            ("copy", "Copy to clipboard only"),
            ("copy_and_type", "Copy to clipboard and type"),
        ):
            self._output_mode_combo.addItem(label, value)
        form.addRow("Output mode:", self._output_mode_combo)

        self._recovery_hotkey_input = QLineEdit()
        self._recovery_hotkey_input.setPlaceholderText("ctrl+alt+shift+key:0x56")
        self._recovery_hotkey_input.setToolTip(
            "Canonical keyboard shortcut: modifiers plus key:0x56 (V). "
            "Mouse bindings are not supported. Must differ from the dictation shortcut. "
            "The registered-shortcut conflict check cannot detect other low-level-hook apps."
        )
        form.addRow("Recovery shortcut:", self._recovery_hotkey_input)

        self._history_check = QCheckBox("Save local text history (opt-in)")
        form.addRow(self._history_check)
        disclosure = QLabel(
            "History may contain dictated text, AI prompts you dictate, and spoken secrets. "
            "Saved history is protected under your Windows account, not from software running "
            "as that account. Clipboard contents, other apps, and backups are separate copies. "
            "Deletion has no secure-erasure guarantee. Turning history off leaves existing "
            "saved history until you use Clear History."
        )
        disclosure.setWordWrap(True)
        form.addRow(disclosure)
        self._history_limit_spin = QSpinBox()
        self._history_limit_spin.setRange(1, 100)
        form.addRow("Retain latest results:", self._history_limit_spin)
        self._clear_history_btn = QPushButton("Clear History…")
        self._clear_history_btn.clicked.connect(self._on_clear_history)
        form.addRow(self._clear_history_btn)

        self._startup_check = QCheckBox("Start Screamer with Windows")
        if not is_supported():
            self._startup_check.setEnabled(False)
            self._startup_check.setToolTip("Windows only")
        form.addRow(self._startup_check)

        self._tabs.addTab(tab, "General")

    def _on_clear_history(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Clear History",
                "Delete all saved text history now? This is separate from "
                "Apply/Cancel and cannot be undone. Copies in other apps or backups are not deleted.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.clear_history_requested.emit()

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
        self._stt_prompt_primary_check = QCheckBox("Send vocabulary prompt to primary STT")
        self._stt_prompt_primary_check.setToolTip(
            "Enable only if this endpoint accepts the multipart prompt field. "
            "A rejected prompt follows the normal fallback path, without retrying it removed."
        )
        form.addRow(self._stt_prompt_primary_check)

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
        for key in (self._stt_key, self._stt_fb_key):
            key.setToolTip(
                "Optional: leave blank intentionally for a local endpoint that needs no API key."
            )
        for url in (self._stt_url, self._stt_fb_url):
            url.setToolTip(
                "Enter the base URL, not the full transcription route. "
                "Screamer appends /audio/transcriptions."
            )
        self._stt_prompt_fallback_check = QCheckBox("Send vocabulary prompt to fallback STT")
        self._stt_prompt_fallback_check.setToolTip(self._stt_prompt_primary_check.toolTip())
        fb_form.addRow(self._stt_prompt_fallback_check)

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
        self._llm_prompt.textChanged.connect(self._on_profile_prompt_changed)
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
        if self._working._rewrite_catalog_corrupt_original is not None:
            if not self._confirm_catalog_repair("Reset"):
                return
            self._repair_catalog()
        self._working.llm_system_prompt = DEFAULT_LLM_SYSTEM_PROMPT
        self._working.llm_prompt_origin = "default_v2"
        self._working.rewrite_selection_mode = "manual"
        self._working.rewrite_manual_profile_id = "clean"
        if self._working.rewrite_catalog is None:
            self._working.rewrite_catalog = encode_rewrite_catalog(
                self._rewrite_profiles, self._rewrite_app_mappings
            )
        self._rewrite_error = None
        self._rewrite_mode_combo.setCurrentIndex(0)
        self._rewrite_manual_combo.setCurrentIndex(
            _combo_index(self._rewrite_manual_combo, "clean")
        )
        self._refresh_profile_choices("clean")

    # --- Rewrite profiles (sixth tab; existing tab indexes stay intact) ---

    def _build_profiles_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)
        hint = QLabel(
            "AI rewrite off always uses Raw. Manual selection ignores application mappings. "
            "Automatic selection matches the full executable path, then uses the default. "
            "Raw never calls an LLM. Edit the selected profile's prompt on the LLM tab; "
            "editing Clean creates a custom profile. Limits: 32 custom profiles, "
            "100-character names, 32 KiB UTF-8 prompts."
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        self._rewrite_warning = QLabel(
            "Saved rewrite catalog is damaged. Its exact value is retained during unrelated "
            "saves. Explicitly Reset or Replace to repair; Apply/OK saves the repair, "
            "Cancel discards it. Rewrite resolution is unavailable until repaired."
        )
        self._rewrite_warning.setWordWrap(True)
        form.addRow(self._rewrite_warning)
        self._rewrite_reset_btn = QPushButton("Reset damaged catalog…")
        self._rewrite_replace_btn = QPushButton("Replace damaged catalog…")
        self._rewrite_reset_btn.clicked.connect(lambda: self._on_catalog_repair("Reset"))
        self._rewrite_replace_btn.clicked.connect(lambda: self._on_catalog_repair("Replace"))
        row = QHBoxLayout()
        row.addWidget(self._rewrite_reset_btn)
        row.addWidget(self._rewrite_replace_btn)
        form.addRow(row)
        self._rewrite_mode_combo = QComboBox()
        self._rewrite_mode_combo.addItem("Manual", "manual")
        self._rewrite_mode_combo.addItem("Automatic (application mappings)", "automatic")
        self._rewrite_default_combo = QComboBox()
        self._rewrite_manual_combo = QComboBox()
        form.addRow("Selection mode:", self._rewrite_mode_combo)
        form.addRow("Default profile:", self._rewrite_default_combo)
        form.addRow("Manual profile:", self._rewrite_manual_combo)
        self._rewrite_profile_combo = QComboBox()
        self._rewrite_profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        form.addRow("Profile to edit:", self._rewrite_profile_combo)
        self._rewrite_edit_prompt_btn = QPushButton("Edit selected prompt on LLM tab")
        self._rewrite_edit_prompt_btn.clicked.connect(self._on_edit_profile_prompt)
        form.addRow(self._rewrite_edit_prompt_btn)
        self._rewrite_name_input = QLineEdit()
        form.addRow("Custom profile name:", self._rewrite_name_input)
        self._rewrite_add_btn = QPushButton("Add custom")
        self._rewrite_rename_btn = QPushButton("Rename selected")
        self._rewrite_delete_btn = QPushButton("Delete selected")
        self._rewrite_add_btn.clicked.connect(self._on_profile_add)
        self._rewrite_rename_btn.clicked.connect(self._on_profile_rename)
        self._rewrite_delete_btn.clicked.connect(self._on_profile_delete)
        row = QHBoxLayout()
        for button in (self._rewrite_add_btn, self._rewrite_rename_btn, self._rewrite_delete_btn):
            row.addWidget(button)
        form.addRow(row)
        self._rewrite_mappings = QListWidget()
        self._rewrite_mappings.setMaximumHeight(140)
        self._rewrite_mappings.itemSelectionChanged.connect(self._on_mapping_selected)
        form.addRow("Application mappings:", self._rewrite_mappings)
        self._rewrite_path_input = QLineEdit()
        self._rewrite_path_input.setPlaceholderText(r"C:\Program Files\Application\app.exe")
        self._rewrite_mapping_profile_combo = QComboBox()
        form.addRow("Full executable path:", self._rewrite_path_input)
        form.addRow("Mapped profile:", self._rewrite_mapping_profile_combo)
        self._rewrite_mapping_add_btn = QPushButton("Add mapping")
        self._rewrite_mapping_edit_btn = QPushButton("Edit selected")
        self._rewrite_mapping_remove_btn = QPushButton("Remove selected")
        self._rewrite_mapping_add_btn.clicked.connect(lambda: self._on_mapping_write(False))
        self._rewrite_mapping_edit_btn.clicked.connect(lambda: self._on_mapping_write(True))
        self._rewrite_mapping_remove_btn.clicked.connect(self._on_mapping_remove)
        row = QHBoxLayout()
        for button in (
            self._rewrite_mapping_add_btn,
            self._rewrite_mapping_edit_btn,
            self._rewrite_mapping_remove_btn,
        ):
            row.addWidget(button)
        form.addRow(row)
        self._tabs.addTab(tab, "Profiles")

    def _all_rewrite_profiles(self) -> list[RewriteProfile]:
        return [*builtin_rewrite_profiles(), *self._rewrite_profiles]

    def _populate_profiles(self, cfg: AppConfig) -> None:
        try:
            migrate_rewrite_catalog(cfg)
            self._rewrite_profiles, self._rewrite_app_mappings = decode_rewrite_catalog(
                cfg.rewrite_catalog
            )
        except (TypeError, ValueError):
            self._rewrite_profiles, self._rewrite_app_mappings = [], []
            if cfg.rewrite_catalog is not None:
                cfg._rewrite_catalog_corrupt_original = (cfg.rewrite_catalog,)
        self._rewrite_error = None
        self._rewrite_mode_combo.setCurrentIndex(
            _combo_index(self._rewrite_mode_combo, cfg.rewrite_selection_mode)
        )
        self._rewrite_default_combo.clear()
        self._rewrite_manual_combo.clear()
        for profile in self._all_rewrite_profiles():
            self._rewrite_default_combo.addItem(profile.name, profile.id)
            self._rewrite_manual_combo.addItem(profile.name, profile.id)
        self._rewrite_default_combo.setCurrentIndex(
            _combo_index(self._rewrite_default_combo, cfg.rewrite_default_profile_id)
        )
        self._rewrite_manual_combo.setCurrentIndex(
            _combo_index(self._rewrite_manual_combo, cfg.rewrite_manual_profile_id)
        )
        self._refresh_profile_choices(cfg.rewrite_manual_profile_id)
        # Missing persisted IDs stay invalid and visible as an unselected combo.
        self._rewrite_default_combo.setCurrentIndex(
            _combo_index(self._rewrite_default_combo, cfg.rewrite_default_profile_id)
        )
        self._rewrite_manual_combo.setCurrentIndex(
            _combo_index(self._rewrite_manual_combo, cfg.rewrite_manual_profile_id)
        )
        # A provenance mismatch must remain visible until an explicit edit/reset.
        if cfg.rewrite_catalog is None or self._rewrite_profile_combo.currentIndex() < 0:
            self._profile_editor_id = None
            self._llm_prompt.setReadOnly(False)
            self._show_profile_prompt(cfg.llm_system_prompt, cfg.llm_prompt_origin)
        self._refresh_mapping_list()
        self._update_catalog_damage_ui()

    def _refresh_profile_choices(
        self, selected: str | None = None, *, show_prompt: bool = True
    ) -> None:
        self._profile_loading = True
        combos = (
            self._rewrite_default_combo,
            self._rewrite_manual_combo,
            self._rewrite_profile_combo,
            self._rewrite_mapping_profile_combo,
        )
        for combo in combos:
            old = selected if combo is self._rewrite_profile_combo else combo.currentData()
            combo.clear()
            for profile in self._all_rewrite_profiles():
                combo.addItem(f"{profile.name} [{profile.id}]", profile.id)
            if old is not None:
                combo.setCurrentIndex(_combo_index(combo, old))
            else:
                combo.setCurrentIndex(
                    -1
                    if combo
                    in (
                        self._rewrite_default_combo,
                        self._rewrite_manual_combo,
                    )
                    else 0
                )
        self._profile_loading = False
        if show_prompt:
            self._on_profile_selected()
        else:
            self._profile_editor_id = self._rewrite_profile_combo.currentData()

    def _show_profile_prompt(self, prompt: str, origin: str) -> None:
        self._profile_loading = True
        self._llm_prompt.setPlainText(prompt)
        self._llm_prompt_baseline = (prompt, origin, self._llm_prompt.toPlainText())
        self._profile_loading = False

    def _on_profile_selected(self, *_args) -> None:
        if self._profile_loading:
            return
        profile_id = self._rewrite_profile_combo.currentData()
        profile = next((p for p in self._all_rewrite_profiles() if p.id == profile_id), None)
        self._profile_editor_id = profile_id
        if profile is None:
            return
        self._rewrite_name_input.setText(profile.name)
        self._llm_prompt.setReadOnly(profile.kind == "raw")
        prompt = DEFAULT_LLM_SYSTEM_PROMPT if profile.kind == "clean" else (profile.prompt or "")
        origin = "default_v2" if profile.kind == "clean" else "user_saved"
        if prompt == self._working.llm_system_prompt:
            origin = self._working.llm_prompt_origin
        self._show_profile_prompt(prompt, origin)

    def _on_edit_profile_prompt(self) -> None:
        self._tabs.setCurrentIndex(2)
        self._llm_group.setVisible(True)
        self._llm_prompt.setFocus()

    def _on_profile_prompt_changed(self) -> None:
        if self._profile_loading or not hasattr(self, "_rewrite_profiles"):
            return
        if self._working._rewrite_catalog_corrupt_original is not None:
            return
        original, _origin, displayed = self._llm_prompt_baseline
        prompt = self._llm_prompt.toPlainText()
        if prompt == displayed:
            prompt = original
        profile_id = self._profile_editor_id
        if profile_id == "raw":
            return
        if profile_id == "clean" or profile_id is None:
            if prompt == original:
                return
            profile_id = uuid.uuid4().hex
            profile = RewriteProfile(profile_id, "Custom cleanup", "custom", prompt)
            if len(self._rewrite_profiles) >= 32:
                QMessageBox.warning(
                    self, "Profile Limit", "Choose no more than 32 custom rewrite profiles"
                )
                self._show_profile_prompt(original, _origin)
                return
            self._rewrite_profiles.append(profile)
            self._working.llm_prompt_origin = "user_saved"
            self._working.llm_system_prompt = prompt
            self._working.rewrite_manual_profile_id = profile_id
            self._working.rewrite_selection_mode = "manual"
            self._rewrite_mode_combo.setCurrentIndex(0)
            self._rewrite_manual_combo.addItem(profile.name, profile.id)
            self._rewrite_manual_combo.setCurrentIndex(
                _combo_index(self._rewrite_manual_combo, profile_id)
            )
            # Updating selectors must not replace the editor and discard its undo stack.
            self._refresh_profile_choices(profile_id, show_prompt=False)
        else:
            self._rewrite_profiles = [
                RewriteProfile(p.id, p.name, p.kind, prompt) if p.id == profile_id else p
                for p in self._rewrite_profiles
            ]
            self._working.llm_system_prompt = prompt
            self._working.llm_prompt_origin = _origin if prompt == original else "user_saved"
        if self._working.rewrite_catalog is None:
            # Editing is explicit provenance repair; collection validates this draft.
            self._working.rewrite_catalog = encode_rewrite_catalog([], [])

    def _check_catalog_draft(self, profiles, mappings) -> bool:
        try:
            encode_rewrite_catalog(profiles, mappings)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Rewrite Profiles", str(e))
            return False
        return True

    def _on_profile_add(self) -> None:
        profile = RewriteProfile(uuid.uuid4().hex, self._rewrite_name_input.text(), "custom", "")
        profiles = [*self._rewrite_profiles, profile]
        if not self._check_catalog_draft(profiles, self._rewrite_app_mappings):
            return
        self._rewrite_profiles = profiles
        self._refresh_profile_choices(profile.id)
        self._tabs.setCurrentIndex(2)
        self._llm_group.setVisible(True)
        self._llm_prompt.setFocus()

    def _on_profile_rename(self) -> None:
        profile_id = self._rewrite_profile_combo.currentData()
        if profile_id in ("raw", "clean", None):
            QMessageBox.warning(self, "Built-in Profile", "Built-in profiles cannot be renamed.")
            return
        profiles = [
            RewriteProfile(p.id, self._rewrite_name_input.text(), p.kind, p.prompt)
            if p.id == profile_id
            else p
            for p in self._rewrite_profiles
        ]
        if self._check_catalog_draft(profiles, self._rewrite_app_mappings):
            self._rewrite_profiles = profiles
            self._refresh_profile_choices(profile_id)
            self._refresh_mapping_list()

    def _on_profile_delete(self) -> None:
        profile_id = self._rewrite_profile_combo.currentData()
        if profile_id in ("raw", "clean", None):
            QMessageBox.warning(self, "Built-in Profile", "Built-in profiles cannot be deleted.")
            return
        if profile_id in (
            self._rewrite_default_combo.currentData(),
            self._rewrite_manual_combo.currentData(),
        ) or any(m.profile_id == profile_id for m in self._rewrite_app_mappings):
            QMessageBox.warning(
                self,
                "Profile In Use",
                "Explicitly reassign default/manual selections and all application "
                "mappings before deleting this profile.",
            )
            return
        self._rewrite_profiles = [p for p in self._rewrite_profiles if p.id != profile_id]
        self._refresh_profile_choices("clean")

    def _refresh_mapping_list(self) -> None:
        self._rewrite_mappings.clear()
        names = {p.id: p.name for p in self._all_rewrite_profiles()}
        for mapping in self._rewrite_app_mappings:
            self._rewrite_mappings.addItem(
                f"{mapping.executable_path} → {names[mapping.profile_id]}"
            )

    def _on_mapping_selected(self) -> None:
        row = self._rewrite_mappings.currentRow()
        if row >= 0:
            mapping = self._rewrite_app_mappings[row]
            self._rewrite_path_input.setText(mapping.executable_path)
            self._rewrite_mapping_profile_combo.setCurrentIndex(
                _combo_index(self._rewrite_mapping_profile_combo, mapping.profile_id)
            )

    def _on_mapping_write(self, edit: bool) -> None:
        row = self._rewrite_mappings.currentRow()
        if edit and row < 0:
            return
        mapping = AppMapping(
            self._rewrite_path_input.text(), self._rewrite_mapping_profile_combo.currentData()
        )
        mappings = list(self._rewrite_app_mappings)
        if edit:
            mappings[row] = mapping
        else:
            mappings.append(mapping)
        if self._check_catalog_draft(self._rewrite_profiles, mappings):
            _, self._rewrite_app_mappings = decode_rewrite_catalog(
                encode_rewrite_catalog(self._rewrite_profiles, mappings)
            )
            self._refresh_mapping_list()

    def _on_mapping_remove(self) -> None:
        row = self._rewrite_mappings.currentRow()
        if row >= 0:
            del self._rewrite_app_mappings[row]
            self._refresh_mapping_list()

    def _update_catalog_damage_ui(self) -> None:
        damaged = self._working._rewrite_catalog_corrupt_original is not None
        for widget in (self._rewrite_warning, self._rewrite_reset_btn, self._rewrite_replace_btn):
            widget.setVisible(damaged)
        self._tabs.setTabText(5, "Profiles ⚠" if damaged else "Profiles")
        for widget in (
            self._rewrite_mode_combo,
            self._rewrite_default_combo,
            self._rewrite_manual_combo,
            self._rewrite_profile_combo,
            self._rewrite_name_input,
            self._rewrite_add_btn,
            self._rewrite_edit_prompt_btn,
            self._rewrite_rename_btn,
            self._rewrite_delete_btn,
            self._rewrite_mappings,
            self._rewrite_path_input,
            self._rewrite_mapping_profile_combo,
            self._rewrite_mapping_add_btn,
            self._rewrite_mapping_edit_btn,
            self._rewrite_mapping_remove_btn,
            self._llm_prompt,
        ):
            widget.setEnabled(not damaged)

    def _confirm_catalog_repair(self, action: str) -> bool:
        return (
            QMessageBox.question(
                self,
                f"{action} Damaged Rewrite Catalog",
                f"{action} the damaged catalog? The retained original will be replaced only when "
                "you Apply or OK, and cannot then be recovered by Screamer.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _repair_catalog(self) -> None:
        cfg = self._working
        cfg._rewrite_catalog_corrupt_original = None
        cfg.rewrite_catalog = encode_rewrite_catalog([], [])
        cfg.rewrite_default_profile_id = cfg.rewrite_manual_profile_id = "clean"
        cfg.rewrite_selection_mode = "manual"
        cfg.llm_system_prompt = DEFAULT_LLM_SYSTEM_PROMPT
        cfg.llm_prompt_origin = "default_v2"
        self._populate_profiles(cfg)

    def _on_catalog_repair(self, action: str) -> None:
        if not self._confirm_catalog_repair(action):
            return
        if action == "Replace":
            value, accepted = QInputDialog.getMultiLineText(
                self,
                "Replace Rewrite Catalog",
                "Version-1 profiles and mappings JSON:",
                encode_rewrite_catalog([], []),
            )
            if not accepted:
                return
            try:
                profiles, mappings = decode_rewrite_catalog(value)
            except ValueError as e:
                QMessageBox.warning(self, "Invalid Rewrite Catalog", str(e))
                return
            self._repair_catalog()
            self._working.rewrite_catalog = encode_rewrite_catalog(profiles, mappings)
            self._populate_profiles(self._working)
        else:
            self._repair_catalog()

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

    # --- Vocabulary tab ------------------------------------------------

    def _build_vocabulary_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)
        hint = QLabel(
            "Preferred spellings for names, acronyms, identifiers, and project terms. "
            "These guide AI cleanup, not exact replacements or permission to invent content. "
            "STT receives them only for providers explicitly opted in on the STT tab. "
            "Engineering limits: 128 terms, 128 characters per term, 8,000 characters of "
            "rendered guidance. Blank terms are discarded; duplicate spellings keep the first."
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        self._vocabulary_warning = QLabel(
            "Saved vocabulary is damaged. No terms are being used. The original value is "
            "preserved during unrelated saves. Explicitly Reset or Replace to repair it; "
            "Apply/OK then replaces the original. Cancel discards the repair."
        )
        self._vocabulary_warning.setWordWrap(True)
        form.addRow(self._vocabulary_warning)
        self._vocabulary_reset_btn = QPushButton("Reset damaged vocabulary…")
        self._vocabulary_reset_btn.clicked.connect(lambda: self._on_vocabulary_repair("Reset"))
        self._vocabulary_replace_btn = QPushButton("Replace damaged vocabulary…")
        self._vocabulary_replace_btn.clicked.connect(lambda: self._on_vocabulary_repair("Replace"))
        repairs = QHBoxLayout()
        repairs.addWidget(self._vocabulary_reset_btn)
        repairs.addWidget(self._vocabulary_replace_btn)
        form.addRow(repairs)
        self._vocabulary_list = QListWidget()
        self._vocabulary_list.setMaximumHeight(200)
        self._vocabulary_list.itemSelectionChanged.connect(self._on_vocabulary_selected)
        form.addRow("Preferred terms:", self._vocabulary_list)
        self._vocabulary_input = QLineEdit()
        self._vocabulary_input.setPlaceholderText("One preferred spelling, e.g. PySide6")
        form.addRow("Term:", self._vocabulary_input)
        self._vocabulary_add_btn = QPushButton("Add")
        self._vocabulary_add_btn.clicked.connect(self._on_vocabulary_add)
        self._vocabulary_edit_btn = QPushButton("Edit selected")
        self._vocabulary_edit_btn.clicked.connect(self._on_vocabulary_edit)
        self._vocabulary_remove_btn = QPushButton("Remove selected")
        self._vocabulary_remove_btn.clicked.connect(self._on_vocabulary_remove)
        buttons = QHBoxLayout()
        for button in (
            self._vocabulary_add_btn,
            self._vocabulary_edit_btn,
            self._vocabulary_remove_btn,
        ):
            buttons.addWidget(button)
        form.addRow(buttons)
        self._tabs.addTab(tab, "Vocabulary")

    def _vocabulary_values(self) -> list[str]:
        return [self._vocabulary_list.item(i).text() for i in range(self._vocabulary_list.count())]

    def _update_vocabulary_damage_ui(self) -> None:
        damaged = self._working._vocabulary_corrupt_original is not None
        self._vocabulary_warning.setVisible(damaged)
        self._vocabulary_reset_btn.setVisible(damaged)
        self._vocabulary_replace_btn.setVisible(damaged)
        self._tabs.setTabText(4, "Vocabulary ⚠" if damaged else "Vocabulary")
        for widget in (
            self._vocabulary_list,
            self._vocabulary_input,
            self._vocabulary_add_btn,
            self._vocabulary_edit_btn,
            self._vocabulary_remove_btn,
        ):
            widget.setEnabled(not damaged)

    def _confirm_vocabulary_repair(self, action: str) -> bool:
        return (
            QMessageBox.question(
                self,
                f"{action} Damaged Vocabulary",
                f"{action} the damaged vocabulary? The retained original will be replaced when "
                "you Apply or OK. It cannot be recovered by Screamer after that save.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _on_vocabulary_repair(self, action: str) -> None:
        if not self._confirm_vocabulary_repair(action):
            return
        self._working._vocabulary_corrupt_original = None
        self._working.vocabulary_entries = []
        self._vocabulary_list.clear()
        self._vocabulary_input.clear()
        self._update_vocabulary_damage_ui()
        if action == "Replace":
            self._vocabulary_input.setFocus()

    def _on_vocabulary_selected(self) -> None:
        item = self._vocabulary_list.currentItem()
        if item is not None:
            self._vocabulary_input.setText(item.text())

    def _set_vocabulary_values(self, entries: list[str]) -> None:
        try:
            normalized = normalize_vocabulary_entries(entries)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Vocabulary", str(e))
            self._vocabulary_input.setFocus()
            return
        self._vocabulary_list.clear()
        self._vocabulary_list.addItems(normalized)
        self._vocabulary_input.clear()

    def _on_vocabulary_add(self) -> None:
        self._set_vocabulary_values([*self._vocabulary_values(), self._vocabulary_input.text()])

    def _on_vocabulary_edit(self) -> None:
        row = self._vocabulary_list.currentRow()
        if row < 0:
            return
        entries = self._vocabulary_values()
        entries[row] = self._vocabulary_input.text()
        self._set_vocabulary_values(entries)

    def _on_vocabulary_remove(self) -> None:
        row = self._vocabulary_list.currentRow()
        if row >= 0:
            self._vocabulary_list.takeItem(row)
            self._vocabulary_input.clear()

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
        self._output_mode_combo.setCurrentIndex(
            _combo_index(self._output_mode_combo, cfg.output_mode)
        )
        self._recovery_hotkey_input.setText(cfg.recovery_hotkey)
        self._history_check.setChecked(cfg.history_enabled)
        self._history_limit_spin.setValue(cfg.history_limit)

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
        self._stt_prompt_primary_check.setChecked(cfg.stt_prompt_primary_enabled)
        self._stt_prompt_fallback_check.setChecked(cfg.stt_prompt_fallback_enabled)
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
        self._populate_profiles(cfg)
        self._llm_fb_check.setChecked(cfg.llm_fallback_enabled)
        self._llm_fb_key.setText(cfg.llm_fallback_api_key)
        self._llm_fb_url.setText(cfg.llm_fallback_base_url)
        self._llm_fb_model.setText(cfg.llm_fallback_model)
        self._llm_fb_headers.setText(cfg.llm_fallback_custom_headers)

        # Audio
        self._select_device(cfg.audio_device_id, cfg.audio_device_name)
        self._rms_spin.setValue(cfg.rms_threshold)
        self._rms_label.setText(f"Threshold: {cfg.rms_threshold:.1f}")
        self._vocabulary_list.clear()
        self._vocabulary_list.addItems(cfg.vocabulary_entries)
        self._vocabulary_input.clear()
        self._update_vocabulary_damage_ui()

    def _collect(self) -> None:
        """Write widget values back into self._working."""
        cfg = self._working

        # General
        if self._captured_hotkey is not None:
            cfg.hotkey = self._captured_hotkey.to_canonical()
        cfg.recording_mode = "toggle" if self._mode_toggle.isChecked() else "hold"
        cfg.post_type_key = self._post_key_combo.currentData()
        cfg.start_with_windows = self._startup_check.isChecked()
        cfg.output_mode = self._output_mode_combo.currentData()
        cfg.recovery_hotkey = self._recovery_hotkey_input.text().strip()
        cfg.history_enabled = self._history_check.isChecked()
        cfg.history_limit = self._history_limit_spin.value()
        if cfg._vocabulary_corrupt_original is None:
            cfg.vocabulary_entries = self._vocabulary_values()

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
        cfg.stt_prompt_primary_enabled = self._stt_prompt_primary_check.isChecked()
        cfg.stt_prompt_fallback_enabled = self._stt_prompt_fallback_check.isChecked()
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
        self._rewrite_error = None
        if cfg._rewrite_catalog_corrupt_original is None:
            if cfg.rewrite_catalog is not None:
                try:
                    cfg.rewrite_catalog = encode_rewrite_catalog(
                        self._rewrite_profiles, self._rewrite_app_mappings
                    )
                except ValueError as e:
                    self._rewrite_error = str(e)
            cfg.rewrite_default_profile_id = self._rewrite_default_combo.currentData()
            cfg.rewrite_manual_profile_id = self._rewrite_manual_combo.currentData()
            cfg.rewrite_selection_mode = self._rewrite_mode_combo.currentData()
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
            if self._rewrite_error is not None:
                self._show_validation_issue()
                return
            self._working = import_from_env(self._working)
            self._populate(self._working)
            log.info("Imported .env values into settings")

    def _on_reset(self) -> None:
        """Reset all fields to defaults."""
        if self._working._rewrite_catalog_corrupt_original is not None:
            if not self._confirm_catalog_repair("Reset"):
                return
        if self._working._vocabulary_corrupt_original is not None:
            if not self._confirm_vocabulary_repair("Reset"):
                return
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
        if self._rewrite_error is not None:
            QMessageBox.warning(self, "Invalid Rewrite Profiles", self._rewrite_error)
            self._tabs.setCurrentIndex(5)
            return False
        # A retained damaged catalog is still a validation issue at the public boundary,
        # but must not prevent an unrelated settings save from preserving its source.
        issue = next(
            (
                issue
                for issue in validate_config(self._working)
                if not (
                    issue.tab_index == 5
                    and self._working._rewrite_catalog_corrupt_original is not None
                )
            ),
            None,
        )
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
            self._working.vocabulary_entries = normalize_vocabulary_entries(
                self._working.vocabulary_entries
            )
            recovery = Hotkey.parse(self._working.recovery_hotkey)
            if recovery is not None:
                self._working.recovery_hotkey = recovery.to_canonical()
            save_config(self._working)
        except ScreamerError as e:
            QMessageBox.warning(self, "Settings Save Failed", e.code.value)
            return False
        except ValueError as e:
            QMessageBox.warning(self, "Settings Save Failed", str(e))
            return False
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
