import os
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.config import AppConfig
from src.injector import InjectionError, InjectionReport, WindowIdentity
from src.main import _Session, _Terminal, _WorkerThread
from src.results import DictationRecord
from src.recovery_dialog import RecoveryDialog
from src.utils import AppError, PipelineResult, ScreamerError
from tests.test_tray_menu import make_tray_app


class RecoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tray = make_tray_app()
        self.tray._apply_state = Mock()
        self.tray._on_error = Mock()
        self.target = WindowIdentity(100, 200, "C:\\Editor\\editor.exe")
        self.cfg = AppConfig(stt_language="cs", output_mode="type", llm_enabled=True)
        self.session = _Session(
            deepcopy(self.cfg), self.target, "session", "2026-09-30T12:00:00+00:00"
        )
        self.enterContext(patch.object(_WorkerThread, "start"))
        self.clipboard = QApplication.clipboard()
        original = self.clipboard.text()
        self.addCleanup(self.clipboard.setText, original)

    def start(self, **kwargs):
        self.tray._start_worker(b"wav", self.session, **kwargs)
        return self.tray._worker

    def test_raw_is_retained_before_rewrite_and_audio_reference_is_released(self):
        worker = self.start()

        def cleanup(raw, _config):
            self.assertEqual(raw, "Raw transcript")
            self.assertEqual(self.tray._latest.raw_text, "Raw transcript")
            self.assertEqual(worker._audio_wav, b"")
            raise RuntimeError("rewrite boundary failed")

        with (
            patch("src.main.transcribe", return_value=PipelineResult("Raw transcript")),
            patch("src.main.rewrite", side_effect=cleanup),
            patch("src.main.get_foreground_target", return_value=self.target),
            patch("src.main.type_text", side_effect=InjectionError("partial", 3)) as inject,
        ):
            worker.run()
        self.assertEqual(self.tray._latest.raw_text, "Raw transcript")
        self.assertEqual(self.tray._latest.rewrite_status, "failed")
        self.assertIn(AppError.LLM_FAILED, self.tray._latest.warnings)
        self.assertEqual(self.tray._latest.last_delivery.text_events_submitted, 3)
        self.assertEqual(inject.call_count, 1)
        self.assertIs(self.tray._worker, worker)
        self.tray._on_worker_finished()
        self.assertIsNone(self.tray._worker)

    def test_finished_before_terminal_keeps_busy_and_late_raw_cannot_erase_final(self):
        worker = self.start()
        self.tray._on_worker_finished()
        self.assertIs(self.tray._worker, worker)
        self.tray._on_hotkey_pressed()
        self.assertIs(self.tray._worker, worker)
        with patch("src.main.get_foreground_target", return_value=None):
            self.tray._on_terminal(_Terminal("session", "raw", "clean", "succeeded", ()))
        self.assertIsNone(self.tray._worker)
        self.assertEqual(self.tray._latest.final_text, "clean")
        self.tray._on_raw_ready("session", "raw", ())
        self.assertEqual(self.tray._latest.final_text, "clean")

    def test_disable_between_checkpoint_and_terminal_never_copies_or_types(self):
        worker = self.start()
        self.clipboard.setText("unchanged clipboard")

        def cleanup(raw, _config):
            self.tray._toggle_enabled(False)
            return PipelineResult(raw + " cleaned")

        with (
            patch("src.main.transcribe", return_value=PipelineResult("raw")),
            patch("src.main.rewrite", side_effect=cleanup),
            patch("src.main.type_text") as inject,
        ):
            worker.run()
        inject.assert_not_called()
        self.assertEqual(self.clipboard.text(), "unchanged clipboard")
        self.assertEqual(self.tray._latest.raw_text, "raw")
        self.assertEqual(self.tray._latest.rewrite_status, "cancelled")
        self.assertEqual(self.tray._latest.last_delivery.reason, "suppressed")
        self.tray._on_worker_finished()
        self.tray._toggle_enabled(True)
        self.assertEqual(self.clipboard.text(), "unchanged clipboard")

    def test_stt_retry_preserves_old_snapshot_until_success_and_never_auto_outputs(self):
        worker = self.start()
        with patch("src.main.transcribe", side_effect=ScreamerError(AppError.STT_FAILED)):
            worker.run()
        self.tray._on_worker_finished()
        self.assertEqual(self.tray._pending_audio[0], b"wav")
        self.tray._config.stt_language = "en"
        self.tray._retry_stt()
        retry = self.tray._worker
        self.assertIs(retry._config, self.session.config)
        self.assertEqual(retry._config.stt_language, "cs")
        self.assertIsNotNone(self.tray._pending_audio)
        with patch("src.main.transcribe", side_effect=ScreamerError(AppError.STT_FAILED)):
            retry.run()
        self.tray._on_worker_finished()
        self.assertIsNotNone(self.tray._pending_audio)
        self.tray._retry_stt()
        with (
            patch("src.main.transcribe", return_value=PipelineResult("recovered")),
            patch("src.main.rewrite") as rewrite,
            patch("src.main.type_text") as inject,
        ):
            self.tray._worker.run()
        inject.assert_not_called()
        rewrite.assert_not_called()
        self.assertEqual(self.tray._latest.raw_text, "recovered")
        self.assertIsNone(self.tray._pending_audio)
        self.tray._on_worker_finished()

    def test_cleanup_rerun_preserves_original_and_has_no_stt_or_automatic_output(self):
        original = DictationRecord(
            "original", self.session.started_at, "raw", "old final", "succeeded"
        )
        self.tray._latest = original
        self.tray._config = AppConfig(
            llm_enabled=True, llm_system_prompt="Current policy", llm_prompt_origin="user_saved"
        )
        self.tray._rerun_rewrite(original)
        with (
            patch("src.main.transcribe") as stt,
            patch("src.main.rewrite", return_value=PipelineResult("new candidate")),
            patch("src.main.type_text") as inject,
        ):
            self.tray._worker.run()
        self.tray._on_worker_finished()
        stt.assert_not_called()
        inject.assert_not_called()
        self.assertIs(self.tray._latest, original)
        self.assertEqual(self.tray._candidate.final_text, "new candidate")
        self.assertNotEqual(self.tray._candidate.id, original.id)

    def test_disabled_history_never_reads_or_writes_and_storage_failure_keeps_ram(self):
        self.tray._load_history()
        entry = DictationRecord("one", self.session.started_at, "raw")
        self.tray._retain(entry)
        self.tray._history.trim.assert_not_called()
        self.tray._history.upsert.assert_not_called()
        self.tray._config.history_enabled = True
        self.tray._history.upsert.side_effect = ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        self.tray._retain(entry)
        self.assertIs(self.tray._latest, entry)
        self.tray._on_error.assert_called_with(AppError.HISTORY_STORAGE_FAILED)

    def test_stt_success_during_orderly_exit_commits_raw_and_suppressed_attempt(self):
        self.tray._config.history_enabled = True
        self.tray._hotkey = Mock()
        self.tray._recorder = Mock(is_recording=False)
        self.tray._finish_exit = Mock()
        committed = []

        def commit(entry, _limit):
            committed.append(entry)
            return [entry]

        self.tray._history.upsert.side_effect = commit
        worker = self.start()

        def finish_stt(_audio, _config):
            self.tray._exit()
            return PipelineResult("raw completed during exit")

        with (
            patch("src.main.transcribe", side_effect=finish_stt),
            patch("src.main.type_text") as inject,
        ):
            worker.run()
        inject.assert_not_called()
        self.assertTrue(committed)
        self.assertEqual(committed[-1].raw_text, "raw completed during exit")
        self.assertEqual(committed[-1].last_delivery.reason, "suppressed")
        self.tray._on_worker_finished()


class RecoveryDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tray = make_tray_app()
        self.tray._apply_state = Mock()
        self.tray._on_error = Mock()
        self.target = WindowIdentity(100, 200)
        self.entry = DictationRecord(
            "one", "2026-09-30T12:00:00+00:00", "raw", "final", "succeeded"
        )
        self.tray._latest = self.entry
        clipboard = QApplication.clipboard()
        original = clipboard.text()
        self.addCleanup(clipboard.setText, original)

    def test_copy_only_ignores_focus_and_does_not_submit_post_key(self):
        with (
            patch("src.main.get_foreground_target", return_value=None),
            patch("src.main.type_text") as inject,
        ):
            self.tray._deliver(self.entry, "final", "copy", self.target, "enter")
        inject.assert_not_called()
        self.assertEqual(QApplication.clipboard().text(), "final")
        self.assertTrue(self.tray._latest.last_delivery.copied)
        self.assertEqual(self.tray._latest.last_delivery.post_key_state, "not_requested")

    def test_windows_clipboard_failure_without_python_exception_is_reported(self):
        clipboard = Mock()
        clipboard.ownsClipboard.return_value = False
        with (
            patch("src.main.platform.system", return_value="Windows"),
            patch("src.main.QApplication.platformName", return_value="windows"),
            patch("src.main.QApplication.clipboard", return_value=clipboard),
            patch("src.main.type_text") as inject,
        ):
            self.tray._deliver(self.entry, "final", "copy", self.target)
        clipboard.setText.assert_called_once_with("final")
        inject.assert_not_called()
        attempt = self.tray._latest.last_delivery
        self.assertFalse(attempt.copied)
        self.assertEqual(attempt.reason, "clipboard_failed")
        self.tray._on_error.assert_called_once_with(AppError.CLIPBOARD_FAILED)

    def test_windows_owned_clipboard_is_recorded_separately_from_typing(self):
        clipboard = Mock()
        clipboard.ownsClipboard.return_value = True
        with (
            patch("src.main.platform.system", return_value="Windows"),
            patch("src.main.QApplication.platformName", return_value="windows"),
            patch("src.main.QApplication.clipboard", return_value=clipboard),
            patch("src.main.get_foreground_target", return_value=self.target),
            patch("src.main.type_text", return_value=InjectionReport(10, 0, False)) as inject,
        ):
            self.tray._deliver(self.entry, "final", "copy_and_type", self.target)
        clipboard.setText.assert_called_once_with("final")
        inject.assert_called_once_with("final", None, expected_target=self.target)
        self.assertTrue(self.tray._latest.last_delivery.copied)
        self.assertEqual(self.tray._latest.last_delivery.text_state, "events_submitted")

    def test_copy_and_type_changed_target_records_copy_and_withhold_separately(self):
        with (
            patch("src.main.get_foreground_target", return_value=WindowIdentity(101, 200)),
            patch("src.main.type_text") as inject,
        ):
            self.tray._deliver(self.entry, "final", "copy_and_type", self.target, "enter")
        inject.assert_not_called()
        self.assertEqual(QApplication.clipboard().text(), "final")
        attempt = self.tray._latest.last_delivery
        self.assertTrue(attempt.copied)
        self.assertEqual(attempt.text_state, "withheld")

    def test_post_key_failure_does_not_reclassify_submitted_text_as_failed(self):
        with (
            patch("src.main.get_foreground_target", return_value=self.target),
            patch("src.main.type_text", side_effect=InjectionError("post failed", 10, 1)) as inject,
        ):
            self.tray._deliver(self.entry, "final", "type", self.target, "enter")
        self.assertEqual(inject.call_count, 1)
        attempt = self.tray._latest.last_delivery
        self.assertEqual(attempt.text_state, "events_submitted")
        self.assertEqual(attempt.post_key_state, "reported_failed")

    def test_manual_arm_is_consumed_once_and_never_targets_screamer(self):
        self.tray._arm_recovery(self.entry, "raw")
        with (
            patch("src.main.get_foreground_target", return_value=WindowIdentity(100, os.getpid())),
            patch("src.main.type_text") as inject,
        ):
            self.tray._insert_armed()
        inject.assert_not_called()
        self.assertIsNone(self.tray._armed)
        self.assertEqual(self.tray._latest.raw_text, "raw")
        self.tray._arm_recovery(self.tray._latest, "final")
        with (
            patch("src.main.get_foreground_target", return_value=self.target),
            patch("src.main.type_text", return_value=InjectionReport(10, 0, False)) as inject,
        ):
            self.tray._insert_armed()
            self.tray._insert_armed()
        inject.assert_called_once_with("final", None, expected_target=self.target)

    def test_failed_delete_or_clear_preserves_latest_and_armed_choice(self):
        self.tray._config.history_enabled = True
        self.tray._committed = [self.entry]
        self.tray._committed_ids = {self.entry.id}
        self.tray._arm_recovery(self.entry, "raw")
        self.tray._history.delete.side_effect = ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        self.tray._delete_record(self.entry)
        self.assertIs(self.tray._latest, self.entry)
        self.assertIsNotNone(self.tray._armed)
        self.tray._history.clear.side_effect = ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        with patch("src.main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.tray._clear_history()
        self.assertIs(self.tray._latest, self.entry)
        self.assertIsNotNone(self.tray._armed)
        self.assertEqual(self.tray._committed, [self.entry])

    def test_successful_delete_clears_matching_latest_and_armed_choice(self):
        self.tray._config.history_enabled = True
        self.tray._committed_ids = {self.entry.id}
        self.tray._history.delete.return_value = []
        self.tray._arm_recovery(self.entry, "raw")
        self.tray._delete_record(self.entry)
        self.assertIsNone(self.tray._latest)
        self.assertIsNone(self.tray._armed)

    def test_failed_history_write_for_older_copy_retains_actual_attempt_in_ram(self):
        older = DictationRecord("older", "2026-09-30T11:00:00+00:00", "older raw")
        self.tray._config.history_enabled = True
        self.tray._committed = [older]
        self.tray._committed_ids = {older.id}
        self.tray._history.upsert.side_effect = ScreamerError(AppError.HISTORY_STORAGE_FAILED)
        self.tray._copy_recovery(older, "raw")
        self.assertEqual(self.tray._latest.id, older.id)
        self.assertTrue(self.tray._latest.last_delivery.copied)
        self.assertEqual(self.tray._committed, [older])
        self.assertIsNone(self.tray._committed[0].last_delivery)

    def test_copying_candidate_updates_ram_attempt_without_implicit_history_save(self):
        candidate = DictationRecord(
            "candidate", "2026-09-30T13:00:00+00:00", "raw", "preview", "succeeded"
        )
        self.tray._candidate = candidate
        self.tray._config.history_enabled = True
        self.tray._copy_recovery(candidate, "final")
        self.assertEqual(QApplication.clipboard().text(), "preview")
        self.assertTrue(self.tray._candidate.last_delivery.copied)
        self.tray._history.upsert.assert_not_called()
        self.assertIs(self.tray._latest, self.entry)

    def test_delete_passes_retention_limit_to_committed_store(self):
        self.tray._config.history_enabled = True
        self.tray._config.history_limit = 1
        self.tray._committed_ids = {self.entry.id}
        self.tray._history.delete.return_value = []
        self.tray._delete_record(self.entry)
        self.tray._history.delete.assert_called_once_with(self.entry.id, 1)


class RecoveryDialogTests(unittest.TestCase):
    def test_user_text_is_literal_and_copy_and_arm_are_explicit_actions(self):
        tray = make_tray_app()
        tray._on_error = Mock()
        entry = DictationRecord(
            "one",
            "2026-09-30T12:00:00+00:00",
            "<b>literal raw</b>",
            "<a>literal final</a>",
            "succeeded",
            language="cs",
        )
        tray._latest = entry
        dialog = RecoveryDialog()
        self.addCleanup(dialog.deleteLater)
        tray._recovery_dlg = dialog
        dialog.copy_requested.connect(tray._copy_recovery)
        dialog.arm_requested.connect(tray._arm_recovery)
        clipboard = QApplication.clipboard()
        original = clipboard.text()
        self.addCleanup(clipboard.setText, original)
        clipboard.setText("unchanged until explicit Copy")
        tray._refresh_recovery()
        self.assertEqual(dialog._raw.toPlainText(), "<b>literal raw</b>")
        self.assertEqual(dialog._final.toPlainText(), "<a>literal final</a>")
        self.assertEqual(clipboard.text(), "unchanged until explicit Copy")
        dialog._buttons["Copy final"].click()
        self.assertEqual(clipboard.text(), "<a>literal final</a>")
        dialog._buttons["Arm raw"].click()
        self.assertEqual(tray._armed[1], "<b>literal raw</b>")
        self.assertFalse(dialog.isVisible())
        dialog._list.setCurrentRow(-1)
        self.assertFalse(dialog._buttons["Copy raw"].isEnabled())
        self.assertFalse(dialog._buttons["Delete"].isEnabled())


if __name__ == "__main__":
    unittest.main()
