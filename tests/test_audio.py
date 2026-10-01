import io
import unittest
import wave
from unittest.mock import patch

import numpy as np

from src.audio import AudioDeviceIdentity, AudioRecorder, default_input_device_id, resolve_device
from src.config import DEFAULT_RMS_THRESHOLD
from src.utils import AppError, ScreamerError


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
    def test_name_resolution_prefers_exact_match_over_earlier_substring(self) -> None:
        fake_sd = FakeSoundDeviceDefaults(
            (0, -1),
            {
                0: {"name": "Microphone Pro", "max_input_channels": 1},
                1: {"name": "Microphone", "max_input_channels": 1},
            },
        )
        with patch("src.audio.sd", fake_sd):
            self.assertEqual(resolve_device(42, "Microphone"), 1)

    def test_explicit_selection_requires_consistent_unique_identity(self) -> None:
        devices = [(0, "Laptop"), (1, "USB mic (Default input)")]
        self.assertEqual(resolve_device(1, " USB MIC ", devices=devices), 1)
        self.assertEqual(resolve_device(1, "", devices=devices), 1)
        self.assertEqual(resolve_device(42, "USB mic", devices=devices), 1)
        for device_id, name, available in (
            (0, "USB mic", devices),
            (42, "", devices),
            (42, "USB", devices),
            (42, "Absent mic", devices),
            (1, "USB mic", [(1, "USB mic"), (2, "USB mic")]),
        ):
            with self.subTest(device_id=device_id, name=name, available=available):
                with self.assertRaises(ScreamerError) as error:
                    resolve_device(device_id, name, devices=available)
                self.assertEqual(error.exception.code, AppError.MIC_UNAVAILABLE)

    def test_intentional_default_defers_to_stream_without_explicit_name_matching(self) -> None:
        with patch("src.audio.list_devices") as enumerate_devices:
            self.assertIsNone(resolve_device(None, "Old microphone"))
        enumerate_devices.assert_not_called()

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


class AudioStreamTests(unittest.TestCase):
    def test_failed_start_closes_partially_opened_stream(self) -> None:
        class Stream:
            closed = False
            device = 2

            def start(self):
                raise OSError("device busy")

            def close(self, *, ignore_errors):
                self.closed = True

        stream = Stream()
        with patch("src.audio.sd") as sd:
            sd.InputStream.return_value = stream
            recorder = AudioRecorder()
            with self.assertRaises(ScreamerError) as error:
                recorder.start()
        self.assertEqual(error.exception.code, AppError.MIC_UNAVAILABLE)
        self.assertTrue(stream.closed)
        self.assertFalse(recorder.is_recording)

    def test_stop_closes_even_when_stream_stop_fails(self) -> None:
        class Stream:
            closed = False

            def stop(self, *, ignore_errors):
                raise OSError("disconnected")

            def close(self, *, ignore_errors):
                self.closed = True

        stream = Stream()
        recorder = AudioRecorder()
        recorder._stream = stream
        with self.assertRaises(ScreamerError) as error:
            recorder.stop()
        self.assertEqual(error.exception.code, AppError.MIC_DISCONNECTED)
        self.assertTrue(stream.closed)
        self.assertFalse(recorder.is_recording)


class FakeCaptureStream:
    device = 2

    def __init__(self, callback, finished_callback) -> None:
        self.callback = callback
        self.finished_callback = finished_callback
        self.start_error = False
        self.stop_error = False
        self.close_error = False
        self.closed = False

    def feed(self, samples, status="") -> None:
        self.callback(samples, len(samples), None, status)

    def start(self) -> None:
        if self.start_error:
            self.feed(np.full((160, 1), 1200, dtype=np.int16))
            raise OSError("device busy")

    def stop(self, *, ignore_errors) -> None:
        assert ignore_errors is False
        # Backends may finish an in-flight callback while stop is in progress.
        self.feed(np.full((160, 1), 5000, dtype=np.int16))
        self.finished_callback()
        if self.stop_error:
            raise OSError("stop failed")

    def close(self, *, ignore_errors) -> None:
        assert ignore_errors is False
        self.closed = True
        if self.close_error:
            raise OSError("close failed")


class FakeCaptureBackend:
    PortAudioError = OSError

    def __init__(self) -> None:
        self.stream = None
        self.start_error = False

    def InputStream(self, **kwargs):
        self.stream = FakeCaptureStream(kwargs["callback"], kwargs["finished_callback"])
        self.stream.start_error = self.start_error
        return self.stream

    def query_devices(self, device_id, kind):
        if device_id != 2 or kind != "input":
            raise ValueError("missing device")
        return {"name": "Actual USB microphone"}


class AudioCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeCaptureBackend()
        patcher = patch("src.audio.sd", self.backend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = 10.0
        clock = patch("src.audio.time.monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.recorder = AudioRecorder()
        self.recorder.start()
        self.addCleanup(self._close_recorder)

    def _close_recorder(self) -> None:
        if self.recorder.is_recording:
            try:
                self.recorder.stop()
            except ScreamerError:
                pass

    def test_latest_level_and_actual_identity_with_unchanged_wav_samples(self) -> None:
        initial = self.recorder.snapshot()
        self.assertFalse(initial.has_callback_data)
        self.assertEqual(initial.level_rms, 0.0)
        self.assertEqual(initial.device, AudioDeviceIdentity(2, "Actual USB microphone"))
        samples = np.array([[1200], [-1200], [1200], [-1200]], dtype=np.int16)
        self.backend.stream.feed(samples)
        capture = self.recorder.snapshot()
        self.assertTrue(capture.has_callback_data)
        self.assertEqual(capture.level_rms, 1200.0)
        self.backend.stream.feed(np.full((4, 1), 600, dtype=np.int16))
        self.assertEqual(self.recorder.snapshot().level_rms, 600.0)
        self.now = 11.0
        wav = self.recorder.stop()
        with wave.open(io.BytesIO(wav), "rb") as audio:
            self.assertEqual(
                (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (16000, 1, 2)
            )
            self.assertEqual(
                audio.readframes(8),
                samples.tobytes() + np.full((4, 1), 600, dtype=np.int16).tobytes(),
            )
        self.assertEqual(self.recorder._frames, [])
        self.backend.stream.feed(np.full((160, 1), 9000, dtype=np.int16))
        self.assertEqual(self.recorder.snapshot().level_rms, 600.0)
        self.assertEqual(self.recorder._frames, [])

    def test_captured_zero_and_quiet_input_are_not_missing_data(self) -> None:
        for value in (0, 2):
            with self.subTest(value=value):
                if not self.recorder.is_recording:
                    self.recorder.start()
                self.backend.stream.feed(np.full((160, 1), value, dtype=np.int16))
                capture = self.recorder.snapshot()
                self.assertTrue(capture.has_callback_data)
                self.assertEqual(capture.level_rms, value)
                self.now += 1.0
                self.assertEqual(self.recorder.stop(), b"")
                self.assertEqual(self.recorder.rms_threshold, DEFAULT_RMS_THRESHOLD)

    def test_no_callback_after_valid_duration_is_capture_error(self) -> None:
        self.now = 11.0
        with self.assertRaises(ScreamerError) as error:
            self.recorder.stop()
        self.assertEqual(error.exception.code, AppError.MIC_DISCONNECTED)
        self.assertIn("No microphone samples", error.exception.detail)
        self.assertEqual(self.recorder._frames, [])
        self.assertFalse(self.recorder.is_recording)

    def test_short_capture_still_discards_without_no_data_error(self) -> None:
        self.now = 10.1
        self.assertEqual(self.recorder.stop(), b"")

    def test_transient_status_is_observation_not_fatal_failure(self) -> None:
        self.backend.stream.feed(np.full((160, 1), 1200, dtype=np.int16), "input overflow")
        capture = self.recorder.snapshot()
        self.assertEqual(capture.input_status, "input overflow")
        self.assertIsNone(capture.capture_error)
        self.now = 11.0
        self.assertTrue(self.recorder.stop())

    def test_unexpected_stream_finish_discards_partial_capture(self) -> None:
        self.backend.stream.feed(np.full((160, 1), 1200, dtype=np.int16))
        self.backend.stream.finished_callback()
        self.assertIsNotNone(self.recorder.snapshot().capture_error)
        self.now = 11.0
        with self.assertRaises(ScreamerError) as error:
            self.recorder.stop()
        self.assertEqual(error.exception.code, AppError.MIC_DISCONNECTED)
        self.assertEqual(self.recorder._frames, [])

    def test_stop_and_close_failure_never_produce_partial_wav(self) -> None:
        for failure in ("stop_error", "close_error"):
            with self.subTest(failure=failure):
                if not self.recorder.is_recording:
                    self.recorder.start()
                self.backend.stream.feed(np.full((160, 1), 1200, dtype=np.int16))
                setattr(self.backend.stream, failure, True)
                self.now += 1.0
                with self.assertRaises(ScreamerError) as error:
                    self.recorder.stop()
                self.assertEqual(error.exception.code, AppError.MIC_DISCONNECTED)
                self.assertTrue(self.backend.stream.closed)
                self.assertFalse(self.recorder.is_recording)
                self.assertEqual(self.recorder._frames, [])

    def test_start_failure_clears_partial_frames_and_ignores_late_callback(self) -> None:
        self.recorder.stop()
        self.backend.start_error = True
        with self.assertRaises(ScreamerError) as error:
            self.recorder.start()
        self.assertEqual(error.exception.code, AppError.MIC_UNAVAILABLE)
        self.assertTrue(self.backend.stream.closed)
        self.backend.stream.feed(np.full((160, 1), 5000, dtype=np.int16))
        self.assertEqual(self.recorder._frames, [])
        self.assertFalse(self.recorder.is_recording)


if __name__ == "__main__":
    unittest.main()
