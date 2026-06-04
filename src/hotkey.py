"""Global hotkey listener using Win32 low-level hooks (WH_KEYBOARD_LL + WH_MOUSE_LL).

Supports arbitrary keys, mouse side/middle buttons, hold/toggle modes, and
swallowing the trigger event. The matching core (_on_kb_event / _on_mouse_event)
is pure and OS-independent; only start()/stop() touch Win32.
"""

from __future__ import annotations

import logging
import platform
import threading
from enum import Enum

from src.config import (
    Hotkey,
    MODIFIER_VK_TO_NAME,
    MOUSE_MIDDLE,
    MOUSE_X1,
    MOUSE_X2,
)
from src.utils import AppError, ScreamerError, SignalBridge

log = logging.getLogger(__name__)

# Win32 message constants (also imported by tests).
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

_KEY_DOWN = frozenset({WM_KEYDOWN, WM_SYSKEYDOWN})
_KEY_UP = frozenset({WM_KEYUP, WM_SYSKEYUP})

# XBUTTON discriminators in the high word of MSLLHOOKSTRUCT.mouseData.
_XBUTTON1 = 0x0001
_XBUTTON2 = 0x0002


class HotkeyMode(Enum):
    HOLD = "hold"
    TOGGLE = "toggle"


class HotkeyListener:
    """Low-level-hook hotkey listener with hold/toggle modes and trigger suppression."""

    def __init__(self, hotkey: Hotkey, mode: HotkeyMode, bridge: SignalBridge) -> None:
        self._hotkey = hotkey
        self._mode = mode
        self._bridge = bridge
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._stop_event = threading.Event()
        # Matching state.
        self._held: set[str] = set()
        self._armed = False
        # Keep ctypes callbacks alive across the message loop's lifetime.
        self._kb_proc = None
        self._mouse_proc = None
        self._kb_hook = None
        self._mouse_hook = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if platform.system() != "Windows":
            raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "Low-level hooks require Windows")
        self._stop_event.clear()
        self._held.clear()
        self._armed = False
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        log.info("HotkeyListener started: %s mode=%s", self._hotkey.to_canonical(), self._mode.value)

    def stop(self) -> None:
        if platform.system() != "Windows":
            return
        self._stop_event.set()
        if self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._thread_id = 0
        log.info("HotkeyListener stopped")

    def set_mode(self, mode: HotkeyMode) -> None:
        self._mode = mode
        self._armed = False
        log.info("Hotkey mode changed to %s", mode.value)

    # ------------------------------------------------------------------
    # Pure matching core (OS-independent; unit-tested)
    # ------------------------------------------------------------------

    def _on_kb_event(self, wparam: int, vk: int) -> bool:
        """Handle a keyboard hook event. Return True to suppress (swallow) it."""
        mod = MODIFIER_VK_TO_NAME.get(vk)
        if mod is not None:
            if wparam in _KEY_DOWN:
                self._held.add(mod)
            elif wparam in _KEY_UP:
                self._held.discard(mod)
            return False  # modifiers always pass through

        if self._hotkey.kind != "key" or vk != self._hotkey.code:
            return False

        if wparam in _KEY_DOWN:
            return self._trigger_down()
        if wparam in _KEY_UP:
            return self._trigger_up()
        return False

    def _on_mouse_event(self, wparam: int, mouse_data: int) -> bool:
        """Handle a mouse hook event. Return True to suppress (swallow) it."""
        if wparam == WM_MBUTTONDOWN:
            btn, is_down = MOUSE_MIDDLE, True
        elif wparam == WM_MBUTTONUP:
            btn, is_down = MOUSE_MIDDLE, False
        elif wparam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            high = (mouse_data >> 16) & 0xFFFF
            if high == _XBUTTON1:
                btn = MOUSE_X1
            elif high == _XBUTTON2:
                btn = MOUSE_X2
            else:
                return False
            is_down = wparam == WM_XBUTTONDOWN
        else:
            return False  # left/right/move/wheel — never our trigger

        if self._hotkey.kind != "mouse" or btn != self._hotkey.code:
            return False
        return self._trigger_down() if is_down else self._trigger_up()

    def _trigger_down(self) -> bool:
        if self._armed:
            return True  # autorepeat / duplicate down while held
        if self._held != self._hotkey.mods:
            return False
        self._armed = True
        self._bridge.hotkey_pressed.emit()
        return True

    def _trigger_up(self) -> bool:
        if not self._armed:
            return False
        self._armed = False
        if self._mode == HotkeyMode.HOLD:
            self._bridge.hotkey_released.emit()
        return True

    # ------------------------------------------------------------------
    # Win32 message loop + hook installation
    # ------------------------------------------------------------------

    def _message_loop(self) -> None:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        lresult = getattr(ctypes.wintypes, "LRESULT", ctypes.c_ssize_t)
        ulong_ptr = getattr(ctypes.wintypes, "ULONG_PTR", ctypes.c_size_t)
        HOOKPROC = ctypes.WINFUNCTYPE(
            lresult, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
        )

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.wintypes.DWORD),
                ("scanCode", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.wintypes.LONG), ("y", ctypes.wintypes.LONG)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT),
                ("mouseData", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr),
            ]

        _declare_win32_functions(ctypes, user32, kernel32, HOOKPROC, lresult)

        def kb_callback(ncode, wparam, lparam):
            if ncode == HC_ACTION:
                kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self._on_kb_event(wparam, kb.vkCode):
                    return 1
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        def mouse_callback(ncode, wparam, lparam):
            if ncode == HC_ACTION:
                ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if self._on_mouse_event(wparam, ms.mouseData):
                    return 1
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        self._kb_proc = HOOKPROC(kb_callback)
        self._mouse_proc = HOOKPROC(mouse_callback)

        self._thread_id = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)

        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
        self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hmod, 0)
        if not self._kb_hook or not self._mouse_hook:
            log.error("SetWindowsHookEx failed: kb=%s mouse=%s", self._kb_hook, self._mouse_hook)
            self._bridge.error_occurred.emit(AppError.HOTKEY_HOOK_FAILED)
            self._uninstall(user32)
            return

        log.info("Hooks installed for %s", self._hotkey.to_canonical())

        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._uninstall(user32)
        log.info("Message loop exited")

    def _uninstall(self, user32) -> None:
        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None


def _declare_win32_functions(ctypes, user32, kernel32, hookproc, lresult) -> None:
    wintypes = ctypes.wintypes
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetCurrentThreadId.argtypes = []

    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc, ctypes.c_void_p, wintypes.DWORD]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.CallNextHookEx.restype = lresult
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = lresult
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


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

    from src.config import Hotkey

    app = QApplication(sys.argv)
    bridge = SignalBridge()
    bridge.hotkey_pressed.connect(lambda: print("PRESSED"))
    bridge.hotkey_released.connect(lambda: print("RELEASED"))

    listener = HotkeyListener(Hotkey(frozenset(), "key", 0x91), HotkeyMode.HOLD, bridge)
    listener.start()
    print("Press Scroll Lock to test (Ctrl+C to quit)...")
    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
