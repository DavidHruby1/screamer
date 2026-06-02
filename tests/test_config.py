import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import (
    AppConfig,
    _autostart_command,
    import_from_env,
    is_autostart_enabled,
    parse_custom_headers,
    set_autostart,
    validate_config,
)


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


class AutostartTests(unittest.TestCase):
    def test_autostart_defaults_off(self) -> None:
        self.assertFalse(AppConfig().autostart)

    def test_is_autostart_enabled_false_off_windows(self) -> None:
        with mock.patch("src.config.platform.system", return_value="Linux"):
            self.assertFalse(is_autostart_enabled())

    def test_set_autostart_noop_off_windows(self) -> None:
        with mock.patch("src.config.platform.system", return_value="Linux"):
            # Neither enabling nor disabling should raise where there is no registry.
            set_autostart(True)
            set_autostart(False)

    def test_autostart_command_frozen_uses_bare_exe(self) -> None:
        with mock.patch.object(sys, "executable", r"C:\Apps\Screamer\Screamer.exe"), \
                mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(_autostart_command(), r'"C:\Apps\Screamer\Screamer.exe"')

    def test_autostart_command_dev_runs_module(self) -> None:
        with mock.patch.object(sys, "executable", r"C:\Py\python.exe"):
            # Ensure dev mode (not frozen) regardless of how tests are launched.
            if hasattr(sys, "frozen"):
                self.skipTest("running under a frozen interpreter")
            self.assertEqual(_autostart_command(), r'"C:\Py\python.exe" -m src.main')


if __name__ == "__main__":
    unittest.main()
