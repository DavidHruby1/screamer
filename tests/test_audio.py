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


class _FakeStream:
    """Minimal stand-in for sd.InputStream so stop() can be called in tests."""
    def stop(self) -> None:
        pass
    def close(self) -> None:
        pass


class AudioDrainTests(unittest.TestCase):
    def test_drain_returns_empty_when_no_frames(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = []
        result = recorder.drain()
        self.assertEqual(result, b"")

    def test_drain_returns_valid_wav_metadata(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = [np.full((16000, 1), 100, dtype=np.int16)]
        result = recorder.drain()
        self.assertGreater(len(result), 0)
        import io
        import wave
        with wave.open(io.BytesIO(result), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getsampwidth(), 2)

    def test_drain_clears_frames(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = [np.full((16000, 1), 100, dtype=np.int16)]
        result = recorder.drain()
        self.assertNotEqual(result, b"")
        self.assertEqual(len(recorder._frames), 0)

    def test_snapshot_does_not_clear_frames(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = [np.full((16000, 1), 100, dtype=np.int16)]

        result = recorder.snapshot_window(0)

        self.assertNotEqual(result.wav, b"")
        self.assertEqual(len(recorder._frames), 1)
        self.assertEqual(recorder.current_sample_count(), 16000)

    def test_snapshot_window_can_include_overlap(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = [np.full((48000, 1), 100, dtype=np.int16)]

        result = recorder.snapshot_window(16000, 48000)

        self.assertEqual(result.start_sample, 16000)
        self.assertEqual(result.end_sample, 48000)
        self.assertAlmostEqual(result.duration, 2.0, places=1)

    def test_snapshot_window_keeps_quiet_audio_for_streaming(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = [np.full((16000, 1), 1, dtype=np.int16)]
        recorder.rms_threshold = 1000.0

        result = recorder.snapshot_window(0, 16000)

        self.assertNotEqual(result.wav, b"")
        self.assertAlmostEqual(result.duration, 1.0, places=1)

    def test_drain_subsequent_returns_empty_after_clearing(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = [np.full((16000, 1), 100, dtype=np.int16)]
        recorder.drain()
        second = recorder.drain()
        self.assertEqual(second, b"")

    def test_drain_discards_very_short_audio(self) -> None:
        recorder = AudioRecorder()
        recorder._frames = [np.full((100, 1), 100, dtype=np.int16)]
        result = recorder.drain()
        self.assertEqual(result, b"")

    def test_stop_after_drain_only_returns_remaining_frames(self) -> None:
        recorder = AudioRecorder()
        initial_frames = [
            np.full((8000, 1), 100, dtype=np.int16),
            np.full((8000, 1), 100, dtype=np.int16),
        ]
        recorder._frames = list(initial_frames)
        drain_result = recorder.drain()
        self.assertNotEqual(drain_result, b"")

        tail_frames = [np.full((16000, 1), 100, dtype=np.int16)]
        recorder._frames = tail_frames
        recorder._stream = _FakeStream()
        stop_result = recorder.stop()
        self.assertNotEqual(stop_result, b"")
        import io
        import wave
        with wave.open(io.BytesIO(stop_result), "rb") as wf:
            tail_duration = wf.getnframes() / wf.getframerate()
        self.assertAlmostEqual(tail_duration, 1.0, places=1)


if __name__ == "__main__":
    unittest.main()
