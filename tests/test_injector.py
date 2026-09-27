import unittest
import ctypes
from unittest.mock import Mock, patch

from src.injector import _utf16_units, type_text
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
        user32.SendInput.side_effect = send
        with (
            patch("src.injector.platform.system", return_value="Windows"),
            patch.object(ctypes, "WinDLL", return_value=user32, create=True),
            patch("src.injector.time.sleep"),
        ):
            type_text("", "enter")
        self.assertEqual(events, [(2, 0x0D, 0, 0x0D, 2)])

    def test_post_key_partial_batch_reports_injection_failure(self) -> None:
        user32 = Mock()
        user32.SendInput.return_value = 1
        with (
            patch("src.injector.platform.system", return_value="Windows"),
            patch.object(ctypes, "WinDLL", return_value=user32, create=True),
            patch.object(ctypes, "get_last_error", return_value=0, create=True),
            patch("src.injector.time.sleep"),
        ):
            with self.assertRaises(ScreamerError) as error:
                type_text("", "enter")
        self.assertEqual(error.exception.code, AppError.INJECTION_FAILED)
        self.assertEqual(user32.SendInput.call_count, 1)


if __name__ == "__main__":
    unittest.main()
