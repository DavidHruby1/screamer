"""Tests for _StreamingWorker — chunk ordering, NO_SPEECH swallowing, warning propagation."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QCoreApplication
from PySide6.QtWidgets import QApplication

from src.config import AppConfig
from src.main import _StreamingWorker
from src.utils import AppError, PipelineResult, ScreamerError


class _Collector(QObject):
    """Collects signal emissions from _StreamingWorker for test assertions."""

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
    """Process Qt events until the collector signals completion or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if collector.done.is_set():
            return
        QCoreApplication.processEvents()
        time.sleep(0.005)
    raise TimeoutError("Timed out waiting for worker signal")


class StreamingWorkerTests(unittest.TestCase):
    """Tests the full _StreamingWorker pipeline with mocked STT/rewrite/type."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_worker(self) -> tuple[_StreamingWorker, _Collector, threading.Event]:
        cancel = threading.Event()
        config = AppConfig()
        worker = _StreamingWorker(config, cancel)
        collector = _Collector()
        worker.succeeded.connect(collector.on_succeeded)
        worker.failed.connect(collector.on_failed)
        worker.cancelled.connect(collector.on_cancelled)
        return worker, collector, cancel

    def test_two_chunks_joined_with_space(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", side_effect=[
                PipelineResult(text="hello"),
                PipelineResult(text="world"),
            ]),
            patch("src.main.rewrite", return_value=PipelineResult(text="hello world")),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"chunk1")
            worker.enqueue(b"chunk2")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.text, "hello world")
        self.assertEqual(collector.result.warnings, [])

    def test_single_chunk_works(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", return_value=PipelineResult(text="hello")),
            patch("src.main.rewrite", return_value=PipelineResult(text="hello")),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"chunk1")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.text, "hello")

    def test_per_chunk_no_speech_swallowed(self) -> None:
        """One chunk produces NO_SPEECH, the other has text. No error."""
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", side_effect=[
                ScreamerError(AppError.NO_SPEECH),
                PipelineResult(text="hello"),
            ]),
            patch("src.main.rewrite", return_value=PipelineResult(text="hello")),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"silent")
            worker.enqueue(b"speech")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.text, "hello")

    def test_all_chunks_no_speech_causes_final_no_speech(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", side_effect=[
                ScreamerError(AppError.NO_SPEECH),
                ScreamerError(AppError.NO_SPEECH),
            ]),
            patch("src.main.rewrite"),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"silent1")
            worker.enqueue(b"silent2")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNone(collector.result)
        self.assertIsNotNone(collector.error)
        self.assertIsInstance(collector.error, ScreamerError)
        self.assertIs(collector.error.code, AppError.NO_SPEECH)

    def test_no_chunks_at_finish_causes_no_speech(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe"),
            patch("src.main.rewrite"),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNone(collector.result)
        self.assertIsNotNone(collector.error)
        self.assertIsInstance(collector.error, ScreamerError)
        self.assertIs(collector.error.code, AppError.NO_SPEECH)

    def test_fallback_warnings_survive_to_result(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", return_value=PipelineResult(
                text="hello", warnings=[AppError.STT_FALLBACK_USED],
            )),
            patch("src.main.rewrite", return_value=PipelineResult(text="hello")),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"chunk1")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.warnings, [AppError.STT_FALLBACK_USED])

    def test_rewrite_warnings_survive_to_result(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", return_value=PipelineResult(text="hello")),
            patch("src.main.rewrite", return_value=PipelineResult(
                text="hello", warnings=[AppError.LLM_FAILED],
            )),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"chunk1")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.warnings, [AppError.LLM_FAILED])

    def test_rewrite_and_stt_warnings_both_survive(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", return_value=PipelineResult(
                text="hello", warnings=[AppError.STT_FALLBACK_USED],
            )),
            patch("src.main.rewrite", return_value=PipelineResult(
                text="hello", warnings=[AppError.LLM_FAILED],
            )),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"chunk1")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNotNone(collector.result)
        self.assertEqual(collector.result.warnings, [AppError.STT_FALLBACK_USED, AppError.LLM_FAILED])

    def test_cancellation_before_any_chunk_emits_cancelled(self) -> None:
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
        self.assertIsNone(collector.result)
        self.assertIsNone(collector.error)

    def test_cancellation_during_processing_no_chunks(self) -> None:
        """Cancel while waiting for queue items; worker should notice."""
        worker, collector, cancel = self._make_worker()
        with (
            patch("src.main.transcribe"),
            patch("src.main.rewrite"),
            patch("src.main.type_text"),
        ):
            worker.start()
            time.sleep(0.1)
            cancel.set()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertTrue(collector.was_cancelled)

    def test_stt_failure_other_than_no_speech_emits_failed(self) -> None:
        worker, collector, _cancel = self._make_worker()
        with (
            patch("src.main.transcribe", side_effect=ScreamerError(AppError.STT_FAILED)),
            patch("src.main.rewrite"),
            patch("src.main.type_text"),
        ):
            worker.start()
            worker.enqueue(b"chunk1")
            worker.finish()
            _wait_for_signal(collector)
            worker.wait(5000)

        self.assertIsNone(collector.result)
        self.assertIsNotNone(collector.error)
        self.assertIsInstance(collector.error, ScreamerError)
        self.assertIs(collector.error.code, AppError.STT_FAILED)
