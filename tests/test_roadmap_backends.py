import json
import contextlib
import io
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from src.config import AppConfig, ProviderConfig, load_config, save_config, validate_config
from src.rewrite import rewrite
from src.stt import transcribe
from src.utils import AppError, ScreamerError


class VocabularyPersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("src.config.APP_DIR", directory))
        self.enterContext(patch("src.config._dpapi_available", return_value=False))

    def test_vocabulary_roundtrip_preserves_display_spelling_and_order(self):
        cfg = AppConfig(
            vocabulary_entries=[" Screamer ", "QThread", "screamer", "", "Stra\u00dfe", "STRASSE"]
        )
        save_config(cfg)
        restored = load_config()
        self.assertEqual(restored.vocabulary_entries, ["Screamer", "QThread", "Stra\u00dfe"])
        self.assertFalse(restored.stt_prompt_primary_enabled)
        self.assertFalse(restored.stt_prompt_fallback_enabled)

    def test_damaged_vocabulary_is_backed_up_and_reset(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        damaged = '["keep this original", 42]'
        settings.setValue("vocabulary_entries", damaged)
        settings.sync()
        cfg = load_config()
        self.assertEqual(cfg.vocabulary_entries, [])
        self.assertEqual(cfg._damaged_settings, ["vocabulary_entries"])
        backup = Path(settings.fileName()).parent / "settings-damaged.txt"
        self.assertIn(f"vocabulary_entries = {damaged}", backup.read_text(encoding="utf-8"))
        cfg.stt_language = "cs"
        save_config(cfg)
        self.assertEqual(_get_qsettings().value("vocabulary_entries"), "[]")
        restored = load_config()
        self.assertEqual(restored.stt_language, "cs")
        self.assertEqual(restored._damaged_settings, [])

    VALID_STT = dict(stt_api_key="key", stt_base_url="https://example.test/v1", stt_model="stt")

    def assert_vocabulary_rejected(self, entries):
        issues = validate_config(AppConfig(**self.VALID_STT, vocabulary_entries=entries))
        self.assertTrue(any("vocabulary" in issue.message.lower() for issue in issues))

    def assert_preferences_rejected(self, **preferences):
        self.assertTrue(validate_config(AppConfig(**self.VALID_STT, **preferences)))

    def test_vocabulary_entry_with_line_break_cannot_be_applied(self):
        self.assert_vocabulary_rejected(["line\nbreak"])

    def test_vocabulary_entry_with_control_character_cannot_be_applied(self):
        self.assert_vocabulary_rejected(["control\x00"])

    def test_overlong_vocabulary_entry_cannot_be_applied(self):
        self.assert_vocabulary_rejected(["x" * 129])

    def test_too_many_vocabulary_entries_cannot_be_applied(self):
        self.assert_vocabulary_rejected([f"term{i}" for i in range(129)])

    def test_vocabulary_list_over_8000_rendered_characters_cannot_be_applied(self):
        # 63 terms of 128 chars render as 63 * 133 - 1 = 8378 list characters.
        self.assert_vocabulary_rejected([f"{i:03d}" + "x" * 125 for i in range(63)])

    def test_vocabulary_valid_under_old_prefix_still_loads_without_reset(self):
        """Lengthening the guidance prefix must never back up and reset stored terms."""
        from src.config import _get_qsettings

        old_prefix_length = 289  # len() of the previous "spelling guidance only" prefix
        suffix_length = len("\n[/Preferred spellings]")
        # Trim the last of 58 max-length terms so the old prefix + list + suffix is exactly 8,000.
        budget = 8000 - old_prefix_length - suffix_length
        terms = [f"{i:03d}" + "x" * 125 for i in range(58)]
        list_length = sum(len(json.dumps(t)) + 2 for t in terms) + len(terms) - 1
        terms[-1] = terms[-1][: len(terms[-1]) - (list_length - budget)]
        self.assertEqual(sum(len(json.dumps(t)) + 2 for t in terms) + len(terms) - 1, budget)

        settings = _get_qsettings()
        settings.setValue("vocabulary_entries", json.dumps(terms))
        settings.sync()
        cfg = load_config()
        self.assertEqual(cfg.vocabulary_entries, terms)
        self.assertEqual(cfg._damaged_settings, [])

    def test_unknown_output_mode_cannot_be_applied(self):
        self.assert_preferences_rejected(output_mode="guess")

    def test_zero_history_limit_cannot_be_applied(self):
        self.assert_preferences_rejected(history_limit=0)

    def test_history_limit_above_maximum_cannot_be_applied(self):
        self.assert_preferences_rejected(history_limit=101)

    def test_recovery_hotkey_equal_to_dictation_hotkey_cannot_be_applied(self):
        self.assert_preferences_rejected(recovery_hotkey="ctrl+alt+key:0x20")

    def test_recovery_hotkey_on_escape_cannot_be_applied(self):
        self.assert_preferences_rejected(recovery_hotkey="ctrl+alt+key:0x1B")

    def test_privacy_defaults_and_preferences_roundtrip(self):
        self.assertFalse(AppConfig().history_enabled)
        self.assertEqual(AppConfig().history_limit, 100)
        self.assertEqual(AppConfig().output_mode, "type")
        save_config(AppConfig(history_enabled=True, history_limit=7, output_mode="copy_and_type"))
        restored = load_config()
        self.assertEqual(
            (restored.history_enabled, restored.history_limit, restored.output_mode),
            (True, 7, "copy_and_type"),
        )


class VocabularyRequestTests(unittest.TestCase):
    def transcribe_via_fallback(self, *, primary_enabled, fallback_enabled):
        """Fail the primary, succeed on the fallback; return (primary, fallback) form data."""
        cfg = AppConfig(
            stt_api_key="primary",
            stt_base_url="https://primary.test/v1",
            stt_model="primary",
            stt_fallback_enabled=True,
            stt_fallback_api_key="fallback",
            stt_fallback_base_url="https://fallback.test/v1",
            stt_fallback_model="fallback",
            stt_language="cs",
            vocabulary_entries=["Screamer", "QThread"],
            stt_prompt_primary_enabled=primary_enabled,
            stt_prompt_fallback_enabled=fallback_enabled,
        )
        with patch(
            "src.http_client.post",
            side_effect=[
                httpx.ConnectError("offline"),
                httpx.Response(200, json={"text": "raw"}),
            ],
        ) as post:
            result = transcribe(b"wav", cfg)
        self.assertEqual(result.warnings, [AppError.STT_FALLBACK_USED])
        self.assertEqual(post.call_count, 2)
        primary_call, fallback_call = post.call_args_list
        return primary_call.kwargs["data"], fallback_call.kwargs["data"]

    def assert_prompted(self, data):
        self.assertEqual(data["language"], "cs")
        self.assertIn("prompt", data)
        self.assertIn("QThread", data["prompt"])

    def assert_not_prompted(self, data):
        self.assertEqual(data["language"], "cs")
        self.assertNotIn("prompt", data)

    def test_no_stt_provider_sends_prompt_without_opt_in(self):
        primary, fallback = self.transcribe_via_fallback(
            primary_enabled=False, fallback_enabled=False
        )
        self.assert_not_prompted(primary)
        self.assert_not_prompted(fallback)

    def test_primary_prompt_opt_in_does_not_prompt_fallback(self):
        primary, fallback = self.transcribe_via_fallback(
            primary_enabled=True, fallback_enabled=False
        )
        self.assert_prompted(primary)
        self.assert_not_prompted(fallback)

    def test_fallback_prompt_opt_in_does_not_prompt_primary(self):
        primary, fallback = self.transcribe_via_fallback(
            primary_enabled=False, fallback_enabled=True
        )
        self.assert_not_prompted(primary)
        self.assert_prompted(fallback)

    def test_both_prompt_opt_ins_prompt_both_stt_providers(self):
        primary, fallback = self.transcribe_via_fallback(
            primary_enabled=True, fallback_enabled=True
        )
        self.assert_prompted(primary)
        self.assert_prompted(fallback)

    def test_empty_vocabulary_omits_prompt_even_when_enabled(self):
        cfg = AppConfig(
            stt_api_key="key",
            stt_base_url="https://stt.test",
            stt_model="model",
            stt_prompt_primary_enabled=True,
        )
        with patch(
            "src.http_client.post", return_value=httpx.Response(200, json={"text": "raw"})
        ) as post:
            transcribe(b"wav", cfg)
        self.assertNotIn("prompt", post.call_args.kwargs["data"])

    def test_rewrite_guidance_asks_for_phonetic_replacement_but_keeps_prohibitions(self):
        from src.rewrite import format_vocabulary_context

        guidance = format_vocabulary_context(["PySide6"]).lower()
        self.assertIn("sounds like", guidance)
        self.assertIn("replace it with the exact term", guidance)
        for prohibition in (
            "do not answer",
            "execute",
            "translate",
            "invent content",
            "add terms that were not spoken",
            "guess missing words",
            "preserve meaning and language choices",
        ):
            self.assertIn(prohibition, guidance)

    def test_rewrite_guidance_is_separate_from_literal_transcript(self):
        raw = "Ask ChatGPT to explain this identifier."
        cfg = AppConfig(
            llm_enabled=True,
            llm_api_key="primary",
            llm_base_url="https://primary.test",
            llm_model="model",
            llm_system_prompt="Keep my exact policy.\r\n",
            vocabulary_entries=["Screamer", "QThread"],
            stt_language="cs",
        )
        with patch(
            "src.http_client.post",
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": "candidate"}}]}
            ),
        ) as post:
            result = rewrite(raw, cfg)
        prompts = [call.kwargs["json"]["messages"][0]["content"] for call in post.call_args_list]
        self.assertEqual(len(prompts), 1)
        self.assertTrue(prompts[0].startswith(cfg.llm_system_prompt))
        self.assertIn("QThread", prompts[0])
        self.assertIn("preferred", prompts[0].lower())
        self.assertIn("The speech language is cs.", prompts[0])
        self.assertEqual(
            post.call_args.kwargs["json"]["messages"][1], {"role": "user", "content": raw}
        )
        self.assertEqual(result.text, "candidate")
        self.assertEqual(cfg.vocabulary_entries, ["Screamer", "QThread"])


class KeylessSttTests(unittest.TestCase):
    def assert_cli_reaches_keyless_provider_after_env_backfill(self, cfg):
        import src.stt as module

        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory, "sample.wav")
        path.write_bytes(b"known audio bytes")
        output = io.StringIO()
        with (
            patch("sys.argv", ["stt", str(path)]),
            patch("src.config.setup_logging"),
            patch("src.config.load_config", return_value=AppConfig()),
            patch("src.config.import_from_env", return_value=cfg) as backfill,
            patch(
                "src.http_client.post",
                return_value=httpx.Response(200, json={"text": "CLI result"}),
            ) as post,
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as exited,
        ):
            runpy.run_path(module.__file__, run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        backfill.assert_called_once()
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("authorization", post.call_args.kwargs["headers"])
        self.assertIn("CLI result", output.getvalue())
        self.assertIn("STT latency: n=1", output.getvalue())

    def test_cli_reaches_keyless_primary_after_env_backfill(self):
        self.assert_cli_reaches_keyless_provider_after_env_backfill(
            AppConfig(stt_base_url="http://localhost:8000/v1", stt_model="local")
        )

    def test_cli_reaches_keyless_fallback_after_env_backfill(self):
        self.assert_cli_reaches_keyless_provider_after_env_backfill(
            AppConfig(
                stt_fallback_enabled=True,
                stt_fallback_base_url="http://localhost:8000/v1",
                stt_fallback_model="local",
            )
        )

    def test_url_and_model_allow_keyless_stt_and_keyless_llm(self):
        cfg = AppConfig(stt_base_url="http://127.0.0.1:8000/v1", stt_model="local")
        self.assertEqual(validate_config(cfg), [])
        self.assertTrue(ProviderConfig(base_url=cfg.stt_base_url, model="local").is_complete)
        self.assertFalse(ProviderConfig(api_key="key", base_url=cfg.stt_base_url).is_complete)
        cfg.rewrite_manual_profile_id = "clean"
        self.assertTrue(any(issue.tab_index == 2 for issue in validate_config(cfg)))
        cfg.llm_base_url = "http://127.0.0.1:8001/v1"
        cfg.llm_model = "llm"
        self.assertEqual(validate_config(cfg), [])

    def test_keyless_request_has_no_default_authorization_and_keeps_exact_route(self):
        cfg = AppConfig(stt_base_url="http://127.0.0.1:8000/v1/", stt_model="local")
        with patch(
            "src.http_client.post", return_value=httpx.Response(200, json={"text": "local result"})
        ) as post:
            self.assertEqual(transcribe(b"wav", cfg).text, "local result")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:8000/v1/audio/transcriptions")
        self.assertNotIn("authorization", post.call_args.kwargs["headers"])
        self.assertEqual(
            post.call_args.kwargs["files"]["file"], ("recording.wav", b"wav", "audio/wav")
        )
        self.assertEqual(post.call_args.kwargs["data"]["response_format"], "verbose_json")

    def test_keyless_fallback_keeps_its_own_custom_auth(self):
        cfg = AppConfig(
            stt_api_key="primary-secret",
            stt_base_url="https://primary.test",
            stt_model="cloud",
            stt_fallback_enabled=True,
            stt_fallback_base_url="http://127.0.0.1:8000/v1",
            stt_fallback_model="local",
            stt_fallback_custom_headers=json.dumps({"aUtHoRiZaTiOn": "Custom local-token"}),
        )
        with patch(
            "src.http_client.post",
            side_effect=[
                httpx.ConnectError("offline"),
                httpx.Response(200, json={"text": "fallback"}),
            ],
        ) as post:
            result = transcribe(b"wav", cfg)
        self.assertEqual(
            post.call_args_list[0].kwargs["headers"]["authorization"], "Bearer primary-secret"
        )
        self.assertEqual(
            post.call_args_list[1].kwargs["headers"].get_list("authorization"),
            ["Custom local-token"],
        )
        self.assertEqual(result.warnings, [AppError.STT_FALLBACK_USED])

    def test_keyless_fallback_can_be_the_only_configured_provider(self):
        cfg = AppConfig(
            stt_fallback_enabled=True,
            stt_fallback_base_url="http://localhost:8000/v1",
            stt_fallback_model="local",
        )
        self.assertEqual(validate_config(cfg), [])
        with patch(
            "src.http_client.post", return_value=httpx.Response(200, json={"text": "fallback"})
        ) as post:
            result = transcribe(b"wav", cfg)
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("authorization", post.call_args.kwargs["headers"])
        self.assertEqual(result.warnings, [AppError.STT_FALLBACK_USED])

    def test_local_failure_without_enabled_fallback_makes_no_cloud_request(self):
        cfg = AppConfig(
            stt_base_url="http://localhost:8000/v1",
            stt_model="local",
            stt_fallback_base_url="https://cloud.test",
            stt_fallback_model="cloud",
            stt_fallback_api_key="secret",
        )
        with patch("src.http_client.post", side_effect=httpx.ConnectError("offline")) as post:
            with self.assertRaises(ScreamerError):
                transcribe(b"wav", cfg)
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
