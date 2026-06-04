import os
import platform
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.config import Hotkey, MOUSE_X1
from src.hotkey import (
    HotkeyListener,
    HotkeyMode,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
)
from src.utils import SignalBridge

_app = QApplication.instance() or QApplication([])


def _listener(hotkey, mode):
    bridge = SignalBridge()
    pressed = []
    released = []
    bridge.hotkey_pressed.connect(lambda: pressed.append(1))
    bridge.hotkey_released.connect(lambda: released.append(1))
    return HotkeyListener(hotkey, mode, bridge), pressed, released


VK_LCTRL = 0xA2
VK_LALT = 0xA4


class HoldKeyTests(unittest.TestCase):
    def test_full_combo_press_and_release(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        listener, pressed, released = _listener(hk, HotkeyMode.HOLD)

        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, VK_LCTRL))  # modifier passes
        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, VK_LALT))
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, 0x20))       # trigger suppressed
        self.assertEqual(pressed, [1])
        self.assertEqual(released, [])

        self.assertTrue(listener._on_kb_event(WM_KEYUP, 0x20))         # release suppressed
        self.assertEqual(released, [1])

    def test_autorepeat_does_not_re_emit(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        listener._on_kb_event(WM_KEYDOWN, VK_LALT)
        listener._on_kb_event(WM_KEYDOWN, 0x20)
        listener._on_kb_event(WM_KEYDOWN, 0x20)  # autorepeat
        listener._on_kb_event(WM_KEYDOWN, 0x20)  # autorepeat
        self.assertEqual(pressed, [1])

    def test_wrong_modifiers_do_not_fire(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)  # only ctrl, alt missing
        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, 0x20))  # not suppressed
        self.assertEqual(pressed, [])

    def test_extra_modifier_blocks_match(self):
        hk = Hotkey(frozenset({"ctrl"}), "key", 0x20)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        listener._on_kb_event(WM_KEYDOWN, VK_LALT)   # extra alt held
        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, 0x20))
        self.assertEqual(pressed, [])


class ToggleKeyTests(unittest.TestCase):
    def test_toggle_emits_pressed_each_time_no_released(self):
        hk = Hotkey(frozenset(), "key", 0x91)  # Scroll Lock, no mods
        listener, pressed, released = _listener(hk, HotkeyMode.TOGGLE)
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, 0x91))
        self.assertTrue(listener._on_kb_event(WM_KEYUP, 0x91))
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, 0x91))
        self.assertEqual(pressed, [1, 1])
        self.assertEqual(released, [])


class MouseTests(unittest.TestCase):
    def test_mouse_x1_with_ctrl(self):
        hk = Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1)
        listener, pressed, released = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        # mouseData high word = XBUTTON1 (0x0001)
        self.assertTrue(listener._on_mouse_event(WM_XBUTTONDOWN, 0x0001 << 16))
        self.assertEqual(pressed, [1])
        self.assertTrue(listener._on_mouse_event(WM_XBUTTONUP, 0x0001 << 16))
        self.assertEqual(released, [1])

    def test_left_button_ignored(self):
        hk = Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1)
        listener, pressed, _ = _listener(hk, HotkeyMode.HOLD)
        listener._on_kb_event(WM_KEYDOWN, VK_LCTRL)
        self.assertFalse(listener._on_mouse_event(0x0201, 0))  # WM_LBUTTONDOWN
        self.assertEqual(pressed, [])


class LifecycleTests(unittest.TestCase):
    def test_stop_without_start_is_safe(self):
        hk = Hotkey(frozenset(), "key", 0x91)
        listener, _p, _r = _listener(hk, HotkeyMode.HOLD)
        # Must not raise or hang regardless of platform.
        listener.stop()
        self.assertIsNone(listener._thread)

    @unittest.skipUnless(platform.system() == "Windows", "LL hooks require Windows")
    def test_start_then_stop_exits_cleanly(self):
        hk = Hotkey(frozenset(), "key", 0x91)  # Scroll Lock
        listener, _p, _r = _listener(hk, HotkeyMode.HOLD)
        listener.start()
        self.assertTrue(listener._ready.is_set(), "start() must wait for readiness")
        listener.stop()
        self.assertIsNone(listener._thread, "thread reference cleared after clean exit")
        self.assertEqual(listener._thread_id, 0)
        self.assertIsNone(listener._kb_hook)
        self.assertIsNone(listener._mouse_hook)

    @unittest.skipUnless(platform.system() == "Windows", "LL hooks require Windows")
    def test_repeated_start_stop(self):
        hk = Hotkey(frozenset(), "key", 0x91)
        listener, _p, _r = _listener(hk, HotkeyMode.HOLD)
        for _ in range(3):
            listener.start()
            listener.stop()
            self.assertIsNone(listener._thread)


if __name__ == "__main__":
    unittest.main()
