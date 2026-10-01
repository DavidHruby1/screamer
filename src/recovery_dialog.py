"""On-demand plain-text recovery UI; main owns actions and records."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from src.results import DictationRecord


class RecoveryDialog(QDialog):
    copy_requested = Signal(object, str)
    arm_requested = Signal(object, str)
    rewrite_requested = Signal(object)
    delete_requested = Signal(object)
    clear_requested = Signal()
    retry_requested = Signal()
    discard_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Screamer — Recovery")
        self.resize(760, 620)
        self._records = []
        self._candidate_id = None
        self._busy = False
        self._enabled = False
        self._pending = False
        layout = QVBoxLayout(self)
        self._notice = QLabel()
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setWordWrap(True)
        layout.addWidget(self._notice)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._select)
        layout.addWidget(self._list)
        self._metadata = QLabel()
        self._metadata.setTextFormat(Qt.TextFormat.PlainText)
        self._metadata.setWordWrap(True)
        layout.addWidget(self._metadata)
        self._raw = QPlainTextEdit()
        self._final = QPlainTextEdit()
        for title, editor in (("Raw transcript", self._raw), ("Final / candidate", self._final)):
            layout.addWidget(QLabel(title))
            editor.setReadOnly(True)
            layout.addWidget(editor)
        self._attempt = QLabel()
        self._attempt.setTextFormat(Qt.TextFormat.PlainText)
        self._attempt.setWordWrap(True)
        layout.addWidget(self._attempt)
        self._buttons = {}
        for actions in (
            (
                ("Copy raw", lambda: self._emit_text(self.copy_requested, "raw")),
                ("Copy final", lambda: self._emit_text(self.copy_requested, "final")),
                ("Arm raw", lambda: self._emit_text(self.arm_requested, "raw")),
                ("Arm final", lambda: self._emit_text(self.arm_requested, "final")),
            ),
            (
                ("Rerun cleanup", lambda: self._emit_record(self.rewrite_requested)),
                ("Delete", lambda: self._emit_record(self.delete_requested)),
                ("Clear all", self.clear_requested.emit),
                ("Retry STT", self.retry_requested.emit),
                ("Discard audio", self.discard_requested.emit),
            ),
        ):
            row = QHBoxLayout()
            layout.addLayout(row)
            for title, callback in actions:
                button = QPushButton(title)
                button.clicked.connect(callback)
                row.addWidget(button)
                self._buttons[title] = button
        self._select(-1)

    def selected_record(self) -> DictationRecord | None:
        index = self._list.currentRow()
        return self._records[index] if 0 <= index < len(self._records) else None

    def _emit_record(self, signal) -> None:
        record = self.selected_record()
        if record is not None:
            signal.emit(record)

    def _emit_text(self, signal, variant: str) -> None:
        record = self.selected_record()
        if record is not None:
            signal.emit(record, variant)

    def _select(self, _index: int) -> None:
        record = self.selected_record()
        self._raw.setPlainText(record.raw_text if record else "")
        self._final.setPlainText(record.final_text if record else "")
        if record is None:
            self._metadata.setText("")
        else:
            candidate = (
                "Cleanup candidate preview (persistence not verified)\n"
                if record.id == self._candidate_id
                else ""
            )
            warnings = (
                "; ".join(f"{warning.name}: {warning.value}" for warning in record.warnings)
                or "None"
            )
            self._metadata.setText(
                candidate
                + f"Language: {record.language or 'Auto'}\n"
                + f"Application: {record.target_executable or 'Unavailable'}\n"
                + f"Profile ID: {record.profile_id or 'None'}\n"
                + f"Rewrite status: {record.rewrite_status}\n"
                + f"Pipeline warnings: {warnings}"
            )
        attempt = record.last_delivery if record else None
        self._attempt.setText(
            (
                f"Last recorded attempt at {attempt.attempted_at} (not verified delivery): "
                + f"{attempt.mode}; copied={attempt.copied}; text={attempt.text_state}; "
                f"events={attempt.text_events_submitted}; post-key={attempt.post_key_state}; "
                f"reason={attempt.reason or 'none'}"
                if attempt
                else "No attempt recorded"
            )
        )
        self._update_button_eligibility()

    def _update_button_eligibility(self) -> None:
        has_selection = self.selected_record() is not None
        for button in self._buttons.values():
            button.setEnabled(has_selection and not self._busy and self._enabled)
        self._buttons["Clear all"].setEnabled(not self._busy)
        self._buttons["Retry STT"].setEnabled(self._pending and not self._busy and self._enabled)
        self._buttons["Discard audio"].setEnabled(self._pending and not self._busy)

    def refresh(
        self,
        records: list[DictationRecord],
        *,
        history_enabled: bool,
        pending: bool,
        busy: bool,
        enabled: bool,
        shortcut: str,
        candidate_id: str | None = None,
    ) -> None:
        selected = self.selected_record()
        self._candidate_id = candidate_id
        self._busy = busy
        self._enabled = enabled
        self._pending = pending
        self._records = records
        self._list.clear()
        for record in records:
            suffix = (
                " — cleanup candidate preview (persistence not verified)"
                if record.id == candidate_id
                else ""
            )
            self._list.addItem(record.created_at + suffix)
        index = next(
            (i for i, item in enumerate(records) if selected and item.id == selected.id), 0
        )
        if records:
            self._list.setCurrentRow(index)
        else:
            self._select(-1)
        self._notice.setText(
            (
                "Encrypted history enabled. "
                if history_enabled
                else "History disabled: only RAM recovery is shown. Existing saved records remain until Clear. "
            )
            + f"Arm hides this window; focus an external app and press {shortcut}. "
            "Clipboard copies can expose text to other apps. Deletion does not securely erase backups."
        )
        self._update_button_eligibility()
