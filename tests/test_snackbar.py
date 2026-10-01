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

    def test_meter_distinguishes_waiting_from_quiet_and_resets_after_recording(self):
        from src.snackbar import RecordingSnackbar

        bar = RecordingSnackbar()
        self.addCleanup(bar.close)
        bar.show_state("Recording", (229, 57, 53))
        bar.set_input_status("USB microphone", 0.7, False)
        self.assertEqual(bar.input_status_text(), "Waiting for samples - USB microphone")
        self.assertEqual(bar.input_level(), 0.0)
        bar.set_input_status("USB microphone", 0.0, True)
        self.assertEqual(bar.input_status_text(), "Input level - USB microphone")
        self.assertEqual(bar.input_level(), 0.0)
        bar.set_input_status("USB microphone", 1.5, True)
        self.assertEqual(bar.input_level(), 1.0)
        bar.set_input_status("USB microphone", -0.1, True)
        self.assertEqual(bar.input_level(), 0.0)
        recording_height = bar.height()
        # Render the extended paint path, not just its stored values.
        self.assertFalse(bar.grab().isNull())
        bar.show_state("Processing", (255, 179, 0))
        bar.set_input_status("Late sample", 1.0, True)
        self.assertEqual(bar.input_status_text(), "")
        self.assertEqual(bar.input_level(), 0.0)
        self.assertLess(bar.height(), recording_height)
        bar.show_state("Recording", (229, 57, 53))
        self.assertIn("Waiting for samples", bar.input_status_text())
        bar.hide_state()
        self.assertEqual(bar.input_status_text(), "")

    def test_long_device_label_cannot_hide_waiting_or_quiet_observation(self):
        from src.snackbar import RecordingSnackbar

        bar = RecordingSnackbar()
        self.addCleanup(bar.close)
        label = "Very long USB microphone name " * 10
        bar.show_state("Recording", (229, 57, 53))
        bar.set_input_status(label, 0.0, False)
        waiting_text = bar.input_status_text()
        self.assertTrue(waiting_text.startswith("Waiting for samples - "))
        self.assertNotIn(label, waiting_text)
        self.assertFalse(bar.grab().isNull())
        bar.set_input_status(label, 0.0, True)
        quiet_text = bar.input_status_text()
        self.assertTrue(quiet_text.startswith("Input level - "))
        self.assertNotEqual(waiting_text, quiet_text)
        self.assertFalse(bar.grab().isNull())

    def test_larger_font_keeps_normal_device_names_and_bounds_long_names(self):
        from PySide6.QtGui import QGuiApplication
        from src.snackbar import RecordingSnackbar

        bar = RecordingSnackbar()
        self.addCleanup(bar.close)
        font = bar.font()
        font.setPointSize(18)
        bar.setFont(font)
        bar.show_state("Recording", (229, 57, 53))
        bar.set_input_status("USB microphone", 0.0, False)
        self.assertEqual(bar.input_status_text(), "Waiting for samples - USB microphone")
        bar.set_input_status("System Default (Actual USB mic)", 0.0, True)
        self.assertEqual(bar.input_status_text(), "Input level - System Default (Actual USB mic)")
        long_name = "Very long USB microphone name " * 10
        bar.set_input_status(long_name, 0.0, False)
        self.assertTrue(bar.input_status_text().startswith("Waiting for samples - "))
        self.assertNotIn(long_name, bar.input_status_text())
        self.assertLessEqual(
            bar.width(), QGuiApplication.primaryScreen().availableGeometry().width()
        )
        self.assertFalse(bar.grab().isNull())
