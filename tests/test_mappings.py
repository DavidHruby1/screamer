import unittest

from src.config import (
    HOTKEY_BINDINGS,
    HOTKEY_OPTIONS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    POST_KEY_OPTIONS,
    AppConfig,
)


class MappingTests(unittest.TestCase):
    def test_hotkey_options_do_not_offer_bare_modifiers(self) -> None:
        option_keys = {key for key, _label in HOTKEY_OPTIONS}

        self.assertNotIn("ctrl", option_keys)
        self.assertNotIn("alt", option_keys)
        self.assertNotIn("f13", option_keys)
        self.assertNotIn("f14", option_keys)

    def test_default_hotkey_is_laptop_friendly(self) -> None:
        self.assertEqual(AppConfig().hotkey, "ctrl_alt_space")
        self.assertEqual(HOTKEY_OPTIONS[0], ("ctrl_alt_space", "Ctrl+Alt+Space"))

    def test_hotkey_options_have_bindings_with_no_repeat(self) -> None:
        for key, _label in HOTKEY_OPTIONS:
            self.assertIn(key, HOTKEY_BINDINGS)
            self.assertTrue(HOTKEY_BINDINGS[key].modifiers & MOD_NOREPEAT)

        default = HOTKEY_BINDINGS["ctrl_alt_space"]
        self.assertTrue(default.modifiers & MOD_CONTROL)
        self.assertTrue(default.modifiers & MOD_ALT)

    def test_post_key_options_include_none(self) -> None:
        self.assertIn(("none", "None"), POST_KEY_OPTIONS)


if __name__ == "__main__":
    unittest.main()
