"""Keyboard text injection via Win32 SendInput (KEYEVENTF_UNICODE) + post-type key."""

from __future__ import annotations

import logging
import platform
import time

from src.utils import AppError, ScreamerError

log = logging.getLogger(__name__)

# Post-type key virtual key codes.
_POST_KEY_VK: dict[str, int] = {
    "enter": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
}


def type_text(text: str, post_key: str | None = None) -> None:
    """Type *text* into the active window via Win32 SendInput.

    0.05s delay then press *post_key* if not ``None`` and not ``"none"``.
    Raises ``ScreamerError(AppError.INJECTION_FAILED)`` on failure.
    On non-Windows, raises ``ScreamerError(AppError.UNSUPPORTED_PLATFORM)``.
    """
    if platform.system() != "Windows":
        raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "SendInput requires Windows")

    import ctypes
    import ctypes.wintypes

    # --- SendInput struct definitions ---
    # Copied from pynput/_util/win32.py (MIT-licensed) for correct alignment.
    # See: https://github.com/moses-palmer/pynput

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.wintypes.WORD),
            ("wScan", ctypes.wintypes.WORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.wintypes.DWORD),
            ("union", _INPUT_UNION),
        ]

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    def _send_unicode(char: str, key_up: bool = False) -> None:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki.wScan = ord(char)
        inp.union.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 0:
            raise ScreamerError(AppError.INJECTION_FAILED, f"SendInput failed for U+{ord(char):04X}")

    def _send_vk(vk: int, key_up: bool = False) -> None:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki.wVk = vk
        inp.union.ki.dwFlags = KEYEVENTF_KEYUP if key_up else 0
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 0:
            raise ScreamerError(AppError.INJECTION_FAILED, f"SendInput failed for VK 0x{vk:02X}")

    try:
        log.info("Typing %d characters", len(text))
        for ch in text:
            _send_unicode(ch)
            _send_unicode(ch, key_up=True)

        # Post-type key with 0.05s delay.
        if post_key and post_key != "none":
            vk = _POST_KEY_VK.get(post_key.lower())
            if vk is not None:
                time.sleep(0.05)
                _send_vk(vk)
                _send_vk(vk, key_up=True)
                log.info("Post-type key pressed: %s", post_key)
            else:
                log.warning("Unknown post-type key: %s", post_key)

    except ScreamerError:
        raise
    except Exception as e:
        raise ScreamerError(AppError.INJECTION_FAILED, str(e)) from e


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if platform.system() != "Windows":
        print("injector.py requires Windows for SendInput.")
        print("On non-Windows: import succeeds, runtime raises ScreamerError(UNSUPPORTED_PLATFORM).")
        print("Import test passed — no crash at import time.")
        raise SystemExit(0)

    text = sys.argv[1] if len(sys.argv) > 1 else "hello world"
    print(f"Typing in 3 seconds: {text!r}")
    print("Click into a text field now...")
    time.sleep(3)
    type_text(text)
    print("Done.")
