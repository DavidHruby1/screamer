import unittest

from src.config import HOTKEY_BINDINGS, HOTKEY_OPTIONS, MOD_CONTROL, MOD_NOREPEAT, POST_KEY_OPTIONS


class MappingTests(unittest.TestCase):
    def test_hotkey_options_do_not_offer_bare_modifiers(self) -> None:
        option_keys = {key for key, _label in HOTKEY_OPTIONS}

        self.assertNotIn("ctrl", option_keys)
        self.assertNotIn("alt", option_keys)

    def test_hotkey_options_have_bindings_with_no_repeat(self) -> None:
        for key, _label in HOTKEY_OPTIONS:
            self.assertIn(key, HOTKEY_BINDINGS)
            self.assertTrue(HOTKEY_BINDINGS[key].modifiers & MOD_NOREPEAT)

        self.assertTrue(HOTKEY_BINDINGS["ctrl_scroll_lock"].modifiers & MOD_CONTROL)

    def test_post_key_options_include_none(self) -> None:
        self.assertIn(("none", "None"), POST_KEY_OPTIONS)


if __name__ == "__main__":
    unittest.main()
