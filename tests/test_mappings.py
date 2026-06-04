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

    def test_all_presets_parse_validate_and_relabel(self) -> None:
        for value, label in HOTKEY_OPTIONS:
            hk = Hotkey.parse(value)
            self.assertIsNotNone(hk, f"preset {value!r} must parse")
            self.assertIsNone(hk.validate(), f"preset {value!r} must be valid")
            self.assertEqual(hk.to_label(), label, f"preset {value!r} label mismatch")

    def test_presets_do_not_offer_bare_modifiers(self) -> None:
        for value, _label in HOTKEY_OPTIONS:
            hk = Hotkey.parse(value)
            self.assertNotIn(hk.code, (0x10, 0x11, 0x12), "no bare modifier presets")

    def test_post_key_options_include_none(self) -> None:
        self.assertIn(("none", "None"), POST_KEY_OPTIONS)


if __name__ == "__main__":
    unittest.main()
