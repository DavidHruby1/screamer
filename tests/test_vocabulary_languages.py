"""Language-tagged vocabulary: model, selection, persistence, requests and settings UI."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
from PySide6.QtWidgets import QApplication

from src.config import (
    AppConfig,
    VocabularyTerm,
    decode_vocabulary_entries,
    encode_vocabulary_entries,
    load_config,
    normalize_vocabulary_entries,
    save_config,
    select_vocabulary,
    validate_config,
)
from src.rewrite import rewrite
from src.settings_dialog import SettingsDialog
from src.stt import transcribe

_app = QApplication.instance() or QApplication([])

T = VocabularyTerm

ENTRIES = [
    T("Screamer"),
    T("ahoj", "cs"),
    T("QThread"),
    T("hello", "en"),
    T("obrigado", "pt-br"),
    T("Kotlin", "cs"),
]


class SelectVocabularyTests(unittest.TestCase):
    def test_auto_selects_only_all_language_terms(self):
        self.assertEqual(select_vocabulary(ENTRIES, ""), [T("Screamer"), T("QThread")])

    def test_language_terms_come_first_then_all_languages_in_list_order(self):
        self.assertEqual(
            select_vocabulary(ENTRIES, "cs"),
            [T("ahoj", "cs"), T("Kotlin", "cs"), T("Screamer"), T("QThread")],
        )

    def test_primary_subtag_matches_both_ways(self):
        self.assertEqual(
            select_vocabulary(ENTRIES, "pt"), [T("obrigado", "pt-br"), T("Screamer"), T("QThread")]
        )
        pt_entries = [T("Lisboa", "pt"), T("Screamer")]
        self.assertEqual(select_vocabulary(pt_entries, "pt-br"), [T("Lisboa", "pt"), T("Screamer")])

    def test_other_language_gets_no_foreign_terms(self):
        self.assertEqual(select_vocabulary(ENTRIES, "de"), [T("Screamer"), T("QThread")])

    def test_empty_vocabulary(self):
        self.assertEqual(select_vocabulary([], "cs"), [])


class NormalizeVocabularyTests(unittest.TestCase):
    def test_accepts_terms_dicts_and_legacy_strings(self):
        self.assertEqual(
            normalize_vocabulary_entries(
                [" PySide6 ", {"term": "ahoj", "language": " CS "}, T("hello", "en")]
            ),
            [T("PySide6"), T("ahoj", "cs"), T("hello", "en")],
        )

    def test_dedup_across_languages_first_occurrence_wins(self):
        self.assertEqual(
            normalize_vocabulary_entries([T("Qt", "cs"), T("qt"), T("QT", "en")]), [T("Qt", "cs")]
        )

    def test_invalid_language_code_is_rejected(self):
        for language in ("c", "cs_CZ", "123", "x" * 40):
            with self.subTest(language=language), self.assertRaises(ValueError):
                normalize_vocabulary_entries([T("term", language)])

    def test_malformed_entries_are_rejected(self):
        for entry in (42, {"language": "cs"}, {"term": "a", "extra": 1}, {"term": 1}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                normalize_vocabulary_entries([entry])

    def test_limits_apply_to_whole_list(self):
        with self.assertRaises(ValueError):
            normalize_vocabulary_entries([T(f"t{i}", "cs" if i % 2 else "") for i in range(129)])
        with self.assertRaises(ValueError):
            normalize_vocabulary_entries([T("x" * 129, "en")])
        with self.assertRaises(ValueError):
            normalize_vocabulary_entries([T(f"{i:03}" + "x" * 120, "cs") for i in range(70)])

    def test_validate_config_reports_bad_language(self):
        cfg = AppConfig(
            stt_base_url="http://127.0.0.1:8000/v1",
            stt_model="local",
            vocabulary_entries=[T("term", "not a code")],
        )
        self.assertTrue(any("vocabulary" in i.message.lower() for i in validate_config(cfg)))


class VocabularyPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", self.tmp))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))

    def test_encode_writes_objects_and_decode_roundtrips(self):
        encoded = encode_vocabulary_entries([T("PySide6"), T("ahoj", "cs")])
        self.assertEqual(
            json.loads(encoded),
            [{"term": "PySide6", "language": ""}, {"term": "ahoj", "language": "cs"}],
        )
        self.assertEqual(decode_vocabulary_entries(encoded), [T("PySide6"), T("ahoj", "cs")])

    def test_legacy_string_list_migrates_on_next_save(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        settings.setValue("vocabulary_entries", '["PySide6", "Qt"]')
        settings.sync()
        cfg = load_config()
        self.assertEqual(cfg.vocabulary_entries, [T("PySide6"), T("Qt")])
        self.assertEqual(cfg._damaged_settings, [])
        save_config(cfg)
        self.assertEqual(
            json.loads(_get_qsettings().value("vocabulary_entries")),
            [{"term": "PySide6", "language": ""}, {"term": "Qt", "language": ""}],
        )

    def test_damaged_tagged_vocabulary_is_backed_up_and_reset(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        damaged = '[{"term": "ahoj", "language": "??"}]'
        settings.setValue("vocabulary_entries", damaged)
        settings.sync()
        cfg = load_config()
        self.assertEqual(cfg.vocabulary_entries, [])
        self.assertEqual(cfg._damaged_settings, ["vocabulary_entries"])
        backup = Path(settings.fileName()).parent / "settings-damaged.txt"
        self.assertIn(f"vocabulary_entries = {damaged}", backup.read_text(encoding="utf-8"))


class VocabularyRequestSelectionTests(unittest.TestCase):
    def _cfg(self, language):
        return AppConfig(
            stt_api_key="key",
            stt_base_url="https://stt.test/v1",
            stt_model="model",
            stt_language=language,
            stt_prompt_primary_enabled=True,
            llm_enabled=True,
            llm_api_key="key",
            llm_base_url="https://llm.test",
            llm_model="model",
            vocabulary_entries=list(ENTRIES),
        )

    def _stt_prompt(self, language):
        with patch(
            "src.http_client.post", return_value=httpx.Response(200, json={"text": "raw"})
        ) as post:
            transcribe(b"wav", self._cfg(language))
        return post.call_args.kwargs["data"].get("prompt", "")

    def _rewrite_prompt(self, language):
        with patch(
            "src.http_client.post",
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]}),
        ) as post:
            rewrite("raw", self._cfg(language))
        return post.call_args.kwargs["json"]["messages"][0]["content"]

    def test_stt_prompt_uses_selected_language_terms(self):
        self.assertEqual(self._stt_prompt("cs"), "ahoj, Kotlin, Screamer, QThread.")
        self.assertEqual(self._stt_prompt(""), "Screamer, QThread.")

    def test_rewrite_uses_same_selection(self):
        prompt = self._rewrite_prompt("en")
        self.assertIn('- "hello"\n- "Screamer"\n- "QThread"', prompt)
        self.assertNotIn("ahoj", prompt)
        self.assertNotIn("obrigado", prompt)


class VocabularySettingsTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", directory))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))
        self.enterContext(patch("src.settings_dialog.is_supported", return_value=False))
        self.cfg = AppConfig(
            stt_base_url="http://127.0.0.1:8000/v1",
            stt_model="local",
            stt_language_favorites=["xx-test"],
            vocabulary_entries=[T("Screamer"), T("legacy", "tlh")],
        )
        save_config(self.cfg)
        self.dialog = SettingsDialog(self.cfg, devices=[])
        self.addCleanup(self.dialog.deleteLater)

    def _combo_codes(self, combo):
        return [combo.itemData(i) for i in range(combo.count())]

    def test_language_combo_offers_all_builtins_common_favorites_and_saved_code(self):
        codes = self._combo_codes(self.dialog._vocabulary_table.cellWidget(1, 1))
        self.assertEqual(codes[:3], ["", "cs", "en"])
        self.assertIn("de", codes)
        self.assertIn("xx-test", codes)
        self.assertEqual(codes[-1], "tlh")
        self.assertEqual(self.dialog._vocabulary_table.cellWidget(1, 1).currentData(), "tlh")
        self.assertEqual(self.dialog._vocabulary_language.itemText(0), "All languages")

    def test_add_term_with_language_change_cell_and_reload(self):
        self.dialog._vocabulary_input.setText("ahoj")
        combo = self.dialog._vocabulary_language
        combo.setCurrentIndex(combo.findData("cs"))
        self.dialog._vocabulary_add_btn.click()
        self.assertEqual(self.dialog._vocabulary_table.rowCount(), 3)
        self.assertEqual(self.dialog._vocabulary_input.text(), "")

        cell = self.dialog._vocabulary_table.cellWidget(0, 1)
        cell.setCurrentIndex(cell.findData("en"))
        self.dialog._vocabulary_table.item(1, 0).setText("Klingon")

        self.dialog._on_apply()
        expected = [T("Screamer", "en"), T("Klingon", "tlh"), T("ahoj", "cs")]
        self.assertEqual(load_config().vocabulary_entries, expected)
        self.assertEqual(self.cfg.vocabulary_entries, [T("Screamer"), T("legacy", "tlh")])

        reopened = SettingsDialog(load_config(), devices=[])
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(reopened._vocabulary_values(), expected)

    def test_update_selected_changes_term_and_language(self):
        self.dialog._vocabulary_table.selectRow(0)
        self.assertEqual(self.dialog._vocabulary_input.text(), "Screamer")
        self.dialog._vocabulary_input.setText("Screamer app")
        combo = self.dialog._vocabulary_language
        combo.setCurrentIndex(combo.findData("de"))
        self.dialog._vocabulary_edit_btn.click()
        self.assertEqual(
            self.dialog._vocabulary_values(), [T("Screamer app", "de"), T("legacy", "tlh")]
        )

    def test_duplicate_across_languages_is_merged(self):
        self.dialog._vocabulary_input.setText("screamer")
        combo = self.dialog._vocabulary_language
        combo.setCurrentIndex(combo.findData("cs"))
        self.dialog._vocabulary_add_btn.click()
        self.assertEqual(self.dialog._vocabulary_values(), [T("Screamer"), T("legacy", "tlh")])

    def test_invalid_term_shows_warning(self):
        self.dialog._vocabulary_input.setText("x" * 129)
        with patch("src.settings_dialog.QMessageBox.warning") as warning:
            self.dialog._vocabulary_add_btn.click()
        warning.assert_called_once()
        self.assertEqual(self.dialog._vocabulary_table.rowCount(), 2)


if __name__ == "__main__":
    unittest.main()
