"""On-demand plain-text recovery UI; main owns actions and records."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from src import theme
from src.results import DictationRecord

# Visual emphasis only: the primary copy action and the destructive actions.
_BUTTON_ROLES = {
    "Copy final": "primary",
    "Delete": "danger",
    "Clear all": "danger",
    "Discard audio": "danger",
}


def _styled(label: QLabel, role: str, font: QFont) -> QLabel:
    theme.set_role(label, role)
    label.setFont(font)
    return label


def _section(text: str) -> QLabel:
    return _styled(QLabel(text.upper()), "section", theme.ui_font(11, QFont.Weight.DemiBold, 8.0))


class RecoveryDialog(QDialog):
    copy_requested = Signal(object, str)
    select_requested = Signal(object, str)
    rewrite_requested = Signal(object)
    delete_requested = Signal(object)
    clear_requested = Signal()
    retry_requested = Signal()
    discard_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Screamer — History")
        self.resize(940, 660)
        self._records = []
        self._candidate_id = None
        self._busy = False
        self._enabled = False
        self._pending = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 22)
        layout.setSpacing(0)
        heading = theme.set_role(QLabel("History"), "title")
        heading.setFont(theme.ui_font(22, QFont.Weight.DemiBold, -1.0))
        layout.addWidget(heading)
        layout.addSpacing(6)
        self._notice = _styled(QLabel(), "hint", theme.ui_font(12))
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setWordWrap(True)
        layout.addWidget(self._notice)
        layout.addSpacing(22)

        body = QHBoxLayout()
        body.setSpacing(24)
        layout.addLayout(body, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(_section("Dictations"))
        self._list = QListWidget()
        self._list.setFixedWidth(272)
        self._list.setFont(theme.mono_font(12))
        self._list.currentRowChanged.connect(self._select)
        side.addWidget(self._list, 1)
        body.addLayout(side)

        detail = QVBoxLayout()
        detail.setSpacing(8)
        body.addLayout(detail, 1)
        self._metadata = _styled(QLabel(), "meta", theme.mono_font(12))
        self._metadata.setTextFormat(Qt.TextFormat.PlainText)
        self._metadata.setWordWrap(True)
        detail.addWidget(_section("Details"))
        detail.addWidget(self._metadata)
        self._raw = QPlainTextEdit()
        self._final = QPlainTextEdit()
        for title, editor in (("Raw transcript", self._raw), ("Result", self._final)):
            detail.addSpacing(10)
            detail.addWidget(_section(title))
            editor.setReadOnly(True)
            editor.setFont(theme.ui_font(14))
            detail.addWidget(editor, 1)
        detail.addSpacing(4)

        layout.addSpacing(18)
        divider = QFrame()
        divider.setObjectName("hushDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(14)
        self._buttons = {}
        for actions in (
            (
                ("Copy raw", lambda: self._emit_text(self.copy_requested, "raw")),
                ("Copy final", lambda: self._emit_text(self.copy_requested, "final")),
                ("Use raw for insertion", lambda: self._emit_text(self.select_requested, "raw")),
                (
                    "Use result for insertion",
                    lambda: self._emit_text(self.select_requested, "final"),
                ),
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
            row.setSpacing(8)
            layout.addLayout(row)
            layout.addSpacing(8)
            for title, callback in actions:
                button = QPushButton(title)
                button.clicked.connect(callback)
                if title in _BUTTON_ROLES:
                    theme.set_role(button, _BUTTON_ROLES[title])
                row.addWidget(button)
                self._buttons[title] = button
            row.addStretch()
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
            self._metadata.setText(
                f"Language: {record.language or 'Auto'}\n"
                f"Application: {record.target_executable or 'Unavailable'}\n"
                + (
                    "Cleanup failed — raw text is available."
                    if record.rewrite_status == "failed"
                    else ""
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
        self._buttons["Retry STT"].setVisible(self._pending)
        self._buttons["Discard audio"].setVisible(self._pending)

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
        insertion_selection: tuple[DictationRecord, str] | None = None,
    ) -> None:
        selected = self.selected_record()
        self._candidate_id = candidate_id
        self._busy = busy
        self._enabled = enabled
        self._pending = pending
        self._records = records
        self._list.clear()
        for record in records:
            suffix = " — cleaned result" if record.id == candidate_id else ""
            self._list.addItem(record.created_at + suffix)
        index = next(
            (i for i, item in enumerate(records) if selected and item.id == selected.id), 0
        )
        if records:
            self._list.setCurrentRow(index)
        else:
            self._select(-1)
        notice = (
            "Encrypted history enabled. "
            if history_enabled
            else "Disk history off: text stays in memory. "
        )
        if insertion_selection is not None:
            record, text = insertion_selection
            variant = "Raw text" if text == record.raw_text else "Result"
            notice += f"{variant} selected for insertion from {record.created_at}. "
        else:
            notice += "Choose Use result (or raw). Without a selection, insertion uses the latest result. "
        notice += (
            f"Switch to the target app and press {shortcut}."
            if shortcut
            else "Set the Insert last shortcut in Settings, then switch to the target app to use it."
        )
        self._notice.setText(notice)
        self._update_button_eligibility()
