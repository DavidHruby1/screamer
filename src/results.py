"""Immutable RAM records and the single-writer, encrypted history boundary."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.utils import APP_DIR, AppError, ScreamerError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DeliveryAttempt:
    attempted_at: str
    mode: str
    copied: bool = False
    text_state: str = "not_attempted"
    text_events_submitted: int | None = None
    post_key_state: str = "not_requested"
    reason: str | None = None


@dataclass(frozen=True)
class DictationRecord:
    id: str
    created_at: str
    raw_text: str
    rewritten_text: str | None = None
    rewrite_status: str = "not_requested"
    warnings: tuple[AppError, ...] = ()
    language: str = ""
    target_executable: str | None = None
    profile_id: str | None = None
    last_delivery: DeliveryAttempt | None = None

    @property
    def final_text(self) -> str:
        if self.rewrite_status == "succeeded" and self.rewritten_text is not None:
            return self.rewritten_text
        return self.raw_text


_REASONS = {
    None,
    "target_changed",
    "no_target",
    "suppressed",
    "clipboard_failed",
    "injection_failed",
    "post_key_failed",
    "post_key_skipped",
    "manual_copy",
}


def _string(value: object, *, optional: bool = False, nonempty: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or len(value) > 100_000 or (nonempty and not value.strip()):
        raise ValueError("Invalid string")


def _timestamp(value: object) -> None:
    _string(value, nonempty=True)
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("Expected UTC timestamp")


def _decode_record(value: object) -> DictationRecord:
    if not isinstance(value, dict) or set(value) != set(DictationRecord.__dataclass_fields__):
        raise ValueError("Invalid record fields")
    data = dict(value)
    _string(data["id"], nonempty=True)
    _timestamp(data["created_at"])
    _string(data["raw_text"], nonempty=True)
    _string(data["rewritten_text"], optional=True, nonempty=True)
    for key in ("target_executable", "profile_id"):
        _string(data[key], optional=True)
    _string(data["language"])
    if data["rewrite_status"] not in {
        "not_requested",
        "pending",
        "succeeded",
        "failed",
        "cancelled",
    }:
        raise ValueError("Invalid rewrite status")
    if data["rewrite_status"] == "succeeded" and data["rewritten_text"] is None:
        raise ValueError("Missing rewritten text")
    warnings = data["warnings"]
    if not isinstance(warnings, list) or len(warnings) > len(AppError):
        raise ValueError("Invalid warnings")
    data["warnings"] = tuple(AppError[name] for name in warnings)
    attempt = data["last_delivery"]
    if attempt is not None:
        if not isinstance(attempt, dict) or set(attempt) != set(
            DeliveryAttempt.__dataclass_fields__
        ):
            raise ValueError("Invalid attempt fields")
        _timestamp(attempt["attempted_at"])
        if attempt["mode"] not in {"type", "copy", "copy_and_type", "manual"}:
            raise ValueError("Invalid output mode")
        if type(attempt["copied"]) is not bool:
            raise ValueError("Invalid copied flag")
        if attempt["text_state"] not in {
            "not_attempted",
            "withheld",
            "events_submitted",
            "reported_failed",
        }:
            raise ValueError("Invalid text state")
        count = attempt["text_events_submitted"]
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("Invalid event count")
        if attempt["post_key_state"] not in {
            "not_requested",
            "submitted",
            "skipped",
            "reported_failed",
        }:
            raise ValueError("Invalid post-key state")
        if attempt["reason"] not in _REASONS:
            raise ValueError("Invalid reason")
        data["last_delivery"] = DeliveryAttempt(**attempt)
    return DictationRecord(**data)


class HistoryStore:
    """Disk methods are explicit: callers never invoke ordinary reads when disabled.

    Every mutation reads the committed file first. Failure cannot turn a damaged
    file into an empty history; only clear() intentionally bypasses decoding.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path(APP_DIR) / "history.enc"

    def load(self) -> list[DictationRecord]:
        """Validate the complete file, then expose only the recent 100 entries."""
        records = self._load_records()
        records.sort(key=lambda item: datetime.fromisoformat(item.created_at))
        return records[-100:]

    def _load_records(self) -> list[DictationRecord]:
        try:
            try:
                size = self.path.stat().st_size
            except FileNotFoundError:
                return []
            from src.config import unprotect_bytes

            # A bounded plaintext schema also bounds reasonable ciphertext size.
            if size > 450_000_000:
                raise ValueError("History too large")
            payload = json.loads(unprotect_bytes(self.path.read_bytes()).decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"version", "entries"}:
                raise ValueError("Invalid envelope")
            if type(payload["version"]) is not int or payload["version"] != 1:
                raise ValueError("Unsupported history version")
            entries = payload["entries"]
            # Legacy oversized collections are readable, but parsing remains
            # bounded independently of the 100-entry retention policy.
            if not isinstance(entries, list) or len(entries) > 10_000:
                raise ValueError("Invalid entry count")
            records = [_decode_record(entry) for entry in entries]
            if len({record.id for record in records}) != len(records):
                raise ValueError("Duplicate IDs")
            return records
        except Exception as error:
            raise ScreamerError(AppError.HISTORY_STORAGE_FAILED) from error

    def _write(self, records: list[DictationRecord]) -> None:
        temporary = None
        try:
            from src.config import protect_bytes

            entries = []
            for record in records:
                data = asdict(record)
                data["warnings"] = [warning.name for warning in record.warnings]
                _decode_record(data)
                entries.append(data)
            if len(entries) > 100:
                raise ValueError("Too many records")
            plaintext = json.dumps({"version": 1, "entries": entries}, ensure_ascii=False).encode(
                "utf-8"
            )
            ciphertext = protect_bytes(plaintext)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=".history-", delete=False
            ) as stream:
                temporary = stream.name
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except Exception as error:
            raise ScreamerError(AppError.HISTORY_STORAGE_FAILED) from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def upsert(self, record: DictationRecord, limit: int) -> list[DictationRecord]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        records = [item for item in self._load_records() if item.id != record.id]
        records.append(record)
        records.sort(key=lambda item: datetime.fromisoformat(item.created_at))
        records = records[-limit:]
        self._write(records)
        return records

    def delete(self, record_id: str, limit: int = 100) -> list[DictationRecord]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        records = [item for item in self._load_records() if item.id != record_id]
        records.sort(key=lambda item: datetime.fromisoformat(item.created_at))
        records = records[-limit:]
        self._write(records)
        return records

    def trim(self, limit: int) -> list[DictationRecord]:
        """Return a preference-limited view without mutating the committed file.

        Retention is committed by explicit upsert/delete or preference Apply,
        never by a view.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        return self.load()[-limit:]

    def commit_retention(self, limit: int) -> list[DictationRecord]:
        """Explicit preference Apply: validate all records, commit only if needed."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        records = self._load_records()
        records.sort(key=lambda item: datetime.fromisoformat(item.created_at))
        if len(records) > limit:
            records = records[-limit:]
            self._write(records)
        return records

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            raise ScreamerError(AppError.HISTORY_STORAGE_FAILED) from error
