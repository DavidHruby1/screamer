import ctypes
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.config import Hotkey
from src.hotkey import HotkeyListener, HotkeyMode, WM_KEYDOWN, WM_KEYUP
from src.injector import InjectionError, WindowIdentity, type_text
from src.utils import SignalBridge

_app = QApplication.instance() or QApplication([])


class GuardedInjectionTests(unittest.TestCase):
    def setUp(self):
        self.target = WindowIdentity(100, 200, None)
        self.user32 = Mock()
        self.user32.SendInput.side_effect = lambda count, _batch, _size: count
        self.enterContext(patch("src.injector.platform.system", return_value="Windows"))
        self.enterContext(patch.object(ctypes, "WinDLL", return_value=self.user32, create=True))
        self.enterContext(patch.object(ctypes, "get_last_error", return_value=0, create=True))
        self.enterContext(patch.object(ctypes, "set_last_error", create=True))
        self.enterContext(patch("src.injector.time.sleep"))

    def test_missing_target_or_reused_hwnd_with_other_pid_sends_nothing(self):
        for current in (None, WindowIdentity(100, 201, None), WindowIdentity(101, 200, None)):
            with (
                self.subTest(current=current),
                patch("src.injector.get_foreground_target", return_value=current),
            ):
                with self.assertRaises(InjectionError):
                    type_text("text", "enter", expected_target=self.target)
        self.user32.SendInput.assert_not_called()

    def test_executable_metadata_does_not_change_verified_target(self):
        current = WindowIdentity(100, 200, "C:\\Editor\\editor.exe")
        with patch("src.injector.get_foreground_target", return_value=current):
            report = type_text("a", expected_target=self.target)
        self.assertEqual(report.text_events_submitted, 2)
        self.assertEqual(report.post_key_events_submitted, 0)
        self.assertEqual(self.user32.SendInput.call_count, 1)

    def test_partial_surrogate_batch_is_never_retried_or_followed_by_post_key(self):
        self.user32.SendInput.return_value = 3
        self.user32.SendInput.side_effect = None
        with patch("src.injector.get_foreground_target", return_value=self.target):
            with self.assertRaises(InjectionError) as failed:
                type_text("\U0001f600", "enter", expected_target=self.target)
        self.assertEqual(failed.exception.text_events_submitted, 3)
        self.assertEqual(failed.exception.post_key_events_submitted, 0)
        self.assertEqual(self.user32.SendInput.call_count, 1)
        self.assertEqual(self.user32.SendInput.call_args.args[0], 4)

    def test_focus_change_after_text_skips_only_the_post_key(self):
        with patch(
            "src.injector.get_foreground_target",
            side_effect=[self.target, WindowIdentity(101, 200, None)],
        ):
            report = type_text("abc", "enter", expected_target=self.target)
        self.assertEqual(report.text_events_submitted, 6)
        self.assertEqual(report.post_key_events_submitted, 0)
        self.assertTrue(report.post_key_skipped)
        self.assertEqual(self.user32.SendInput.call_count, 1)

    def test_partial_post_key_preserves_completed_text_count(self):
        self.user32.SendInput.side_effect = [6, 1]
        with patch("src.injector.get_foreground_target", return_value=self.target):
            with self.assertRaises(InjectionError) as failed:
                type_text("abc", "enter", expected_target=self.target)
        self.assertEqual(failed.exception.text_events_submitted, 6)
        self.assertEqual(failed.exception.post_key_events_submitted, 1)
        self.assertEqual(self.user32.SendInput.call_count, 2)

    def test_invalid_post_key_cannot_submit_text_first(self):
        with patch("src.injector.get_foreground_target", return_value=self.target):
            with self.assertRaises(InjectionError):
                type_text("abc", "not-a-key", expected_target=self.target)
        self.user32.SendInput.assert_not_called()


class RecoveryChordTests(unittest.TestCase):
    def setUp(self):
        self.bridge = SignalBridge()
        self.recovered = []
        self.cancelled = []
        self.dictation = []
        self.bridge.recovery_requested.connect(lambda: self.recovered.append(True))
        self.bridge.recovery_cancelled.connect(lambda: self.cancelled.append(True))
        self.bridge.hotkey_pressed.connect(lambda: self.dictation.append(True))
        self.listener = HotkeyListener(
            Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20),
            HotkeyMode.HOLD,
            self.bridge,
            recovery_hotkey=Hotkey(frozenset({"ctrl", "alt", "shift"}), "key", 0x56),
        )

    def press_chord(self):
        for key in (0xA2, 0xA4, 0xA0):
            self.listener._on_kb_event(WM_KEYDOWN, key)
        self.assertTrue(self.listener._on_kb_event(WM_KEYDOWN, 0x56))
        self.assertTrue(self.listener._on_kb_event(WM_KEYDOWN, 0x56))
        self.assertEqual(self.recovered, [])

    def test_trigger_first_release_waits_for_all_modifiers(self):
        self.press_chord()
        self.assertTrue(self.listener._on_kb_event(WM_KEYUP, 0x56))
        for key in (0xA2, 0xA4):
            self.listener._on_kb_event(WM_KEYUP, key)
            self.assertEqual(self.recovered, [])
        self.listener._on_kb_event(WM_KEYUP, 0xA0)
        self.assertEqual(self.recovered, [True])
        self.listener._on_kb_event(WM_KEYUP, 0xA0)
        self.assertEqual(self.recovered, [True])
        self.assertEqual(self.dictation, [])

    def test_modifiers_first_release_waits_for_trigger(self):
        self.press_chord()
        for key in (0xA0, 0xA4, 0xA2):
            self.listener._on_kb_event(WM_KEYUP, key)
        self.assertEqual(self.recovered, [])
        self.assertTrue(self.listener._on_kb_event(WM_KEYUP, 0x56))
        self.assertEqual(self.recovered, [True])
        self.assertEqual(self.dictation, [])

    def test_escape_requests_disarm_without_swallowing_escape(self):
        self.assertFalse(self.listener._on_kb_event(WM_KEYDOWN, 0x1B))
        self.assertEqual(self.cancelled, [True])

    def test_injected_dictation_post_key_passes_through_without_starting_recording(self):
        self.listener._on_kb_event(WM_KEYDOWN, 0xA2)
        self.listener._on_kb_event(WM_KEYDOWN, 0xA4)
        self.assertFalse(self.listener._on_kb_event(WM_KEYDOWN, 0x20, flags=0x10))
        self.assertFalse(self.listener._on_kb_event(WM_KEYUP, 0x20, flags=0x10))
        self.assertEqual(self.dictation, [])
        self.assertTrue(self.listener._on_kb_event(WM_KEYDOWN, 0x20))
        self.assertEqual(self.dictation, [True])

    def test_injected_key_up_does_not_release_a_physically_held_trigger(self):
        released = []
        self.bridge.hotkey_released.connect(lambda: released.append(True))
        self.listener._on_kb_event(WM_KEYDOWN, 0xA2)
        self.listener._on_kb_event(WM_KEYDOWN, 0xA4)
        self.assertTrue(self.listener._on_kb_event(WM_KEYDOWN, 0x20))
        self.assertFalse(self.listener._on_kb_event(WM_KEYUP, 0x20, flags=0x10))
        self.assertEqual(released, [])
        self.assertTrue(self.listener._on_kb_event(WM_KEYUP, 0x20))
        self.assertEqual(released, [True])


if __name__ == "__main__":
    unittest.main()
