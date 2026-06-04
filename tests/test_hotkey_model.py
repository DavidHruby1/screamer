import unittest

from src.config import (
    Hotkey,
    MOUSE_X1,
    MOUSE_X2,
    MOUSE_MIDDLE,
)


class HotkeyModelTests(unittest.TestCase):
    def test_canonical_roundtrip_key_with_mods(self):
        hk = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20)
        self.assertEqual(hk.to_canonical(), "ctrl+alt+key:0x20")
        self.assertEqual(Hotkey.parse("ctrl+alt+key:0x20"), hk)

    def test_canonical_orders_mods_consistently(self):
        a = Hotkey(frozenset({"alt", "ctrl"}), "key", 0x44)
        b = Hotkey(frozenset({"ctrl", "alt"}), "key", 0x44)
        self.assertEqual(a.to_canonical(), b.to_canonical())
        self.assertEqual(a.to_canonical(), "ctrl+alt+key:0x44")

    def test_canonical_roundtrip_bare_key(self):
        hk = Hotkey(frozenset(), "key", 0x91)  # Scroll Lock
        self.assertEqual(hk.to_canonical(), "key:0x91")
        self.assertEqual(Hotkey.parse("key:0x91"), hk)

    def test_canonical_roundtrip_mouse(self):
        hk = Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1)
        self.assertEqual(hk.to_canonical(), "ctrl+mouse:x1")
        self.assertEqual(Hotkey.parse("ctrl+mouse:x1"), hk)
        self.assertEqual(Hotkey.parse("mouse:x2"), Hotkey(frozenset(), "mouse", MOUSE_X2))
        self.assertEqual(Hotkey.parse("mouse:middle"), Hotkey(frozenset(), "mouse", MOUSE_MIDDLE))

    def test_parse_legacy_keys_migrate(self):
        self.assertEqual(Hotkey.parse("ctrl_alt_space"), Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20))
        self.assertEqual(Hotkey.parse("scroll_lock"), Hotkey(frozenset(), "key", 0x91))
        self.assertEqual(Hotkey.parse("pause"), Hotkey(frozenset(), "key", 0x13))

    def test_parse_invalid_returns_none(self):
        self.assertIsNone(Hotkey.parse(""))
        self.assertIsNone(Hotkey.parse("garbage"))
        self.assertIsNone(Hotkey.parse("ctrl+mouse:x9"))

    def test_label_human_readable(self):
        self.assertEqual(Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20).to_label(), "Ctrl+Alt+Space")
        self.assertEqual(Hotkey(frozenset(), "key", 0x91).to_label(), "Scroll Lock")
        self.assertEqual(Hotkey(frozenset({"ctrl"}), "mouse", MOUSE_X1).to_label(), "Ctrl+Mouse Back")
        self.assertEqual(Hotkey(frozenset(), "mouse", MOUSE_MIDDLE).to_label(), "Mouse Middle")

    def test_validate_ok_with_modifier(self):
        self.assertIsNone(Hotkey(frozenset({"ctrl", "alt"}), "key", 0x20).validate())

    def test_validate_ok_safe_standalone(self):
        self.assertIsNone(Hotkey(frozenset(), "key", 0x91).validate())   # Scroll Lock
        self.assertIsNone(Hotkey(frozenset(), "key", 0x70).validate())   # F1
        self.assertIsNone(Hotkey(frozenset(), "mouse", MOUSE_X1).validate())

    def test_validate_rejects_bare_normal_key(self):
        self.assertIsNotNone(Hotkey(frozenset(), "key", 0x20).validate())  # bare Space
        self.assertIsNotNone(Hotkey(frozenset(), "key", 0x41).validate())  # bare A

    def test_validate_rejects_modifier_as_trigger(self):
        self.assertIsNotNone(Hotkey(frozenset({"ctrl"}), "key", 0x11).validate())  # trigger is Ctrl

    def test_validate_rejects_unknown_mouse_button(self):
        self.assertIsNotNone(Hotkey(frozenset(), "mouse", 99).validate())


if __name__ == "__main__":
    unittest.main()
