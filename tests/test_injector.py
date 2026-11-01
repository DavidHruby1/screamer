import unittest
import ctypes
from unittest.mock import Mock, patch

from src.injector import InjectionError, _utf16_units, type_text
from src.utils import AppError, ScreamerError


class Utf16UnitsTests(unittest.TestCase):
    def test_ascii_maps_one_to_one(self) -> None:
        self.assertEqual([ord(u) for u in _utf16_units("hi")], [0x68, 0x69])

    def test_emoji_splits_into_surrogate_pair(self) -> None:
        units = _utf16_units("\U0001f600")
        self.assertEqual([ord(u) for u in units], [0xD83D, 0xDE00])

    def test_empty_text_yields_no_units(self) -> None:
        self.assertEqual(_utf16_units(""), [])

    def test_post_key_down_up_are_one_checked_sendinput_call(self) -> None:
        events = []

        def send(count, batch, size):
            events.append(
                (count, batch[0].ki.wVk, batch[0].ki.dwFlags, batch[1].ki.wVk, batch[1].ki.dwFlags)
            )
            return count

        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        user32.SendInput.side_effect = send
        with (
            patch("src.injector.platform.system", return_value="Windows"),
            patch.object(ctypes, "WinDLL", return_value=user32, create=True),
            patch.object(ctypes, "set_last_error", create=True),
            patch("src.injector.time.sleep"),
        ):
            type_text("", "enter")
        self.assertEqual(events, [(2, 0x0D, 0, 0x0D, 2)])

    def test_post_key_partial_batch_reports_injection_failure(self) -> None:
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        user32.SendInput.return_value = 1
        with (
            patch("src.injector.platform.system", return_value="Windows"),
            patch.object(ctypes, "WinDLL", return_value=user32, create=True),
            patch.object(ctypes, "get_last_error", return_value=0, create=True),
            patch.object(ctypes, "set_last_error", create=True),
            patch("src.injector.time.sleep"),
        ):
            with self.assertRaises(ScreamerError) as error:
                type_text("", "enter")
        self.assertEqual(error.exception.code, AppError.INJECTION_FAILED)
        self.assertEqual(user32.SendInput.call_count, 1)


class HeldModifierTests(unittest.TestCase):
    """Shift/Alt still held from the hotkey chord are released inside the same batch."""

    def setUp(self) -> None:
        self.batches = []

        def send(count, batch, _size):
            self.batches.append([(batch[i].ki.wVk, batch[i].ki.dwFlags) for i in range(count)])
            return count

        self.user32 = Mock()
        self.user32.SendInput.side_effect = send
        self.held = set()
        self.user32.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if vk in self.held else 0
        self.enterContext(patch("src.injector.platform.system", return_value="Windows"))
        self.enterContext(patch.object(ctypes, "WinDLL", return_value=self.user32, create=True))
        self.enterContext(patch.object(ctypes, "set_last_error", create=True))
        self.enterContext(patch.object(ctypes, "get_last_error", return_value=0, create=True))
        self.enterContext(patch("src.injector.time.sleep"))

    def test_paste_releases_held_shift_and_alt_after_ctrl_down(self) -> None:
        self.held = {0x10, 0x12}
        report = type_text("ignored", paste=True)
        self.assertEqual(
            self.batches,
            [[(0x11, 0), (0x10, 2), (0x12, 2), (0x56, 0), (0x56, 2), (0x11, 2)]],
        )
        self.assertEqual(report.text_events_submitted, 6)

    def test_paste_without_held_modifiers_is_plain_ctrl_v(self) -> None:
        report = type_text("ignored", paste=True)
        self.assertEqual(self.batches, [[(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)]])
        self.assertEqual(report.text_events_submitted, 4)

    def test_partial_paste_reports_expected_count(self) -> None:
        self.held = {0x10}
        self.user32.SendInput.side_effect = lambda count, _b, _s: 2
        with self.assertRaises(InjectionError) as failed:
            type_text("ignored", paste=True)
        self.assertEqual(failed.exception.text_events_submitted, 2)
        self.assertEqual(failed.exception.text_events_expected, 5)

    def test_post_key_releases_held_shift_so_enter_is_not_shift_enter(self) -> None:
        self.held = {0x10}
        report = type_text("a", "enter")
        self.assertEqual(self.batches[1], [(0x10, 2), (0x0D, 0), (0x0D, 2)])
        self.assertEqual(report.post_key_events_submitted, 3)


if __name__ == "__main__":
    unittest.main()
