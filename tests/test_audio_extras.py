import io
import unittest
import wave
from unittest.mock import patch

import numpy as np

from src.audio import AudioRecorder, SystemAudioMute, trim_silence
from tests.test_audio import FakeCaptureBackend

RATE = 16000


def _clip(*segments: tuple[float, int]) -> np.ndarray:
    """Build (N, 1) int16 audio from (seconds, constant amplitude) segments."""
    parts = [np.full((int(sec * RATE), 1), amp, dtype=np.int16) for sec, amp in segments]
    return np.concatenate(parts, axis=0)


class TrimSilenceTests(unittest.TestCase):
    def test_trims_leading_and_trailing_keeping_padding(self) -> None:
        audio = _clip((1.0, 0), (0.5, 3000), (1.0, 0))
        out = trim_silence(audio, RATE, min_threshold=50.0, pad_seconds=0.3)
        self.assertEqual(out.shape, (int(1.1 * RATE), 1))
        self.assertEqual(int(np.count_nonzero(out)), int(0.5 * RATE))
        self.assertEqual(int(out[int(0.3 * RATE), 0]), 3000)
        self.assertEqual(int(out[int(0.3 * RATE) - 1, 0]), 0)

    def test_middle_silence_preserved(self) -> None:
        audio = _clip((1.0, 0), (0.3, 3000), (2.0, 0), (0.3, 3000), (1.0, 0))
        out = trim_silence(audio, RATE, min_threshold=50.0, pad_seconds=0.1)
        self.assertEqual(len(out), int((0.1 + 0.3 + 2.0 + 0.3 + 0.1) * RATE))

    def test_all_silent_or_quiet_input_unchanged(self) -> None:
        for amp in (0, 20):
            with self.subTest(amp=amp):
                audio = _clip((1.0, amp))
                self.assertIs(trim_silence(audio, RATE, min_threshold=50.0), audio)

    def test_one_dimensional_shape_supported(self) -> None:
        audio = _clip((1.0, 0), (0.5, 3000), (1.0, 0)).reshape(-1)
        out = trim_silence(audio, RATE, min_threshold=50.0, pad_seconds=0.0)
        self.assertEqual(out.ndim, 1)
        self.assertEqual(len(out), int(0.5 * RATE))

    def test_shape_n1_preserved(self) -> None:
        out = trim_silence(_clip((0.5, 0), (0.5, 3000)), RATE, min_threshold=50.0)
        self.assertEqual(out.ndim, 2)
        self.assertEqual(out.shape[1], 1)

    def test_padding_clamped_at_edges(self) -> None:
        audio = _clip((0.1, 0), (0.5, 3000), (0.1, 0))
        out = trim_silence(audio, RATE, min_threshold=50.0, pad_seconds=0.3)
        self.assertEqual(len(out), len(audio))

    def test_noise_floor_raises_threshold_above_minimum(self) -> None:
        # Steady hum at 200 is ignored (3x floor = 600), speech at 3000 is kept.
        audio = _clip((1.0, 200), (0.5, 3000), (1.0, 200))
        out = trim_silence(audio, RATE, min_threshold=50.0, pad_seconds=0.0)
        self.assertEqual(len(out), int(0.5 * RATE))


class RecorderTrimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeCaptureBackend()
        for target, kwargs in (
            ("src.audio.sd", {"new": self.backend}),
            ("src.audio.time.monotonic", {"side_effect": lambda: self.now}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = 10.0

    def _record(self, trim: bool) -> int:
        recorder = AudioRecorder()
        self.assertTrue(recorder.trim_silence)
        recorder.trim_silence = trim
        self.now = 10.0
        recorder.start()
        self.backend.stream.feed(_clip((1.0, 0), (0.5, 3000), (1.0, 0)))
        self.now = 12.5
        with wave.open(io.BytesIO(recorder.stop()), "rb") as wav:
            return wav.getnframes()

    def test_stop_trims_when_enabled(self) -> None:
        self.assertEqual(self._record(True), int(1.1 * RATE))

    def test_stop_keeps_full_audio_when_disabled(self) -> None:
        self.assertEqual(self._record(False), int(2.5 * RATE))

    def test_level_only_preview_tracks_rms_without_buffering_frames(self) -> None:
        recorder = AudioRecorder()
        self.assertTrue(recorder.retain_frames)
        recorder.retain_frames = False
        recorder.start()
        self.backend.stream.feed(_clip((1.0, 3000)))
        self.assertGreater(recorder.snapshot().level_rms, 2000)
        self.assertEqual(recorder._frames, [])


class SystemAudioMuteTests(unittest.TestCase):
    def test_noop_off_windows(self) -> None:
        mute = SystemAudioMute()
        with (
            patch("src.audio.platform.system", return_value="Linux"),
            patch("src.audio.SystemAudioMute._with_endpoint") as endpoint,
        ):
            mute.mute()
            mute.restore()
            mute.restore()
        endpoint.assert_not_called()

    def test_failure_is_logged_not_raised(self) -> None:
        mute = SystemAudioMute()
        with (
            patch("src.audio.platform.system", return_value="Windows"),
            patch("src.audio.SystemAudioMute._with_endpoint", side_effect=OSError("no device")),
            self.assertLogs("src.audio", "WARNING"),
        ):
            mute.mute()
        mute.restore()

    def test_restore_only_unmutes_what_we_muted(self) -> None:
        calls = []
        mute = SystemAudioMute()
        with (
            patch("src.audio.platform.system", return_value="Windows"),
            patch(
                "src.audio.SystemAudioMute._with_endpoint",
                side_effect=lambda action: calls.append(action) or True,
            ),
        ):
            mute.mute()
            mute.mute()  # already muted by us: no second call
            mute.restore()
            mute.restore()  # idempotent
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
