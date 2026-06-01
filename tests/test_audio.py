import unittest
from unittest.mock import patch

import numpy as np

from src.audio import AudioRecorder, default_input_device_id
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


class FakeInputOutputPair:
    def __init__(self, input_device, output_device=None) -> None:
        self._pair = [input_device, output_device]

    def __getitem__(self, index):
        if index == "input":
            index = 0
        elif index == "output":
            index = 1
        return self._pair[index]


class FakeDefault:
    def __init__(self, device) -> None:
        self.device = device


class FakeSoundDeviceDefaults:
    def __init__(self, device, devices=None) -> None:
        self.default = FakeDefault(device)
        self.devices = devices or {}

    def query_devices(self, device=None, kind=None):
        if device is None:
            return list(self.devices.values())
        return self.devices[device]


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


class AudioDeviceDefaultTests(unittest.TestCase):
    def test_default_input_device_accepts_sounddevice_pair(self) -> None:
        fake_sd = FakeSoundDeviceDefaults(FakeInputOutputPair(3, 9))

        with patch("src.audio.sd", fake_sd):
            self.assertEqual(default_input_device_id(), 3)

    def test_default_input_device_accepts_tuple(self) -> None:
        fake_sd = FakeSoundDeviceDefaults((4, 8))

        with patch("src.audio.sd", fake_sd):
            self.assertEqual(default_input_device_id(), 4)

    def test_default_input_device_resolves_name_default(self) -> None:
        fake_sd = FakeSoundDeviceDefaults("Microphone", {"Microphone": {"index": 5}})

        with patch("src.audio.sd", fake_sd):
            self.assertEqual(default_input_device_id(), 5)

    def test_default_input_device_resolves_name_default_without_index(self) -> None:
        fake_sd = FakeSoundDeviceDefaults(
            "Microphone",
            {
                "Speaker": {"name": "Speaker", "max_input_channels": 0},
                "Microphone": {"name": "Microphone", "max_input_channels": 1},
            },
        )

        with patch("src.audio.sd", fake_sd):
            self.assertEqual(default_input_device_id(), 1)

    def test_default_input_device_returns_none_for_missing_default(self) -> None:
        fake_sd = FakeSoundDeviceDefaults(FakeInputOutputPair(-1, 9))

        with patch("src.audio.sd", fake_sd):
            self.assertIsNone(default_input_device_id())


if __name__ == "__main__":
    unittest.main()
