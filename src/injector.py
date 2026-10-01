"""Keyboard text injection via Win32 SendInput (KEYEVENTF_UNICODE) + post-type key."""

from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass, field

from src.utils import AppError, ScreamerError, log_duration

log = logging.getLogger(__name__)

# Post-type key virtual key codes.
_POST_KEY_VK: dict[str, int] = {
    "enter": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
}


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    process_id: int
    executable_path: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class InjectionReport:
    text_events_submitted: int
    post_key_events_submitted: int
    post_key_skipped: bool


class InjectionError(ScreamerError):
    def __init__(
        self,
        detail: str,
        text_events_submitted: int = 0,
        post_key_events_submitted: int = 0,
    ) -> None:
        self.text_events_submitted = text_events_submitted
        self.post_key_events_submitted = post_key_events_submitted
        super().__init__(AppError.INJECTION_FAILED, detail)


def get_foreground_target() -> WindowIdentity | None:
    """Read only foreground HWND/PID and optional executable metadata."""
    if platform.system() != "Windows":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    if not hwnd or not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        return None
    path = None
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if process:
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                path = buffer.value
        finally:
            kernel32.CloseHandle(process)
    return WindowIdentity(int(hwnd), pid.value, path)


def _utf16_units(value: str) -> list[str]:
    """Split *value* into UTF-16 code units (surrogate pairs become two units)."""
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    return [chr(int.from_bytes(encoded[i : i + 2], "little")) for i in range(0, len(encoded), 2)]


def type_text(
    text: str,
    post_key: str | None = None,
    *,
    expected_target: WindowIdentity | None = None,
) -> InjectionReport:
    """Type *text* into the active window via Win32 SendInput.

    0.05s delay then press *post_key* if not ``None`` and not ``"none"``.
    Reports submitted events, not visible insertion. Never retries a partial send.
    Raises ``InjectionError`` with separate text/post-key counts on failure.
    On non-Windows, raises ``ScreamerError(AppError.UNSUPPORTED_PLATFORM)``.
    """
    if platform.system() != "Windows":
        raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "SendInput requires Windows")
    normalized_key = post_key.lower() if post_key is not None else "none"
    if normalized_key != "none" and normalized_key not in _POST_KEY_VK:
        raise InjectionError("Unknown post-type key")
    text_sent = 0
    post_sent = 0

    import ctypes
    import ctypes.wintypes

    # --- SendInput struct definitions ---
    # SendInput requires cbSize to be the exact size of the Win32 INPUT union.
    # Defining only KEYBDINPUT makes INPUT too small on 64-bit Windows.

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.wintypes.WORD),
            ("wScan", ctypes.wintypes.WORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.wintypes.LONG),
            ("dy", ctypes.wintypes.LONG),
            ("mouseData", ctypes.wintypes.DWORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.wintypes.DWORD),
            ("wParamL", ctypes.wintypes.WORD),
            ("wParamH", ctypes.wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [
            ("type", ctypes.wintypes.DWORD),
            ("union", _INPUT_UNION),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    user32.SendInput.argtypes = (ctypes.wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = ctypes.wintypes.UINT

    def _raise_sendinput_failed(detail: str) -> None:
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        if err:
            detail = f"{detail} (WinError {err})"
        raise InjectionError(detail, text_sent, post_sent)

    def _send_vk(vk: int) -> None:
        nonlocal post_sent
        batch = (INPUT * 2)()
        batch[0].type = INPUT_KEYBOARD
        batch[0].ki.wVk = vk
        batch[1].type = INPUT_KEYBOARD
        batch[1].ki.wVk = vk
        batch[1].ki.dwFlags = KEYEVENTF_KEYUP
        ctypes.set_last_error(0)  # type: ignore[attr-defined]
        post_sent = user32.SendInput(2, batch, ctypes.sizeof(INPUT))
        if post_sent != 2:
            _raise_sendinput_failed(
                f"SendInput submitted {post_sent}/2 post-key events for VK 0x{vk:02X}"
            )

    try:
        with log_duration(log, f"Text injection ({len(text)} chars)"):
            log.info("Typing %d characters", len(text))
            # One batched SendInput call: atomic with respect to concurrent
            # user input and far fewer syscalls than per-character sends.
            units = _utf16_units(text)
            if units:
                count = 2 * len(units)
                batch = (INPUT * count)()
                for i, unit in enumerate(units):
                    code = ord(unit)
                    down = batch[2 * i]
                    down.type = INPUT_KEYBOARD
                    down.ki.wScan = code
                    down.ki.dwFlags = KEYEVENTF_UNICODE
                    up = batch[2 * i + 1]
                    up.type = INPUT_KEYBOARD
                    up.ki.wScan = code
                    up.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
                if expected_target is not None and get_foreground_target() != expected_target:
                    raise InjectionError("Foreground target changed or unavailable")
                ctypes.set_last_error(0)  # type: ignore[attr-defined]
                text_sent = user32.SendInput(count, batch, ctypes.sizeof(INPUT))
                # Partial injection (sent < count) means a prefix of the text
                # was already typed; there is no rollback — surface the counts.
                if text_sent != count:
                    _raise_sendinput_failed(f"SendInput submitted {text_sent}/{count} text events")

            # Post-type key with 0.05s delay.
            if normalized_key != "none":
                time.sleep(0.05)
                if expected_target is not None and get_foreground_target() != expected_target:
                    return InjectionReport(text_sent, 0, True)
                _send_vk(_POST_KEY_VK[normalized_key])
            return InjectionReport(text_sent, post_sent, False)

    except ScreamerError:
        raise
    except Exception as e:
        raise InjectionError(str(e), text_sent, post_sent) from e


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if platform.system() != "Windows":
        print("injector.py requires Windows for SendInput.")
        print(
            "On non-Windows: import succeeds, runtime raises ScreamerError(UNSUPPORTED_PLATFORM)."
        )
        print("Import test passed — no crash at import time.")
        raise SystemExit(0)

    text = sys.argv[1] if len(sys.argv) > 1 else "hello world"
    print(f"Typing in 3 seconds: {text!r}")
    print("Click into a text field now...")
    time.sleep(3)
    type_text(text)
    print("Done.")
