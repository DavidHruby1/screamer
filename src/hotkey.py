"""Global hotkey listener using Win32 RegisterHotKey + message-only window."""

from __future__ import annotations

import logging
import platform
import threading
import time
from enum import Enum

from src.config import HOTKEY_BINDINGS, HotkeyBinding
from src.utils import AppError, ScreamerError, SignalBridge

log = logging.getLogger(__name__)

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
        self._release_lock = threading.Lock()
        self._release_thread: threading.Thread | None = None
        self._release_watch_active = False

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

        lresult_type = getattr(ctypes.wintypes, "LRESULT", ctypes.c_ssize_t)
        WNDPROC = ctypes.WINFUNCTYPE(
            lresult_type,
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

        _declare_win32_functions(ctypes, user32, kernel32, wnd_class_type, lresult_type)

        # Create a message-only window.
        wnd_proc = WNDPROC(self._wnd_proc)
        wnd_class = wnd_class_type()
        wnd_class.cbSize = ctypes.sizeof(wnd_class_type)
        wnd_class.lpfnWndProc = wnd_proc
        wnd_class.hInstance = kernel32.GetModuleHandleW(None)
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
        binding = HOTKEY_BINDINGS.get(self._key.lower())
        if binding is None:
            log.error("Unknown hotkey: %s", self._key)
            return

        HOTKEY_ID = self._hotkey_id
        if not user32.RegisterHotKey(self._hwnd, HOTKEY_ID, binding.modifiers, binding.vk):
            log.error(
                "RegisterHotKey failed for key=%s modifiers=0x%04X vk=0x%02X (conflict?)",
                self._key,
                binding.modifiers,
                binding.vk,
            )
            self._bridge.error_occurred.emit(AppError.HOTKEY_CONFLICT)
            return

        log.info(
            "Registered hotkey: key=%s modifiers=0x%04X vk=0x%02X id=%d",
            self._key,
            binding.modifiers,
            binding.vk,
            HOTKEY_ID,
        )

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
                binding = HOTKEY_BINDINGS.get(self._key.lower())
                if binding is not None:
                    self._start_release_watch(binding)

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _start_release_watch(self, binding: HotkeyBinding) -> None:
        """Poll key release outside the window procedure so the pump stays responsive."""
        with self._release_lock:
            if self._release_watch_active:
                return
            self._release_watch_active = True
            self._release_thread = threading.Thread(
                target=self._watch_release,
                args=(binding.vk,),
                daemon=True,
            )
            self._release_thread.start()

    def _watch_release(self, vk: int) -> None:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        while not self._stop_event.is_set():
            state = user32.GetAsyncKeyState(vk)
            if not (state & 0x8000):  # high-order bit = key is down
                break
            time.sleep(0.05)

        with self._release_lock:
            self._release_watch_active = False

        if not self._stop_event.is_set():
            self._bridge.hotkey_released.emit()


def _declare_win32_functions(ctypes, user32, kernel32, wnd_class_type, lresult_type) -> None:
    """Declare the Win32 ABI once before any ctypes calls cross the boundary."""
    wintypes = ctypes.wintypes

    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.GetLastError.argtypes = []

    atom_type = getattr(wintypes, "ATOM", wintypes.WORD)
    user32.RegisterClassExW.restype = atom_type
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(wnd_class_type)]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = lresult_type
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostQuitMessage.restype = None
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.DefWindowProcW.restype = lresult_type
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


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
