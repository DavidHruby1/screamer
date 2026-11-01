"""Product-review features: replacements, damaged settings, legacy prompts, keyless LLM,
Shift-to-submit, practice dictation, dictation during Settings, latency, connection test."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from time import perf_counter
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from src import http_client
from src.config import (
    MAX_REWRITE_PROMPT_BYTES,
    POLISHED_LLM_SYSTEM_PROMPT,
    AppConfig,
    AppMapping,
    Hotkey,
    RewriteProfile,
    apply_replacements,
    decode_replacements,
    decode_rewrite_catalog,
    encode_replacements,
    encode_rewrite_catalog,
    load_config,
    normalize_replacements,
    resolve_rewrite_profile,
    rewrite_needs_llm,
    save_config,
    validate_config,
)
from src.hotkey import WM_KEYDOWN, WM_KEYUP, HotkeyListener, HotkeyMode
from src.injector import InjectionReport, WindowIdentity
from src.main import TrayState, _WorkerThread
from src.rewrite import rewrite
from src.settings_dialog import ConnectionTestThread, SettingsDialog
from src.setup_wizard import SetupWizard
from src.stt import latency_summary
from src.utils import AppError, PipelineResult, ScreamerError, SignalBridge
from tests.test_tray_menu import make_tray_app

_app = QApplication.instance() or QApplication([])

VK_LCTRL, VK_LALT, VK_LSHIFT, VK_SPACE = 0xA2, 0xA4, 0xA0, 0x20


class ReplacementTests(unittest.TestCase):
    def test_apply_is_case_insensitive_whole_word_and_phrase_aware(self):
        pairs = [("screamer", "Screamer"), ("q thread", "QThread")]
        self.assertEqual(
            apply_replacements("SCREAMER uses a Q Thread, screamer!", pairs),
            "Screamer uses a QThread, Screamer!",
        )

    def test_no_partial_word_matches(self):
        pairs = [("cat", "dog")]
        self.assertEqual(apply_replacements("concatenate cats cat", pairs), "concatenate cats dog")

    def test_longest_source_wins_in_a_single_pass(self):
        pairs = [("new", "old"), ("new york", "NYC"), ("old", "new")]
        # Single pass: replaced text is never replaced again.
        self.assertEqual(apply_replacements("new york is new and old", pairs), "NYC is old and new")

    def test_punctuation_bordered_source_matches(self):
        pairs = [("c++", "C++"), (".net", ".NET")]
        self.assertEqual(
            apply_replacements("I like c++ and .net (c++).", pairs), "I like C++ and .NET (C++)."
        )

    def test_empty_inputs_return_text_unchanged(self):
        self.assertEqual(apply_replacements("", [("a", "b")]), "")
        self.assertEqual(apply_replacements("keep", []), "keep")

    def test_normalize_trims_dedupes_and_skips_blank_sources(self):
        self.assertEqual(
            normalize_replacements(
                [(" Foo ", " Bar "), ["foo", "ignored"], ("  ", "x"), ("a", "")]
            ),
            [("Foo", "Bar"), ("a", "")],
        )

    def test_normalize_rejects_invalid_values(self):
        for value in (
            "not a list",
            [("only one",)],
            [("a", 1)],
            [("line\nbreak", "x")],
            [("a", "tab\tinside")],
            [("x" * 257, "y")],
            [(f"w{i}", "y") for i in range(257)],
        ):
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                normalize_replacements(value)

    def test_encode_decode_and_save_load_roundtrip(self):
        pairs = [("Straße", "Strasse"), ("q thread", "QThread")]
        self.assertEqual(decode_replacements(encode_replacements(pairs)), pairs)
        with self.assertRaises(ValueError):
            decode_replacements(None)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=False),
        ):
            save_config(AppConfig(replacements=pairs))
            self.assertEqual(load_config().replacements, pairs)


class DamagedSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", self.tmp))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))

    def test_damaged_values_are_backed_up_reset_and_listed(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        settings.setValue("replacements", '[["a"]]')
        settings.setValue("vocabulary_entries", "not json")
        settings.setValue("rewrite_catalog", "{}")
        settings.setValue("stt_language", "cs")
        settings.sync()
        cfg = load_config()
        self.assertEqual(
            sorted(cfg._damaged_settings), ["replacements", "rewrite_catalog", "vocabulary_entries"]
        )
        self.assertEqual(cfg.replacements, [])
        self.assertEqual(cfg.vocabulary_entries, [])
        self.assertEqual(decode_rewrite_catalog(cfg.rewrite_catalog), ([], []))
        self.assertEqual(cfg.stt_language, "cs")
        backup = Path(self.tmp, "settings-damaged.txt").read_text(encoding="utf-8")
        for line in ('replacements = [["a"]]', "vocabulary_entries = not json"):
            self.assertIn(line, backup)
        save_config(cfg)
        self.assertEqual(load_config()._damaged_settings, [])

    def test_tray_saves_on_startup_and_reports_damage(self):
        damaged = AppConfig()
        damaged._damaged_settings = ["replacements"]
        with (
            patch("src.main.load_config", return_value=damaged),
            patch("src.main.import_from_env", side_effect=lambda cfg: cfg),
            patch("src.main.has_plaintext_secrets", return_value=False),
            patch("src.main.save_config") as save,
            patch("src.main.validate_config", return_value=[]),
            patch("src.main.AudioRecorder"),
            patch("src.main._TrayApp._build_tray"),
            patch("src.main._TrayApp._build_hotkey"),
            patch("src.main._TrayApp._apply_state"),
            patch("src.main._TrayApp._on_error") as on_error,
        ):
            from src.main import _TrayApp

            _TrayApp(startup_mode=True)
        save.assert_called_once()
        on_error.assert_any_call(AppError.SETTINGS_DAMAGED)


class LegacyAndObsoleteKeyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch("src.config.APP_DIR", self.enterContext(tempfile.TemporaryDirectory()))
        )
        self.enterContext(patch("src.config._dpapi_available", return_value=False))

    def test_legacy_prompt_migrates_to_selected_legacy_profile(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        settings.setValue("llm_system_prompt", "My old prompt.")
        settings.setValue("llm_enabled", "true")
        settings.sync()
        cfg = load_config()
        profiles, _ = decode_rewrite_catalog(cfg.rewrite_catalog)
        self.assertEqual(
            profiles, [RewriteProfile("legacy", "Saved rewrite prompt", "custom", "My old prompt.")]
        )
        self.assertEqual(
            (cfg.rewrite_selection_mode, cfg.rewrite_manual_profile_id), ("manual", "legacy")
        )

    def test_unmigratable_legacy_prompt_is_backed_up_not_fatal(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        settings.setValue("llm_system_prompt", "x" * (MAX_REWRITE_PROMPT_BYTES + 1))
        settings.setValue("llm_enabled", "true")
        settings.sync()
        with self.assertLogs("src.config", "WARNING"):
            cfg = load_config()
        self.assertEqual(decode_rewrite_catalog(cfg.rewrite_catalog), ([], []))
        self.assertIn("llm_system_prompt", cfg._damaged_settings)
        self.assertEqual(cfg.rewrite_manual_profile_id, "clean")
        from src.config import APP_DIR

        backup = Path(APP_DIR) / "settings-damaged.txt"
        self.assertIn("llm_system_prompt = xxx", backup.read_text(encoding="utf-8"))

    def test_obsolete_keys_are_removed_on_save(self):
        from src.config import _OBSOLETE_KEYS, _get_qsettings

        settings = _get_qsettings()
        for key in _OBSOLETE_KEYS:
            settings.setValue(key, "old")
        settings.setValue("unrelated_future_key", "kept")
        settings.sync()
        save_config(load_config())
        saved = _get_qsettings()
        for key in _OBSOLETE_KEYS:
            self.assertFalse(saved.contains(key), key)
        self.assertEqual(saved.value("unrelated_future_key"), "kept")
        self.assertEqual(int(saved.value("rewrite_controls_version")), 2)

    def test_invalid_saved_output_mode_loads_as_type(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        settings.setValue("output_mode", "teleport")
        settings.sync()
        self.assertEqual(load_config().output_mode, "type")
        save_config(AppConfig(output_mode="paste"))
        self.assertEqual(load_config().output_mode, "paste")


class RewriteProfileTests(unittest.TestCase):
    def test_polished_profile_resolves_with_its_prompt(self):
        cfg = AppConfig(rewrite_manual_profile_id="polished")
        profile = resolve_rewrite_profile(cfg, None)
        self.assertEqual(
            (profile.id, profile.kind, profile.name), ("polished", "polished", "Polished")
        )
        self.assertEqual(profile.prompt, POLISHED_LLM_SYSTEM_PROMPT)
        tray = make_tray_app()
        session = AppConfig(rewrite_manual_profile_id="polished")
        self.assertEqual(tray._apply_rewrite_profile(session, None), "polished")
        self.assertTrue(session.llm_enabled)
        self.assertEqual(session.llm_system_prompt, POLISHED_LLM_SYSTEM_PROMPT)
        raw = AppConfig(rewrite_manual_profile_id="raw")
        tray._apply_rewrite_profile(raw, None)
        self.assertFalse(raw.llm_enabled)
        self.assertEqual(raw.llm_system_prompt, "")

    def test_rewrite_needs_llm(self):
        self.assertFalse(rewrite_needs_llm(AppConfig()))
        self.assertTrue(rewrite_needs_llm(AppConfig(rewrite_manual_profile_id="clean")))
        self.assertTrue(rewrite_needs_llm(AppConfig(rewrite_manual_profile_id="polished")))
        auto_raw = AppConfig(rewrite_selection_mode="automatic", rewrite_default_profile_id="raw")
        self.assertFalse(rewrite_needs_llm(auto_raw))
        auto_raw.rewrite_catalog = encode_rewrite_catalog([], [AppMapping("editor.exe", "clean")])
        self.assertTrue(rewrite_needs_llm(auto_raw))
        self.assertFalse(rewrite_needs_llm(AppConfig(rewrite_catalog="damaged")))

    def test_keyless_llm_request_has_no_authorization_header(self):
        cfg = AppConfig(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:1234/v1",
            llm_model="local",
            llm_system_prompt="Clean.",
        )
        with patch(
            "src.http_client.post",
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
        ) as post:
            result = rewrite("raw", cfg)
        self.assertEqual(result.text, "ok")
        headers = post.call_args.kwargs["headers"]
        self.assertNotIn("authorization", headers)
        cfg.llm_api_key = "secret"
        with patch(
            "src.http_client.post",
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
        ) as post:
            rewrite("raw", cfg)
        self.assertEqual(post.call_args.kwargs["headers"]["authorization"], "Bearer secret")


def _listener(hotkey, mode):
    bridge = SignalBridge()
    events = []
    bridge.hotkey_pressed.connect(lambda: events.append("pressed"))
    bridge.hotkey_released.connect(lambda: events.append("released"))
    bridge.submit_requested.connect(lambda: events.append("submit"))
    return HotkeyListener(hotkey, mode, bridge), events


class ShiftSubmitListenerTests(unittest.TestCase):
    hotkey = Hotkey(frozenset({"ctrl", "alt"}), "key", VK_SPACE)

    def test_hold_release_with_extra_shift_requests_submit(self):
        listener, events = _listener(self.hotkey, HotkeyMode.HOLD)
        for vk in (VK_LCTRL, VK_LALT):
            listener._on_kb_event(WM_KEYDOWN, vk)
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, VK_SPACE))
        listener._on_kb_event(WM_KEYDOWN, VK_LSHIFT)
        self.assertTrue(listener._on_kb_event(WM_KEYUP, VK_SPACE))
        self.assertEqual(events, ["pressed", "submit", "released"])

    def test_hold_release_without_shift_does_not_submit(self):
        listener, events = _listener(self.hotkey, HotkeyMode.HOLD)
        for vk in (VK_LCTRL, VK_LALT, VK_SPACE):
            listener._on_kb_event(WM_KEYDOWN, vk)
        listener._on_kb_event(WM_KEYUP, VK_SPACE)
        self.assertEqual(events, ["pressed", "released"])

    def test_toggle_stop_with_extra_shift_matches_and_requests_submit(self):
        listener, events = _listener(self.hotkey, HotkeyMode.TOGGLE)
        for vk in (VK_LCTRL, VK_LALT, VK_SPACE):
            listener._on_kb_event(WM_KEYDOWN, vk)
        listener._on_kb_event(WM_KEYUP, VK_SPACE)
        listener.set_recording(True)
        listener._on_kb_event(WM_KEYDOWN, VK_LSHIFT)
        self.assertTrue(listener._on_kb_event(WM_KEYDOWN, VK_SPACE))
        self.assertEqual(events, ["pressed", "submit", "pressed"])

    def test_toggle_start_with_extra_shift_does_not_match(self):
        listener, events = _listener(self.hotkey, HotkeyMode.TOGGLE)
        for vk in (VK_LCTRL, VK_LALT, VK_LSHIFT):
            listener._on_kb_event(WM_KEYDOWN, vk)
        self.assertFalse(listener._on_kb_event(WM_KEYDOWN, VK_SPACE))
        self.assertEqual(events, [])

    def test_shift_that_is_part_of_the_chord_never_submits(self):
        hotkey = Hotkey(frozenset({"ctrl", "shift"}), "key", VK_SPACE)
        listener, events = _listener(hotkey, HotkeyMode.HOLD)
        for vk in (VK_LCTRL, VK_LSHIFT, VK_SPACE):
            listener._on_kb_event(WM_KEYDOWN, vk)
        listener._on_kb_event(WM_KEYUP, VK_SPACE)
        self.assertEqual(events, ["pressed", "released"])

        listener, events = _listener(hotkey, HotkeyMode.TOGGLE)
        for vk in (VK_LCTRL, VK_LSHIFT, VK_SPACE):
            listener._on_kb_event(WM_KEYDOWN, vk)
        listener._on_kb_event(WM_KEYUP, VK_SPACE)
        listener.set_recording(True)
        listener._on_kb_event(WM_KEYDOWN, VK_SPACE)
        self.assertEqual(events, ["pressed", "pressed"])


class WorkerReplacementTests(unittest.TestCase):
    def _run(self, cfg):
        worker = _WorkerThread(b"wav", cfg, threading.Event(), session_id="s")
        raw_ready, terminals = [], []
        worker.raw_ready.connect(lambda _id, raw, _w: raw_ready.append(raw))
        worker.terminal.connect(terminals.append)
        worker.run()
        return raw_ready, terminals[0]

    def test_replacements_apply_to_raw_and_rewritten_text(self):
        cfg = AppConfig(
            llm_enabled=True,
            llm_base_url="http://localhost/v1",
            llm_model="m",
            replacements=[("screamer", "Screamer"), ("q thread", "QThread")],
        )
        with (
            patch("src.main.transcribe", return_value=PipelineResult("screamer uses q thread")),
            patch(
                "src.main.rewrite", return_value=PipelineResult("Screamer uses a q thread.")
            ) as rw,
        ):
            raw_ready, result = self._run(cfg)
        rw.assert_called_once_with("Screamer uses QThread", cfg)
        self.assertEqual(raw_ready, ["Screamer uses QThread"])
        self.assertEqual(result.raw, "Screamer uses QThread")
        self.assertEqual(result.rewritten, "Screamer uses a QThread.")
        self.assertEqual(result.rewrite_status, "succeeded")
        self.assertGreaterEqual(result.stt_seconds, 0.0)
        self.assertGreaterEqual(result.rewrite_seconds, 0.0)

    def test_replacements_apply_without_rewrite(self):
        cfg = AppConfig(llm_enabled=False, replacements=[("screamer", "Screamer")])
        with (
            patch("src.main.transcribe", return_value=PipelineResult("hello screamer")),
            patch("src.main.rewrite") as rw,
        ):
            _raw_ready, result = self._run(cfg)
        rw.assert_not_called()
        self.assertEqual((result.raw, result.rewritten), ("hello Screamer", None))


class _TrayTestBase(unittest.TestCase):
    def setUp(self):
        self.tray = make_tray_app()
        self.tray._apply_state = Mock()
        self.tray._on_error = Mock()
        self.tray._play_sound = Mock()
        self.recorder = Mock()
        self.recorder.stop.return_value = b"wav" + b"\0" * 64
        self.enterContext(patch("src.main.resolve_device", return_value=None))
        self.enterContext(patch("src.main.AudioRecorder", return_value=self.recorder))
        self.enterContext(patch.object(_WorkerThread, "start"))

    def run_worker(self, text="hello", target=None):
        with (
            patch("src.main.transcribe", return_value=PipelineResult(text)),
            patch("src.main.rewrite"),
            patch("src.main.get_foreground_target", return_value=target),
            patch("src.main.type_text", return_value=InjectionReport(5, 0, False)) as inject,
        ):
            self.tray._worker.run()
            self.tray._on_worker_finished()
        return inject


class PracticeSessionTests(_TrayTestBase):
    def test_dictation_into_own_guide_goes_to_practice_field(self):
        for target in (None, WindowIdentity(1, os.getpid())):
            with self.subTest(target=target):
                self.tray._setup_dlg = Mock()
                with patch("src.main.get_foreground_target", return_value=target):
                    self.tray._start_recording()
                self.assertTrue(self.tray._session.practice)
                self.tray._setup_dlg.stop_preview.assert_called_once()
                self.tray._finalize_recording()
                inject = self.run_worker("practice text", target)
                inject.assert_not_called()
                self.tray._setup_dlg.show_practice_result.assert_called_once_with("practice text")

    def test_other_screamer_window_focused_is_not_practice(self):
        # History or Settings in front, guide in the background: deliver normally.
        self.tray._setup_dlg = Mock()
        self.tray._setup_dlg.isActiveWindow.return_value = False
        target = WindowIdentity(1, os.getpid())
        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._start_recording()
        self.assertFalse(self.tray._session.practice)
        self.tray._finalize_recording()
        self.run_worker("history text", target)
        self.tray._setup_dlg.show_practice_result.assert_not_called()

    def test_guide_level_meter_restarts_whenever_app_is_idle_again(self):
        tray = make_tray_app()
        tray._tray = Mock()
        tray._snackbar = Mock()
        tray._setup_dlg = Mock()
        for state in (TrayState.RECORDING, TrayState.PROCESSING):
            tray._apply_state(state)
        tray._setup_dlg.ensure_preview.assert_not_called()
        tray._apply_state(TrayState.IDLE)
        tray._setup_dlg.ensure_preview.assert_called_once()

    def test_guide_open_but_other_app_focused_types_normally(self):
        target = WindowIdentity(1, 4242)
        self.tray._setup_dlg = Mock()
        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._start_recording()
        self.assertFalse(self.tray._session.practice)
        self.tray._finalize_recording()
        inject = self.run_worker("real text", target)
        inject.assert_called_once()
        self.tray._setup_dlg.show_practice_result.assert_not_called()


class PasteDeliveryTests(_TrayTestBase):
    def test_clipboard_failure_is_reported_as_clipboard_not_injection(self):
        target = WindowIdentity(7, 4242)
        self.tray._config = AppConfig(output_mode="paste")
        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._start_recording()
        self.tray._finalize_recording()
        with patch.object(
            type(self.tray),
            "_stage_paste",
            side_effect=ScreamerError(AppError.CLIPBOARD_FAILED),
        ):
            inject = self.run_worker("pasted", target)
        inject.assert_not_called()
        self.tray._on_error.assert_called_once_with(AppError.CLIPBOARD_FAILED)
        attempt = self.tray._latest.last_delivery
        self.assertEqual((attempt.reason, attempt.text_state), ("clipboard_failed", "withheld"))


class ConnectionTestLifecycleTests(unittest.TestCase):
    """A pending connection test never blocks closing a window or exiting for 10 s."""

    def _blocking_check(self):
        release = threading.Event()

        def check(*_args, **_kwargs):
            release.wait(5)
            return "Connected."

        self.addCleanup(release.set)
        return patch("src.http_client.check_connection", side_effect=check)

    def test_abort_returns_promptly_and_drops_the_result(self):
        from src.config import ProviderConfig

        results = []
        owner = QObject()  # Kept alive for the test; the thread is its child.
        thread = ConnectionTestThread(ProviderConfig("", "https://x.test/v1", "m", ""), owner)
        thread.done.connect(results.append)
        with self._blocking_check():
            thread.start()
            started = perf_counter()
            thread.abort()
        self.assertLess(perf_counter() - started, 2.0)
        self.assertFalse(thread.isRunning())
        QApplication.processEvents()
        self.assertEqual(results, [])

    def test_setup_guide_done_joins_its_connection_test(self):
        cfg = AppConfig(stt_base_url="https://x.test/v1", stt_model="m")
        with patch("src.setup_wizard.AudioRecorder"):
            wizard = SetupWizard(cfg)
        self.addCleanup(wizard.deleteLater)
        with self._blocking_check():
            wizard._provider.test()
            thread = wizard._provider._thread
            self.assertIsNotNone(thread)
            started = perf_counter()
            wizard.done(0)
        self.assertLess(perf_counter() - started, 2.0)
        self.assertFalse(thread.isRunning())
        self.assertIsNone(wizard._provider._thread)
        self.assertTrue(wizard._provider.test_btn.isEnabled())

    def test_settings_done_joins_connection_tests(self):
        cfg = AppConfig(llm_base_url="https://x.test/v1", llm_model="m")
        dialog = SettingsDialog(cfg, devices=[])
        self.addCleanup(dialog.deleteLater)
        with self._blocking_check():
            dialog._llm_provider.test()
            thread = dialog._llm_provider._thread
            started = perf_counter()
            dialog.done(0)
        self.assertLess(perf_counter() - started, 2.0)
        self.assertFalse(thread.isRunning())
        self.assertIsNone(dialog._llm_provider._thread)


class AutoModeStaleManualProfileTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch("src.config.APP_DIR", self.enterContext(tempfile.TemporaryDirectory()))
        )
        self.enterContext(patch("src.config._dpapi_available", return_value=False))

    def test_deleting_hidden_manual_profile_in_auto_mode_still_validates(self):
        custom = RewriteProfile("c1", "Coding", "custom", "Keep identifiers.")
        cfg = AppConfig(
            stt_base_url="https://x.test/v1",
            stt_model="m",
            llm_base_url="https://x.test/v1",
            llm_model="m",
            rewrite_catalog=encode_rewrite_catalog([custom], []),
            rewrite_selection_mode="manual",
            rewrite_manual_profile_id="c1",
        )
        dialog = SettingsDialog(cfg, devices=[])
        self.addCleanup(dialog.deleteLater)
        active = dialog._rewrite_active_combo
        active.setCurrentIndex(active.findData("__automatic__"))
        profiles = dialog._rewrite_profile_combo
        profiles.setCurrentIndex(profiles.findData("c1"))
        dialog._rewrite_delete_btn.click()
        dialog._collect()
        self.assertEqual(validate_config(dialog._working), [])
        self.assertEqual(dialog._working.rewrite_selection_mode, "automatic")
        self.assertEqual(dialog._working.rewrite_manual_profile_id, "clean")


class DictationDuringSettingsTests(_TrayTestBase):
    def test_dictation_runs_and_types_while_settings_is_open(self):
        self.tray._settings_dlg = Mock()
        self.tray._settings_dlg.is_hotkey_capture_active.return_value = False
        self.tray._config = AppConfig(post_type_key="tab")
        target = WindowIdentity(7, 4242)
        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._on_hotkey_pressed()
        self.assertTrue(self.tray._recording)
        self.tray._on_hotkey_released()
        self.assertFalse(self.tray._recording)
        inject = self.run_worker("typed", target)
        inject.assert_called_once_with("typed", "tab", expected_target=target, paste=False)
        self.assertTrue(self.tray._recovery_allowed())

    def test_hotkey_capture_in_settings_still_blocks_new_dictation(self):
        self.tray._settings_dlg = Mock()
        self.tray._settings_dlg.is_hotkey_capture_active.return_value = True
        with patch("src.main.get_foreground_target", return_value=None):
            self.tray._on_hotkey_pressed()
        self.assertFalse(self.tray._recording)

    def test_shift_submit_sends_enter_for_that_session_only(self):
        target = WindowIdentity(7, 4242)
        self.tray._config = AppConfig(post_type_key="none")
        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._start_recording()
        self.tray._on_submit_requested()
        self.tray._finalize_recording()
        self.assertTrue(self.tray._session.submit)
        inject = self.run_worker("send this", target)
        inject.assert_called_once_with("send this", "enter", expected_target=target, paste=False)

        with patch("src.main.get_foreground_target", return_value=target):
            self.tray._start_recording()
        self.tray._finalize_recording()
        self.assertFalse(self.tray._session.submit)
        inject = self.run_worker("plain", target)
        inject.assert_called_once_with("plain", None, expected_target=target, paste=False)

    def test_submit_request_outside_recording_is_ignored(self):
        self.tray._on_submit_requested()
        self.assertFalse(self.tray._submit_requested)


class TrayMenuShapeTests(unittest.TestCase):
    def test_menu_has_only_everyday_controls(self):
        tray = make_tray_app()
        tray._config = AppConfig(
            rewrite_catalog=encode_rewrite_catalog(
                [RewriteProfile("c1", "Coding", "custom", "p")], []
            )
        )
        tray._rebuild_menu()
        labels = []
        for action in tray._menu.actions():
            if action.isSeparator():
                continue
            widget = action.defaultWidget() if hasattr(action, "defaultWidget") else None
            labels.append(widget.text() if widget is not None else action.text())
        self.assertEqual(
            labels,
            [
                "Enabled",
                "Language",
                "Profile",
                "Copy last",
                "History...",
                "Settings...",
                "Setup guide...",
                "Exit",
            ],
        )
        profile_menu = next(a.menu() for a in tray._menu.actions() if a.text() == "Profile")
        self.assertEqual(
            [a.defaultWidget().text() for a in profile_menu.actions()],
            ["Auto (by app)", "Raw transcription", "Clean", "Polished", "Coding"],
        )
        for removed in ("_set_recording_mode", "_set_hotkey", "_set_post_key"):
            self.assertFalse(hasattr(tray, removed))


class LatencySummaryTests(unittest.TestCase):
    def test_median_and_nearest_rank_p95(self):
        self.assertEqual(latency_summary([]), {"count": 0, "median": 0.0, "p95": 0.0})
        self.assertEqual(latency_summary([2.0]), {"count": 1, "median": 2.0, "p95": 2.0})
        self.assertEqual(latency_summary([3.0, 1.0]), {"count": 2, "median": 2.0, "p95": 3.0})
        summary = latency_summary([float(i) for i in range(1, 21)])
        self.assertEqual(summary["median"], 10.5)
        self.assertEqual(summary["p95"], 19.0)
        self.assertEqual(latency_summary([float(i) for i in range(1, 101)])["p95"], 95.0)


class CheckConnectionTests(unittest.TestCase):
    def _check(self, response, model="whisper-1"):
        client = Mock()
        client.get.return_value = response
        with patch("src.http_client._get_client", return_value=client):
            result = http_client.check_connection(
                "https://api.example.test/v1/", httpx.Headers({"Authorization": "Bearer k"}), model
            )
        self.assertEqual(client.get.call_args.args[0], "https://api.example.test/v1/models")
        return result

    def test_ok_with_model_listed(self):
        response = httpx.Response(200, json={"data": [{"id": "whisper-1"}, {"id": "other"}]})
        self.assertEqual(self._check(response), "Connected.")

    def test_ok_but_model_missing_is_reported_not_raised(self):
        response = httpx.Response(200, json={"data": [{"id": "other"}]})
        self.assertIn("does not list model 'whisper-1'", self._check(response))

    def test_unauthorized_raises_without_url(self):
        with self.assertRaises(RuntimeError) as error:
            self._check(httpx.Response(401, json={}))
        self.assertIn("rejected the API key", str(error.exception))
        self.assertNotIn("example.test", str(error.exception))

    def test_unreachable_raises_without_url(self):
        client = Mock()
        client.get.side_effect = httpx.ConnectError("https://api.example.test failed")
        with (
            patch("src.http_client._get_client", return_value=client),
            self.assertRaises(RuntimeError) as error,
        ):
            http_client.check_connection("https://api.example.test/v1", httpx.Headers(), "m")
        self.assertNotIn("example.test", str(error.exception))


if __name__ == "__main__":
    unittest.main()
