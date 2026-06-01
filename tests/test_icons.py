import os
import unittest

# Render off-screen so the test needs no display server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class IconCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:  # PySide6 not installed in this environment.
            raise unittest.SkipTest(f"PySide6 unavailable: {e}")
        cls._app = QApplication.instance() or QApplication([])

    def test_same_state_returns_cached_pixmap(self) -> None:
        from src.icons import TrayState, get_icon_pixmap

        self.assertIs(get_icon_pixmap(TrayState.IDLE), get_icon_pixmap(TrayState.IDLE))

    def test_distinct_states_return_distinct_pixmaps(self) -> None:
        from src.icons import TrayState, get_icon_pixmap

        self.assertIsNot(
            get_icon_pixmap(TrayState.IDLE), get_icon_pixmap(TrayState.RECORDING)
        )


if __name__ == "__main__":
    unittest.main()
