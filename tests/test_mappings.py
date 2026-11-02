import unittest

from src.config import (
    HOTKEY_OPTIONS,
    POST_KEY_OPTIONS,
    AppConfig,
    Hotkey,
)


class MappingTests(unittest.TestCase):
    def test_default_hotkey_is_ctrl_alt_space(self) -> None:
        self.assertEqual(AppConfig().hotkey, "ctrl+alt+key:0x20")
        parsed = Hotkey.parse(AppConfig().hotkey)
        self.assertEqual(parsed, Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20))

    def test_first_preset_is_ctrl_alt_space(self) -> None:
        self.assertEqual(HOTKEY_OPTIONS[0], ("ctrl+alt+key:0x20", "Ctrl+Alt+Space"))

    def test_all_presets_parse(self) -> None:
        unparsed = [value for value, _label in HOTKEY_OPTIONS if Hotkey.parse(value) is None]
        self.assertEqual(unparsed, [])

    def test_all_presets_validate(self) -> None:
        invalid = [
            value for value, _label in HOTKEY_OPTIONS if Hotkey.parse(value).validate() is not None
        ]
        self.assertEqual(invalid, [])

    def test_all_presets_relabel_to_their_option_label(self) -> None:
        labels = {value: Hotkey.parse(value).to_label() for value, _label in HOTKEY_OPTIONS}
        self.assertEqual(labels, dict(HOTKEY_OPTIONS))

    def test_presets_do_not_offer_bare_modifiers(self) -> None:
        bare = [
            value
            for value, _label in HOTKEY_OPTIONS
            if Hotkey.parse(value).code in (0x10, 0x11, 0x12)
        ]
        self.assertEqual(bare, [], "no bare modifier presets")

    def test_post_key_options_include_none(self) -> None:
        self.assertIn(("none", "None"), POST_KEY_OPTIONS)


if __name__ == "__main__":
    unittest.main()
