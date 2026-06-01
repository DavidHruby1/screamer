"""Global hotkey listener using Win32 RegisterHotKey + message-only window."""

from __future__ import annotations

import logging
import platform
import threading
import time
from enum import Enum

from src.utils import AppError, ScreamerError, SignalBridge

log = logging.getLogger(__name__)

# Virtual key code map for hotkey strings.
_VK_MAP: dict[str, int] = {
    "ctrl": 0xA2,       # VK_LCONTROL
    "ctrl_l": 0xA2,
    "ctrl_r": 0xA3,
    "alt": 0xA4,        # VK_LMENU
    "alt_l": 0xA4,
    "alt_r": 0xA5,
    "pause": 0x13,      # VK_PAUSE
    "f13": 0x7C,
    "f14": 0x7D,
    "scroll_lock": 0x91,  # VK_SCROLL
}


class HotkeyMode(Enum):
    HOLD = "hold"
    TOGGLE = "toggle"


class HotkeyListener:
    """Win32 RegisterHotKey-based hotkey listener with hold/toggle modes."""

    def __init__(self, key: str, mode: HotkeyMode, bridge: SignalBridge) -> None:
        self._key = key
        self._mode = mode
        self._bridge = bridge
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._hwnd = None
        self._hotkey_id = 1

    def start(self) -> None:
        """Create message-only window, RegisterHotKey, GetMessage pump in daemon thread."""
        if platform.system() != "Windows":
            raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "RegisterHotKey requires Windows")

        import ctypes
        import ctypes.wintypes

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        log.info("HotkeyListener started: key=%s mode=%s", self._key, self._mode.value)

    def stop(self) -> None:
        """Post WM_QUIT, join thread, unregister hotkey."""
        if platform.system() != "Windows":
            return

        self._stop_event.set()
        if self._hwnd is not None:
            import ctypes
            import ctypes.wintypes
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_QUIT
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        log.info("HotkeyListener stopped")

    def set_mode(self, mode: HotkeyMode) -> None:
        self._mode = mode
        log.info("Hotkey mode changed to %s", mode.value)

    def _message_loop(self) -> None:
        """Run in a daemon thread: create message-only window, register hotkey, pump messages."""
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.wintypes.LRESULT,
            ctypes.wintypes.HWND,
            ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )

        try:
            wnd_class_type = ctypes.wintypes.WNDCLASSEXW
        except AttributeError:
            class WNDCLASSEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.wintypes.UINT),
                    ("style", ctypes.wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.c_void_p),
                    ("hIcon", ctypes.c_void_p),
                    ("hCursor", ctypes.c_void_p),
                    ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p),
                    ("hIconSm", ctypes.c_void_p),
                ]

            wnd_class_type = WNDCLASSEXW

        # Create a message-only window.
        wnd_proc = WNDPROC(self._wnd_proc)
        wnd_class = wnd_class_type()
        wnd_class.cbSize = ctypes.sizeof(wnd_class_type)
        wnd_class.lpfnWndProc = wnd_proc
        wnd_class.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)  # type: ignore[attr-defined]
        wnd_class.lpszClassName = "ScreamerHotkeyWindow"

        atom = user32.RegisterClassExW(ctypes.byref(wnd_class))
        if not atom:
            ERROR_CLASS_ALREADY_EXISTS = 1410
            last_error = kernel32.GetLastError()
            if last_error != ERROR_CLASS_ALREADY_EXISTS:
                log.error("RegisterClassExW failed: error=%d", last_error)
                return

        if self._stop_event.is_set():
            return

        # HWND_MESSAGE parent = -3 for message-only window.
        HWND_MESSAGE = ctypes.wintypes.HWND(-3)
        self._hwnd = user32.CreateWindowExW(
            0, wnd_class.lpszClassName, "ScreamerHotkey", 0,
            0, 0, 0, 0, HWND_MESSAGE, None, wnd_class.hInstance, None,
        )
        if not self._hwnd:
            log.error("CreateWindowExW failed")
            return

        if self._stop_event.is_set():
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
            return

        # Register the hotkey.
        vk = _VK_MAP.get(self._key.lower())
        if vk is None:
            log.error("Unknown hotkey: %s", self._key)
            return

        HOTKEY_ID = self._hotkey_id
        if not user32.RegisterHotKey(self._hwnd, HOTKEY_ID, 0, vk):
            log.error("RegisterHotKey failed for key=%s vk=0x%02X (conflict?)", self._key, vk)
            self._bridge.error_occurred.emit(AppError.HOTKEY_CONFLICT)
            return

        log.info("Registered hotkey: key=%s vk=0x%02X id=%d", self._key, vk, HOTKEY_ID)

        # Message pump — blocks until WM_QUIT.
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), self._hwnd, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
        user32.DestroyWindow(self._hwnd)
        self._hwnd = None
        log.info("Message loop exited")

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """Window procedure: dispatch WM_HOTKEY to press/release callbacks."""
        import ctypes
        import ctypes.wintypes

        WM_HOTKEY = 0x0312
        WM_CLOSE = 0x0010
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        if msg == WM_CLOSE:
            user32.PostQuitMessage(0)
            return 0

        if msg == WM_HOTKEY:
            log.debug("WM_HOTKEY received: id=%d", wparam)
            self._bridge.hotkey_pressed.emit()

            if self._mode == HotkeyMode.HOLD:
                # Poll GetAsyncKeyState until the key is released.
                vk = _VK_MAP.get(self._key.lower(), 0)
                while not self._stop_event.is_set():
                    state = user32.GetAsyncKeyState(vk)
                    if not (state & 0x8000):  # high-order bit = key is down
                        break
                    time.sleep(0.05)
                self._bridge.hotkey_released.emit()

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if platform.system() != "Windows":
        print("HotkeyListener requires Windows.")
        print("On non-Windows: start() raises ScreamerError(UNSUPPORTED_PLATFORM).")
        print("Import test passed — no crash at import time.")
        raise SystemExit(0)

    from PySide6.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    bridge = SignalBridge()

    def on_pressed():
        print("PRESSED")

    def on_released():
        print("RELEASED")

    bridge.hotkey_pressed.connect(on_pressed)
    bridge.hotkey_released.connect(on_released)

    listener = HotkeyListener("scroll_lock", HotkeyMode.HOLD, bridge)
    listener.start()
    print("Press Scroll Lock to test (Ctrl+C to quit)...")
    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
