import json
import os
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import AppConfig, ProviderConfig, _env_path, import_from_env, parse_custom_headers, validate_config


class ConfigValidationTests(unittest.TestCase):
    def test_complete_stt_config_is_valid(self) -> None:
        cfg = AppConfig(stt_api_key="key", stt_base_url="https://example.test/v1", stt_model="stt")

        self.assertEqual(validate_config(cfg), [])

    def test_partial_stt_config_is_invalid(self) -> None:
        cfg = AppConfig(stt_api_key="key")

        messages = [issue.message for issue in validate_config(cfg)]

        self.assertIn("Primary STT requires an API key, base URL, and model.", messages)
        self.assertIn("Configure a complete primary or fallback STT provider.", messages)

    def test_fallback_stt_can_satisfy_required_config(self) -> None:
        cfg = AppConfig(
            stt_fallback_enabled=True,
            stt_fallback_api_key="key",
            stt_fallback_base_url="https://example.test/v1",
            stt_fallback_model="stt-fallback",
        )

        self.assertEqual(validate_config(cfg), [])

    def test_enabled_llm_requires_complete_provider(self) -> None:
        cfg = AppConfig(
            stt_api_key="key",
            stt_base_url="https://example.test/v1",
            stt_model="stt",
            llm_enabled=True,
            llm_api_key="llm-key",
        )

        messages = [issue.message for issue in validate_config(cfg)]

        self.assertIn("Primary LLM requires an API key, base URL, and model.", messages)
        self.assertIn("AI rewrite requires a complete primary or fallback LLM provider.", messages)

    def test_parse_custom_headers_requires_json_object(self) -> None:
        self.assertEqual(
            parse_custom_headers('{"X-Test": "yes", "X-Number": 1}'),
            {"X-Test": "yes", "X-Number": "1"},
        )

        with self.assertRaises(ValueError):
            parse_custom_headers(json.dumps(["not", "an", "object"]))

    def test_provider_config_detects_groq_by_host(self) -> None:
        self.assertTrue(ProviderConfig(base_url="https://api.groq.com/openai/v1").is_groq)
        self.assertFalse(ProviderConfig(base_url="https://api.openai.com/v1").is_groq)

    def test_import_from_env_backfills_empty_fields_only(self) -> None:
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                Path(".env").write_text(
                    "STT_API_KEY=from-env\nSTT_BASE_URL=https://env.test/v1\nSTT_MODEL=env-model\n",
                    encoding="utf-8",
                )
                cfg = AppConfig(stt_api_key="existing")

                imported = import_from_env(cfg)
            finally:
                os.chdir(cwd)

        self.assertEqual(imported.stt_api_key, "existing")
        self.assertEqual(imported.stt_base_url, "https://env.test/v1")
        self.assertEqual(imported.stt_model, "env-model")


class SecretHeaderTests(unittest.TestCase):
    def test_custom_headers_are_secret_fields(self) -> None:
        from src.config import _SECRET_FIELDS

        self.assertTrue(
            {
                "stt_custom_headers",
                "stt_fallback_custom_headers",
                "llm_custom_headers",
                "llm_fallback_custom_headers",
            }
            <= _SECRET_FIELDS
        )

    @unittest.skipUnless(platform.system() == "Windows", "DPAPI requires Windows")
    def test_save_config_keeps_headers_out_of_ini_and_roundtrips(self) -> None:
        from src.config import load_config, save_config

        with tempfile.TemporaryDirectory() as tmp, patch("src.config.APP_DIR", tmp):
            cfg = AppConfig(stt_custom_headers='{"X-Token": "s3cret"}')
            save_config(cfg)

            ini = Path(tmp, "settings.ini").read_text(encoding="utf-8")
            self.assertNotIn("s3cret", ini)

            self.assertEqual(load_config().stt_custom_headers, '{"X-Token": "s3cret"}')

    @unittest.skipUnless(platform.system() == "Windows", "DPAPI requires Windows")
    def test_save_config_purges_stale_plaintext_headers(self) -> None:
        from src.config import _get_qsettings, save_config

        with tempfile.TemporaryDirectory() as tmp, patch("src.config.APP_DIR", tmp):
            stale = _get_qsettings()
            stale.setValue("llm_custom_headers", '{"X-Old": "plain"}')
            stale.sync()
            del stale

            save_config(AppConfig())

            ini = Path(tmp, "settings.ini").read_text(encoding="utf-8")
            self.assertNotIn("X-Old", ini)


class EnvPathTests(unittest.TestCase):
    def test_env_path_uses_cwd_when_not_frozen(self) -> None:
        self.assertEqual(_env_path(), os.path.join(os.getcwd(), ".env"))

    def test_env_path_uses_exe_dir_when_frozen(self) -> None:
        exe = os.path.join("C:" + os.sep, "apps", "Screamer", "Screamer.exe")
        with patch.object(sys, "frozen", new=True, create=True), patch.object(sys, "executable", new=exe):
            self.assertEqual(_env_path(), os.path.join(os.path.dirname(exe), ".env"))


if __name__ == "__main__":
    unittest.main()
