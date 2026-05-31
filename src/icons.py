"""Embedded 32x32 PNG icons for tray states: idle, recording, processing."""

from __future__ import annotations

import base64
from enum import Enum

from PySide6.QtGui import QPixmap


class TrayState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


# Embedded base64 PNG data (32x32 RGBA) for each tray state.
_EMBEDDED_PNG: dict[TrayState, bytes] = {
    TrayState.IDLE: base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAR0lEQVR42u3WwQkAIAzF0O7P31kX"
        "8NBLDWICnvtAKK0ye7Ek6/QECBAgQIAAbPAVSHf4GAIH4F/QRSCXEX6aCcA34B+LyKbaY7Gl8Ci"
        "VH8wAAAAASUVORK5CYII="
    ),
    TrayState.RECORDING: base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAASklEQVR42mNgGAWjYCiCOxoa/7Hh"
        "UQeMOmDUAaMOGHXAqAMGzGK6OIRYy2nmiAF3wIBHAbGOGJCW0YA3zUYdMOAl4MgoiEYBrQAAaota"
        "8JSNLOoAAAAASUVORK5CYII="
    ),
    TrayState.PROCESSING: base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAASklEQVR42mNgGAWjYCiCO1tE/mPD"
        "ow4YdcCoA0YdMOqAUQcMmMV0cQixltPMEQPugAGPAmIdMSAtowFvmo06YMBLwJFREI0CWgEA4LGl"
        "8JJpBiYAAAAASUVORK5CYII="
    ),
}


def get_icon_pixmap(state: TrayState) -> QPixmap:
    """32x32 QPixmap from embedded base64 PNG data."""
    pixmap = QPixmap()
    pixmap.loadFromData(_EMBEDDED_PNG[state])
    return pixmap


def get_icon_bytes(state: TrayState) -> bytes:
    """Raw PNG bytes for testing without Qt."""
    return _EMBEDDED_PNG[state]


if __name__ == "__main__":
    import os

    out_dir = os.path.join(os.path.dirname(__file__), "..")
    for state in TrayState:
        fname = f"icon_{state.value}.png"
        path = os.path.join(out_dir, fname)
        with open(path, "wb") as f:
            f.write(get_icon_bytes(state))
        print(f"Wrote {path} ({len(get_icon_bytes(state))} bytes)")
