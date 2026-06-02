"""Shared utilities: error codes, exception type, signal bridge, constants."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Signal


APP_NAME = "Screamer"
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)


class AppError(Enum):
    MIC_UNAVAILABLE = "No microphone detected. Check your audio settings."
    MIC_DISCONNECTED = "Microphone disconnected during recording."
    STT_FAILED = "Transcription failed. Check your API key and internet."
    STT_FALLBACK_USED = "Primary STT failed. Used fallback provider."
    LLM_FAILED = "AI rewrite failed. Using raw transcription."
    NETWORK_ERROR = "Network error. Please check your connection."
    NO_SPEECH = "No speech detected. Try speaking louder or closer."
    INJECTION_FAILED = "Could not type text. Focus may have changed."
    HOTKEY_CONFLICT = "Hotkey conflict. Choose a different hotkey."
    UNSUPPORTED_PLATFORM = "This feature is only available on Windows."
    KEY_STORAGE_FAILED = "Could not save or load API keys securely."
    AUTOSTART_FAILED = "Could not update Windows startup setting."


class ScreamerError(Exception):
    """Application error carrying an ``AppError`` code and optional detail."""

    def __init__(self, code: AppError, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code.value)


class SignalBridge(QObject):
    """Thread-safe bridge for dispatching events to the Qt main thread."""

    hotkey_pressed = Signal()
    hotkey_released = Signal()
    error_occurred = Signal(AppError)


@dataclass
class PipelineResult:
    """Result from an STT or rewrite call, carrying text and any warnings."""

    text: str
    warnings: list[AppError] = field(default_factory=list)
