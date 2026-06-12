import unittest

from src.injector import _utf16_units


class Utf16UnitsTests(unittest.TestCase):
    def test_ascii_maps_one_to_one(self) -> None:
        self.assertEqual([ord(u) for u in _utf16_units("hi")], [0x68, 0x69])

    def test_emoji_splits_into_surrogate_pair(self) -> None:
        units = _utf16_units("\U0001F600")
        self.assertEqual([ord(u) for u in units], [0xD83D, 0xDE00])

    def test_empty_text_yields_no_units(self) -> None:
        self.assertEqual(_utf16_units(""), [])


if __name__ == "__main__":
    unittest.main()
