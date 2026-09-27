import json
import os
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import (
    AppConfig,
    ProviderConfig,
    _env_path,
    import_from_env,
    parse_custom_headers,
    validate_config,
)
from src.utils import AppError, ScreamerError


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

    def test_complete_fallback_does_not_allow_partial_primary(self) -> None:
        cfg = AppConfig(
            stt_api_key="unfinished-primary",
            stt_fallback_enabled=True,
            stt_fallback_api_key="key",
            stt_fallback_base_url="https://example.test/v1",
            stt_fallback_model="stt",
        )
        self.assertEqual(
            [issue.message for issue in validate_config(cfg)],
            ["Primary STT requires an API key, base URL, and model."],
        )

    def test_plain_http_remains_supported(self) -> None:
        cfg = AppConfig(stt_api_key="key", stt_base_url="http://localhost:8080/v1", stt_model="stt")
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
        self.assertEqual(
            parse_custom_headers('{"X-Test": "part\\tpart"}'), {"X-Test": "part\tpart"}
        )

    def test_provider_config_detects_groq_by_host(self) -> None:
        self.assertTrue(ProviderConfig(base_url="https://api.groq.com/openai/v1").is_groq)
        self.assertFalse(ProviderConfig(base_url="https://api.openai.com/v1").is_groq)

    def test_invalid_http_headers_and_urls_are_rejected(self) -> None:
        cfg = AppConfig(
            stt_api_key="key",
            stt_base_url="not-a-url",
            stt_model="stt",
            stt_custom_headers='{"X-Test\\r\\nBad": "token"}',
        )
        messages = [issue.message for issue in validate_config(cfg)]
        self.assertTrue(any("base URL is invalid" in message for message in messages))
        self.assertTrue(any("custom headers are invalid" in message for message in messages))
        with self.assertRaises(ValueError):
            parse_custom_headers('{"X-Test": "line\\nfeed"}')

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

    def test_has_plaintext_secrets_detects_stale_ini_values(self) -> None:
        from src.config import _get_qsettings, has_plaintext_secrets

        with tempfile.TemporaryDirectory() as tmp, patch("src.config.APP_DIR", tmp):
            self.assertFalse(has_plaintext_secrets())

            stale = _get_qsettings()
            stale.setValue("llm_custom_headers", '{"X-Old": "plain"}')
            stale.sync()
            del stale

            self.assertTrue(has_plaintext_secrets())

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

    def test_migration_preserves_plaintext_if_encryption_fails(self) -> None:
        from src.config import _get_qsettings, has_plaintext_secrets, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
            patch(
                "src.config._dpapi_encrypt", side_effect=ScreamerError(AppError.KEY_STORAGE_FAILED)
            ),
        ):
            settings = _get_qsettings()
            settings.setValue("stt_custom_headers", '{"X-Token": "old"}')
            settings.sync()

            with self.assertRaises(ScreamerError) as error:
                save_config(AppConfig(stt_custom_headers='{"X-Token": "old"}'))

            self.assertEqual(error.exception.code, AppError.KEY_STORAGE_FAILED)
            self.assertTrue(has_plaintext_secrets())
            self.assertEqual(_get_qsettings().value("stt_custom_headers"), '{"X-Token": "old"}')
            self.assertFalse(Path(tmp, "keys.enc").exists())
            self.assertEqual(list(Path(tmp).glob(".keys-*")), [])

    def test_failed_settings_replacement_keeps_old_url_and_secret(self) -> None:
        from src.config import load_config, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
            patch("src.config._dpapi_encrypt", side_effect=lambda val: val.encode().hex()),
            patch("src.config._dpapi_decrypt", side_effect=lambda val: bytes.fromhex(val).decode()),
        ):
            save_config(
                AppConfig(stt_api_key="old", stt_base_url="https://old.test", stt_model="stt")
            )

            before = Path(tmp, "settings.ini").read_bytes()
            with patch("src.config.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(ScreamerError):
                    save_config(
                        AppConfig(
                            stt_api_key="new", stt_base_url="https://new.test", stt_model="stt"
                        )
                    )
            restored = load_config()
            self.assertEqual(
                (restored.stt_api_key, restored.stt_base_url), ("old", "https://old.test")
            )
            self.assertEqual(Path(tmp, "settings.ini").read_bytes(), before)
            self.assertEqual(list(Path(tmp).glob(".settings-*")), [])

    def test_successful_replacement_reloads_new_url_and_secret(self) -> None:
        from src.config import load_config, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
            patch("src.config._dpapi_encrypt", side_effect=lambda val: val.encode().hex()),
            patch("src.config._dpapi_decrypt", side_effect=lambda val: bytes.fromhex(val).decode()),
        ):
            save_config(AppConfig(stt_api_key="old", stt_base_url="https://old.test"))
            self.assertEqual(load_config().stt_api_key, "old")
            save_config(AppConfig(stt_api_key="new", stt_base_url="https://new.test"))
            actual = load_config()
            self.assertEqual((actual.stt_api_key, actual.stt_base_url), ("new", "https://new.test"))

    def test_encrypted_legacy_secret_takes_precedence_over_stale_plaintext(self) -> None:
        from src.config import _get_qsettings, load_config, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
            patch("src.config._dpapi_encrypt", side_effect=lambda val: val.encode().hex()),
            patch("src.config._dpapi_decrypt", side_effect=lambda val: bytes.fromhex(val).decode()),
        ):
            Path(tmp, "keys.enc").write_text('{"stt_api_key":"6e6577"}', encoding="utf-8")
            settings = _get_qsettings()
            settings.setValue("stt_api_key", "old")
            settings.setValue("stt_base_url", "https://new.test")
            settings.sync()
            restored = load_config()
            self.assertEqual(restored.stt_api_key, "new")
            save_config(restored)
            self.assertEqual(load_config().stt_api_key, "new")
            self.assertNotIn(
                "stt_api_key=old", Path(tmp, "settings.ini").read_text(encoding="utf-8")
            )

    def test_legacy_plaintext_is_not_misidentified_as_ciphertext_by_prefix(self) -> None:
        from src.config import _get_qsettings, load_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
        ):
            settings = _get_qsettings()
            settings.setValue("stt_api_key", "dpapi:literal-provider-token")
            settings.sync()
            self.assertEqual(load_config().stt_api_key, "dpapi:literal-provider-token")

    def test_incomplete_encrypted_ini_fails_closed(self) -> None:
        from src.config import _get_qsettings, load_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
        ):
            settings = _get_qsettings()
            settings.setValue("secret_storage", "dpapi-v1")
            settings.setValue("stt_base_url", "https://example.test")
            settings.sync()
            with self.assertRaises(ScreamerError) as error:
                load_config()
            self.assertEqual(error.exception.code, AppError.KEY_STORAGE_FAILED)

    def test_clearing_secret_does_not_resurrect_legacy_key_if_cleanup_fails(self) -> None:
        from src.config import load_config, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
            patch("src.config._dpapi_encrypt", side_effect=lambda val: val.encode().hex()),
            patch("src.config._dpapi_decrypt", side_effect=lambda val: bytes.fromhex(val).decode()),
        ):
            Path(tmp, "keys.enc").write_text('{"stt_api_key":"6f6c64"}', encoding="utf-8")
            real_unlink = os.unlink

            def unlink(path):
                if str(path).endswith("keys.enc"):
                    raise OSError("locked")
                real_unlink(path)

            with patch("src.config.os.unlink", side_effect=unlink):
                save_config(AppConfig(stt_base_url="https://example.test"))
            self.assertEqual(load_config().stt_api_key, "")
            Path(tmp, "keys.enc").write_text("damaged", encoding="utf-8")
            self.assertEqual(load_config().stt_api_key, "")

    def test_encryption_failure_leaves_plaintext_untouched(self) -> None:
        from src.config import _get_qsettings, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
            patch(
                "src.config._dpapi_encrypt", side_effect=ScreamerError(AppError.KEY_STORAGE_FAILED)
            ),
        ):
            settings = _get_qsettings()
            settings.setValue("llm_custom_headers", "legacy")
            settings.sync()
            with self.assertRaises(ScreamerError):
                save_config(AppConfig(llm_custom_headers="legacy"))
            self.assertEqual(_get_qsettings().value("llm_custom_headers"), "legacy")

    def test_unreadable_store_fails_closed_instead_of_overwriting(self) -> None:
        from src.config import load_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
        ):
            Path(tmp, "keys.enc").write_text("not-json", encoding="utf-8")
            with self.assertRaises(ScreamerError) as error:
                load_config()
            self.assertEqual(error.exception.code, AppError.KEY_STORAGE_FAILED)

    def test_non_windows_save_does_not_purge_legacy_secret(self) -> None:
        from src.config import _get_qsettings, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=False),
        ):
            settings = _get_qsettings()
            settings.setValue("stt_custom_headers", "legacy")
            settings.sync()
            save_config(AppConfig(stt_custom_headers="legacy"))
            self.assertEqual(_get_qsettings().value("stt_custom_headers"), "legacy")

    def test_non_windows_save_does_not_purge_windows_ciphertext(self) -> None:
        from src.config import _get_qsettings, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=False),
        ):
            settings = _get_qsettings()
            settings.setValue("stt_api_key", "dpapi:stored-on-windows")
            settings.sync()
            save_config(AppConfig())
            self.assertEqual(_get_qsettings().value("stt_api_key"), "dpapi:stored-on-windows")

    def test_migration_roundtrips_headers_after_successful_encrypted_save(self) -> None:
        from src.config import _get_qsettings, has_plaintext_secrets, load_config, save_config

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("src.config.APP_DIR", tmp),
            patch("src.config._dpapi_available", return_value=True),
            patch("src.config._dpapi_encrypt", side_effect=lambda value: value.encode().hex()),
            patch(
                "src.config._dpapi_decrypt", side_effect=lambda value: bytes.fromhex(value).decode()
            ),
        ):
            settings = _get_qsettings()
            settings.setValue("stt_custom_headers", '{"X-Token": "legacy"}')
            settings.sync()
            config = load_config()
            self.assertTrue(has_plaintext_secrets())

            save_config(config)

            self.assertFalse(has_plaintext_secrets())
            self.assertEqual(load_config().stt_custom_headers, '{"X-Token": "legacy"}')
            self.assertNotIn("legacy", Path(tmp, "settings.ini").read_text(encoding="utf-8"))


class EnvPathTests(unittest.TestCase):
    def test_env_path_uses_cwd_when_not_frozen(self) -> None:
        self.assertEqual(_env_path(), os.path.join(os.getcwd(), ".env"))

    def test_env_path_uses_exe_dir_when_frozen(self) -> None:
        exe = os.path.join("C:" + os.sep, "apps", "Screamer", "Screamer.exe")
        with (
            patch.object(sys, "frozen", new=True, create=True),
            patch.object(sys, "executable", new=exe),
        ):
            self.assertEqual(_env_path(), os.path.join(os.path.dirname(exe), ".env"))


if __name__ == "__main__":
    unittest.main()
