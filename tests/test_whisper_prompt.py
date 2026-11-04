"""Whisper prompt shape, token budget, logging and the settings estimate/preset toggles."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
from PySide6.QtWidgets import QApplication

from src.config import AppConfig, VocabularyTerm, select_vocabulary
from src.settings_dialog import STT_PRESETS, SettingsDialog
from src.stt import (
    WHISPER_PROMPT_TOKEN_BUDGET,
    build_whisper_prompt,
    estimate_prompt_tokens,
    transcribe,
)

_app = QApplication.instance() or QApplication([])

T = VocabularyTerm


def _long_entries():
    """40 Czech terms followed by 40 All-languages terms, far over the budget."""
    return [T(f"slovíčko{i:02d}", "cs") for i in range(40)] + [
        T(f"GeneralTerm{i:02d}") for i in range(40)
    ]


class BuildWhisperPromptTests(unittest.TestCase):
    def test_bare_list_without_header_or_quotes(self):
        prompt, omitted = build_whisper_prompt([T("PySide6"), T("Qt"), T("httpx")])
        self.assertEqual(prompt, "PySide6, Qt, httpx.")
        self.assertEqual(omitted, 0)

    def test_empty_selection_gives_no_prompt(self):
        self.assertEqual(build_whisper_prompt([]), ("", 0))

    def test_term_with_comma_is_kept_as_is(self):
        self.assertEqual(build_whisper_prompt([T("Smith, Inc"), T("Qt")])[0], "Smith, Inc, Qt.")

    def test_estimate_counts_utf8_bytes_conservatively(self):
        self.assertEqual(estimate_prompt_tokens(""), 0)
        self.assertEqual(estimate_prompt_tokens("abc"), 2)
        self.assertEqual(estimate_prompt_tokens("čř"), 2)  # 4 UTF-8 bytes

    def test_long_list_is_cut_deterministically_language_terms_first(self):
        terms = select_vocabulary(_long_entries(), "cs")
        prompt, omitted = build_whisper_prompt(terms)
        self.assertGreater(omitted, 0)
        self.assertLessEqual(estimate_prompt_tokens(prompt), WHISPER_PROMPT_TOKEN_BUDGET)
        kept = prompt.rstrip(".").split(", ")
        self.assertEqual(kept, [item.term for item in terms[: len(terms) - omitted]])
        self.assertTrue(all(item.language == "cs" for item in terms[: len(kept)]))
        self.assertNotIn("GeneralTerm00", prompt)
        # The cut is a prefix: the next term would not have fit.
        bigger = ", ".join([*kept, terms[len(kept)].term]) + "."
        self.assertGreater(estimate_prompt_tokens(bigger), WHISPER_PROMPT_TOKEN_BUDGET)
        self.assertEqual(build_whisper_prompt(terms), (prompt, omitted))

    def test_first_term_that_does_not_fit_stops_the_list(self):
        huge = T("x" * 460)  # ~231 tokens on its own
        prompt, omitted = build_whisper_prompt([T("Qt"), huge, T("y")])
        self.assertEqual((prompt, omitted), ("Qt.", 2))


class TranscribePromptTests(unittest.TestCase):
    def _transcribe(self, entries, enabled=True):
        cfg = AppConfig(
            stt_base_url="https://stt.test/v1",
            stt_model="whisper-1",
            stt_language="cs",
            vocabulary_entries=entries,
            stt_prompt_primary_enabled=enabled,
        )
        with patch(
            "src.http_client.post", return_value=httpx.Response(200, json={"text": "ok"})
        ) as post:
            transcribe(b"wav", cfg)
        return post.call_args.kwargs["data"]

    def test_empty_selection_sends_no_prompt_field(self):
        self.assertNotIn("prompt", self._transcribe([T("hello", "en")]))

    def test_logs_counts_but_not_terms(self):
        with self.assertLogs("src.stt", level="INFO") as logs:
            data = self._transcribe(_long_entries())
        line = next(m for m in logs.output if "STT prompt:" in m)
        _prompt, omitted = build_whisper_prompt(select_vocabulary(_long_entries(), "cs"))
        self.assertIn(f"{80 - omitted} terms sent, {omitted} omitted (token budget)", line)
        self.assertNotIn("slovíčko", "\n".join(logs.output))
        self.assertTrue(data["prompt"].startswith("slovíčko00, "))


class SettingsPromptEstimateTests(unittest.TestCase):
    def _dialog(self, **overrides):
        dlg = SettingsDialog(AppConfig(**overrides), devices=[], calibrate_fn=None)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_estimate_for_current_language(self):
        dlg = self._dialog(stt_language="cs", vocabulary_entries=[T("PySide6"), T("ahoj", "cs")])
        expected = estimate_prompt_tokens("ahoj, PySide6.")
        self.assertIn(
            f"~{expected} / {WHISPER_PROMPT_TOKEN_BUDGET} tokens", dlg._stt_prompt_estimate.text()
        )
        self.assertTrue(dlg._stt_prompt_overflow.isHidden())

    def test_overflow_warning_and_refresh_on_language_change(self):
        dlg = self._dialog(stt_language="cs", vocabulary_entries=_long_entries())
        self.assertFalse(dlg._stt_prompt_overflow.isHidden())
        self.assertIn("won't fit into the Whisper prompt", dlg._stt_prompt_overflow.text())
        dlg._stt_lang.setCurrentIndex(dlg._stt_lang.findData(""))  # Auto: only 40 general terms
        self.assertIn("all languages", dlg._stt_prompt_estimate.text())

    def test_estimate_refreshes_when_vocabulary_changes(self):
        dlg = self._dialog(stt_language="cs")
        self.assertIn("no prompt is sent", dlg._stt_prompt_estimate.text())
        dlg._vocabulary_input.setText("ahoj")
        dlg._on_vocabulary_add()
        self.assertIn("tokens", dlg._stt_prompt_estimate.text())

    def test_estimate_refreshes_when_a_term_is_edited_in_place(self):
        dlg = self._dialog(stt_language="cs", vocabulary_entries=[T("ahoj", "cs")])
        before = dlg._stt_prompt_estimate.text()
        dlg._vocabulary_table.item(0, 0).setText("a much longer vocabulary term")
        self.assertNotEqual(dlg._stt_prompt_estimate.text(), before)
        self.assertIn(
            f"~{estimate_prompt_tokens('a much longer vocabulary term.')} /",
            dlg._stt_prompt_estimate.text(),
        )


class PresetEnablesPromptTests(unittest.TestCase):
    def _dialog(self):
        dlg = SettingsDialog(AppConfig(), devices=[], calibrate_fn=None)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def _choose(self, fields, label):
        index = fields.preset.findText(label)
        fields.preset.setCurrentIndex(index)
        fields.preset.activated.emit(index)

    def test_openai_and_groq_presets_enable_prompt_for_their_role(self):
        for label, _url, _model in STT_PRESETS[:2]:
            with self.subTest(label=label):
                dlg = self._dialog()
                self._choose(dlg._stt_provider, label)
                self.assertTrue(dlg._stt_prompt_primary_check.isChecked())
                self.assertFalse(dlg._stt_prompt_fallback_check.isChecked())
                self._choose(dlg._stt_fb_provider, label)
                self.assertTrue(dlg._stt_prompt_fallback_check.isChecked())

    def test_custom_and_local_presets_leave_prompt_unchanged(self):
        dlg = self._dialog()
        self._choose(dlg._stt_provider, "Custom")
        self._choose(dlg._stt_provider, "Local server (Speaches)")
        self.assertFalse(dlg._stt_prompt_primary_check.isChecked())
        dlg._stt_prompt_primary_check.setChecked(True)
        self._choose(dlg._stt_provider, "Custom")
        self.assertTrue(dlg._stt_prompt_primary_check.isChecked())


if __name__ == "__main__":
    unittest.main()
