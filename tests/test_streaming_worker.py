"""Tests for _StreamingWorker coalescing and final assembly."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import ANY, patch

from PySide6.QtCore import QObject, QCoreApplication
from PySide6.QtWidgets import QApplication

from src.audio import AudioSnapshot
from src.config import AppConfig
from src.main import _StreamingWorker
from src.stt import TranscriptionResult
from src.utils import AppError, PipelineResult, ScreamerError


class _Collector(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.result: PipelineResult | None = None
        self.error: Exception | None = None
        self.was_cancelled = False
        self.done = threading.Event()

    def on_succeeded(self, result: PipelineResult) -> None:
        self.result = result
        self.done.set()

    def on_failed(self, error: Exception) -> None:
        self.error = error
        self.done.set()

    def on_cancelled(self) -> None:
        self.was_cancelled = True
        self.done.set()


def _wait_for_signal(collector: _Collector, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if collector.done.is_set():
            return
        QCoreApplication.processEvents()
        time.sleep(0.005)
    raise TimeoutError("Timed out waiting for worker signal")


def _snapshot(name: str) -> AudioSnapshot:
    return AudioSnapshot(name.encode(), 0, 64000, 4.0)


class StreamingWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_worker(self) -> tuple[_StreamingWorker, _Collector, threading.Event]:
        cancel = threading.Event()
        worker = _StreamingWorker(AppConfig(), cancel)
        collector = _Collector()
        worker.succeeded.connect(collector.on_succeeded)
        worker.failed.connect(collector.on_failed)
        worker.cancelled.connect(collector.on_cancelled)
        return worker, collector, cancel

    def test_final_transcript_rewritten_and_typed_once(self) -> None:
        worker, collector, _cancel = self._make_worker()
        first_batch_seen = threading.Event()

        def fake_transcribe(audio: bytes, _config: AppConfig) -> TranscriptionResult:
            if audio == b"batch":
                first_batch_seen.set()
                return TranscriptionResult("Let's test the")
            return TranscriptionResult("Let's test the batching implementation")

        with (
            patch("src.main.transcribe", side_effect=fake_transcribe),
            patch("src.main.rewrite", return_value=PipelineResult("Let's test the batching implementation")) as rewrite_mock,
            patch("src.main.type_text") as type_mock,
        ):
            worker.start()
            worker.enqueue(_snapshot("batch"))
            self.assertTrue(first_batch_seen.wait(2.0))
            worker.finish(_snapshot("final"))
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.text, "Let's test the batching implementation")
        rewrite_mock.assert_called_once_with("Let's test the batching implementation", ANY)
        type_mock.assert_called_once()

    def test_pending_snapshots_are_coalesced_to_latest(self) -> None:
        worker, collector, _cancel = self._make_worker()
        seen_audio: list[bytes] = []

        def fake_transcribe(audio: bytes, _config: AppConfig) -> TranscriptionResult:
            seen_audio.append(audio)
            time.sleep(0.05)
            return TranscriptionResult("The deployment finished successfully")

        with (
            patch("src.main.transcribe", side_effect=fake_transcribe),
            patch("src.main.rewrite", return_value=PipelineResult("The deployment finished successfully")),
            patch("src.main.type_text"),
        ):
            worker.enqueue(_snapshot("stale-1"))
            worker.enqueue(_snapshot("stale-2"))
            worker.finish(_snapshot("final"))
            worker.start()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertNotIn(b"stale-1", seen_audio)
        self.assertNotIn(b"stale-2", seen_audio)
        self.assertIn(b"final", seen_audio)

    def test_punctuation_batch_does_not_output_dots_for_long_dictation(self) -> None:
        worker, collector, _cancel = self._make_worker()
        first_batch_seen = threading.Event()

        def fake_transcribe(audio: bytes, _config: AppConfig) -> TranscriptionResult:
            if audio == b"dot-batch":
                first_batch_seen.set()
                return TranscriptionResult("...")
            return TranscriptionResult("The deployment finished successfully after a longer dictation")

        with (
            patch("src.main.transcribe", side_effect=fake_transcribe),
            patch("src.main.rewrite", return_value=PipelineResult("The deployment finished successfully after a longer dictation")) as rewrite_mock,
            patch("src.main.type_text") as type_mock,
        ):
            worker.start()
            worker.enqueue(AudioSnapshot(b"dot-batch", 0, 5 * 16000, 5.0))
            self.assertTrue(first_batch_seen.wait(2.0))
            worker.finish(AudioSnapshot(b"final", 0, 6 * 16000, 6.0))
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        rewrite_mock.assert_called_once_with("The deployment finished successfully after a longer dictation", ANY)
        type_mock.assert_called_once()

    def test_no_speech_batch_does_not_erase_later_final_text(self) -> None:
        worker, collector, _cancel = self._make_worker()
        first_batch_seen = threading.Event()

        def fake_transcribe(audio: bytes, _config: AppConfig) -> TranscriptionResult:
            if audio == b"silent":
                first_batch_seen.set()
                raise ScreamerError(AppError.NO_SPEECH)
            return TranscriptionResult("The deployment finished successfully")

        with (
            patch("src.main.transcribe", side_effect=fake_transcribe),
            patch("src.main.rewrite", return_value=PipelineResult("The deployment finished successfully")),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(_snapshot("silent"))
            self.assertTrue(first_batch_seen.wait(2.0))
            worker.finish(_snapshot("final"))
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.text, "The deployment finished successfully")

    def test_all_speechless_recording_fails_no_speech(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", side_effect=ScreamerError(AppError.NO_SPEECH)),
            patch("src.main.rewrite"),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.finish(_snapshot("silent"))
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNone(collector.result)
        self.assertIsInstance(collector.error, ScreamerError)
        self.assertIs(collector.error.code, AppError.NO_SPEECH)

    def test_stt_and_rewrite_warnings_survive(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", return_value=TranscriptionResult(
                "hello", warnings=[AppError.STT_FALLBACK_USED],
            )),
            patch("src.main.rewrite", return_value=PipelineResult(
                "hello", warnings=[AppError.LLM_FAILED],
            )),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.finish(_snapshot("final"))
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.warnings, [AppError.STT_FALLBACK_USED, AppError.LLM_FAILED])

    def test_stt_failure_other_than_no_speech_emits_failed(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", side_effect=ScreamerError(AppError.STT_FAILED)),
            patch("src.main.rewrite"),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.finish(_snapshot("final"))
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNone(collector.result)
        self.assertIsInstance(collector.error, ScreamerError)
        self.assertIs(collector.error.code, AppError.STT_FAILED)

    def test_cancellation_before_work_emits_cancelled(self) -> None:
        worker, collector, cancel = self._make_worker()
        cancel.set()
        with (
            patch("src.main.transcribe"),
            patch("src.main.rewrite"),
            patch("src.main.type_text"),
        ):
            worker.start()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertTrue(collector.was_cancelled)


if __name__ == "__main__":
    unittest.main()
