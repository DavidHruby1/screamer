import json
import os
import tempfile
import unittest
from pathlib import Path

from src.config import AppConfig, ProviderConfig, import_from_env, parse_custom_headers, validate_config


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


if __name__ == "__main__":
    unittest.main()
