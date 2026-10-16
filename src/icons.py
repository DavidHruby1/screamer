"""Embedded 64x64 PNG icons for tray states: idle, recording, processing."""

from __future__ import annotations

import base64
from enum import Enum

from PySide6.QtGui import QPixmap


class TrayState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


# Embedded base64 PNG data (64x64 RGBA, the Hush waveform mark) for each tray state.
_EMBEDDED_PNG: dict[TrayState, bytes] = {
    TrayState.IDLE: base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAACXBIWXMAAA7EAAAOxAGVKw4bAAACM0lE"
        "QVR4nO2asWobQRCG/zmldHCR8lTkOhkXATcqLfA7mBQCByNi/BTLPYWwMMIiDgSV6Q1S6QcwdqfGeoKo"
        "lSaFL+msXaHR7B6ar/7Z/fVrd2ZgDzAMwzAMY18h7Q2dc1mzaPWY6RKEYwAA44mIh6+zl9uyLFeaflQD"
        "6I/GeYNX9wA670gmS8q61xfncy1PDa2NnHPZ4eGn3yDqrJF9zsAnJ1+Of0ynU9bwlWlsAgDNotUD0WmA"
        "tNMsWr2dG6pQC4CZLneh3Ra1AP4XPGntlnzQ2gjAgaRWqpvonQBB+qNxnhdHDwy6AaGNt8AOQGgz6CYv"
        "jh76o3EeslbtAnDOZY3V8ifeb6UA0Gnw6t455/19tQtAupsE14BUJrhq/3AtMFinCToBkndua4S7iTcA"
        "6TsngGg38RpOdYKTwhtAqhOcFP4jm+gEJ0XInRW9c6lRuzlAGgsgtoHYWACxDcTGAohtIDYWQGwDsbEA"
        "YhuIjQUQ20BsLIDYBmITEsBig/U20SaBPwDGU/Bqm2gTwRsAEQ9DF9tEmwreAF5nL7cAJgFrTSptrfAG"
        "UJblaklZF+tDmCwp6yq9DonWpKAucH1xPp/Pns8IfAXGY7XwAoxHAl/NZ89nat/1CNek4LfB6t8dwPPW"
        "tmuIeMigdqjWp6ndHCBdk2oXgHRNql0AgGxNUvtQcnD36w/CX44W3799/bhLP//QOwGJTpRqAaQ6UaoF"
        "kOpEufcfS6t2gaQmSsMwDMMw9p6/7sQDg8/K92sAAAAASUVORK5CYII="
    ),
    TrayState.RECORDING: base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAB/0lE"
        "QVR4nO2aMW7CQBBF/1qBipyAI6RzQxkkWnIDKisSdwiF5YLcAYSoyAmgRaJNQ8cROEGoiORJgekIHoth"
        "di3mVRYa7X6+d2dH3gEMwzAMw3hUnPaElKYRfrfvACWAeyl+3QFujkY8c1mWa+pRNYBG/TZyt4BD93IA"
        "Noho4MarvZamSGsiStMI5L7+/fMA4NBF7haUpmq61CY6LXu8lsY5dItYFfQMACX3ib0NRQPOCU869jae"
        "tCYC0JKMlTpNFFeAHDTqt3HcrgFMANfBybBW8TzBcbumUb/NGat2BkifJrUzQPo0YeeAcCo4Svj1GyUA"
        "ptciWCtAcs/djuxpUmpAgBWc6GlSLjjQCk4KxhsLs4KTgmFAmBWcFJw9K7rnQqN+dYAwZoBvAb4xA3wL"
        "8I0Z4FuAb8wA3wJ8Ywb4FuAbM8C3AN+YAb4F+IZjwKHCeFVig4DzSWzHH65KbBhwPonN+cNViQ2DcgMa"
        "8QyETWkcYYNGPBPQpEqpAS7LckQ0uGrCubVF53ZINCexTgE3Xu3RjHsAhgB9FwMfiuchmnFPr69HNiex"
        "7waLtzvFxbu2JV/Tzbg5gE6F2KvUrw4Qzkm1M0A6J9XOAEA2J6k1StLH2w/4N0cH97l8vqeeM5ptckFW"
        "lJptckFWlHoGBFpRWrO01kRAaBWlYRiGYRiPzh8F89p1tUggvgAAAABJRU5ErkJggg=="
    ),
    TrayState.PROCESSING: base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAB/UlE"
        "QVR4nO2aMWpCQRCG/3laWgoWHiEHEDQQwWtYBcFDWIiFh1DEyhxDsFDBA3gEiweWlvomRR6k0t0XJ7P7"
        "cL4yDLuTz30zA7uAYRiGYRivCmlvOGZOsm06YMIngLf8z0diLJP3xmJClGnmoypgtD83KbuuAHTvhGw4"
        "qfan7fpJK6dEa6Mxc0J8/cL9fx4AupRdV2NmtbzUNsq26QCMD4/QbrZNB/+eUI6agPybF499FjUB+C14"
        "0rFPUdXaCEBNMlaqm2ieADFG+3PztkvXTJgBaOFHWA1Aiwmz2y5dj/bnps9apRMg3U1KJ0C6m3jXgFgm"
        "uD90k/mjGK8TIPnNCSDaTZwCIpzgRLuJM+FYJzgpnAJineCk8DmyUU5wUvgIEP3mYqN0c4A0JiB0AqEx"
        "AaETCI0JCJ1AaExA6ARCYwJCJxAaExA6gdCYgNAJhMZHwKXAekVio8BHwLHAekVio8ApgBhL38WKxMaC"
        "++rovbEAsPFYa5PHlgqngAlRxkm1j8cSNpxU+0q3Q6I1yasLTNv1U6XT6BFjCOCQL3wBcCDGsNJp9BTf"
        "9YjWJO+7wfzXncNx1/bfEGPJhJZvrCumdHOAdE0qnQDpmlQ6AYBsTdJ8I3SB/82Rs3pL1STNExDlRKkm"
        "INaJUu9JaqQTpZqACCdKAPZc3jAMwzCMF+YbTHPprRi3kvgAAAAASUVORK5CYII="
    ),
}


# Decoded pixmaps, built lazily once a QApplication exists and reused thereafter.
# The icons are static, so this cache is intentionally never invalidated; it
# holds at most three small pixmaps for the process lifetime.
_pixmap_cache: dict[TrayState, QPixmap] = {}


def get_icon_pixmap(state: TrayState) -> QPixmap:
    """64x64 QPixmap from embedded base64 PNG data (decoded once per state, then cached)."""
    pixmap = _pixmap_cache.get(state)
    if pixmap is None:
        pixmap = QPixmap()
        pixmap.loadFromData(_EMBEDDED_PNG[state])
        _pixmap_cache[state] = pixmap
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
