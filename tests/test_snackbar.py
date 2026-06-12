import os
import unittest

# Set before any PySide6 import so the helper tests need no display server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SnackbarContentTests(unittest.TestCase):
    def test_recording_maps_to_red_label(self):
        from src.snackbar import snackbar_content_for

        content = snackbar_content_for("recording")
        self.assertIsNotNone(content)
        label, rgb = content
        self.assertEqual(label, "Recording")
        self.assertEqual(rgb, (229, 57, 53))

    def test_processing_maps_to_amber_label(self):
        from src.snackbar import snackbar_content_for

        content = snackbar_content_for("processing")
        self.assertIsNotNone(content)
        label, rgb = content
        self.assertEqual(label, "Processing")
        self.assertEqual(rgb, (255, 179, 0))

    def test_idle_and_unknown_map_to_none(self):
        from src.snackbar import snackbar_content_for

        self.assertIsNone(snackbar_content_for("idle"))
        self.assertIsNone(snackbar_content_for("nonsense"))


class SnackbarGeometryTests(unittest.TestCase):
    def test_centers_horizontally_and_sits_above_bottom_margin(self):
        from PySide6.QtCore import QPoint, QRect, QSize
        from src.snackbar import bottom_center_xy

        avail = QRect(0, 0, 1920, 1040)  # 1920x1080 minus a 40px taskbar
        size = QSize(160, 40)
        point = bottom_center_xy(avail, size, margin=48)

        self.assertIsInstance(point, QPoint)
        self.assertEqual(point.x(), (1920 - 160) // 2)  # 880
        self.assertEqual(point.y(), 0 + 1040 - 40 - 48)  # 952

    def test_respects_non_zero_screen_origin(self):
        from PySide6.QtCore import QRect, QSize
        from src.snackbar import bottom_center_xy

        avail = QRect(100, 50, 800, 600)
        size = QSize(200, 50)
        point = bottom_center_xy(avail, size, margin=10)

        self.assertEqual(point.x(), 100 + (800 - 200) // 2)  # 400
        self.assertEqual(point.y(), 50 + 600 - 50 - 10)  # 590


class SnackbarWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as e:  # PySide6 not installed in this environment.
            raise unittest.SkipTest(f"PySide6 unavailable: {e}")
        cls._app = QApplication.instance() or QApplication([])

    def test_show_state_makes_widget_visible_then_hide_keeps_object(self):
        from src.snackbar import RecordingSnackbar

        bar = RecordingSnackbar()
        self.assertFalse(bar.isVisible())

        bar.show_state("Recording", (229, 57, 53))
        self.assertTrue(bar.isVisible())
        self.assertEqual(bar.current_label(), "Recording")

        # Switching content while visible updates the label in place.
        bar.show_state("Processing", (255, 179, 0))
        self.assertEqual(bar.current_label(), "Processing")
        self.assertTrue(bar.isVisible())

        # hide_state starts a fade; the object survives and pulse stops.
        bar.hide_state()
        self.assertFalse(bar.is_pulsing())

    def test_is_click_through_and_non_focusable(self):
        from PySide6.QtCore import Qt
        from src.snackbar import RecordingSnackbar

        bar = RecordingSnackbar()
        self.assertTrue(bar.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(bar.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertEqual(bar.focusPolicy(), Qt.FocusPolicy.NoFocus)
