import unittest
from unittest.mock import patch

import numpy as np

from src.audio import AudioRecorder
from src.config import DEFAULT_RMS_THRESHOLD


class FakeSoundDevice:
    def __init__(self, recording: np.ndarray | None = None, error: Exception | None = None) -> None:
        self.recording = recording
        self.error = error

    def rec(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.recording

    def wait(self) -> None:
        return None


class AudioCalibrationTests(unittest.TestCase):
    def test_calibration_uses_low_default_for_silent_input(self) -> None:
        fake_sd = FakeSoundDevice(np.zeros((16000, 1), dtype=np.int16))

        with patch("src.audio.sd", fake_sd):
            threshold = AudioRecorder().calibrate(1.0)

        self.assertEqual(threshold, DEFAULT_RMS_THRESHOLD)

    def test_calibration_scales_audible_noise_floor(self) -> None:
        fake_sd = FakeSoundDevice(np.full((16000, 1), 10, dtype=np.int16))

        with patch("src.audio.sd", fake_sd):
            threshold = AudioRecorder().calibrate(1.0)

        self.assertEqual(threshold, 20.0)

    def test_calibration_failure_uses_low_default(self) -> None:
        fake_sd = FakeSoundDevice(error=RuntimeError("no mic"))

        with patch("src.audio.sd", fake_sd):
            threshold = AudioRecorder().calibrate(1.0)

        self.assertEqual(threshold, DEFAULT_RMS_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
