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

    def test_damaged_vocabulary_survives_unrelated_save(self):
        from src.config import _get_qsettings

        settings = _get_qsettings()
        damaged = '["keep this original", 42]'
        settings.setValue("vocabulary_entries", damaged)
        settings.sync()
        cfg = load_config()
        self.assertEqual(cfg.vocabulary_entries, [])
        cfg.stt_language = "cs"
        save_config(cfg)
        self.assertEqual(_get_qsettings().value("vocabulary_entries"), damaged)
        self.assertEqual(load_config().stt_language, "cs")

    def test_invalid_vocabulary_and_output_preferences_cannot_be_applied(self):
        valid = dict(stt_api_key="key", stt_base_url="https://example.test/v1", stt_model="stt")
        for entries in (
            ["line\nbreak"],
            ["control\x00"],
            ["x" * 129],
            [f"term{i}" for i in range(129)],
        ):
            with self.subTest(entries=entries):
                issues = validate_config(AppConfig(**valid, vocabulary_entries=entries))
                self.assertTrue(any("vocabulary" in issue.message.lower() for issue in issues))
        for preferences in (
            {"output_mode": "guess"},
            {"history_limit": 0},
            {"history_limit": 101},
            {"recovery_hotkey": "ctrl+alt+key:0x20"},
            {"recovery_hotkey": "ctrl+alt+key:0x1B"},
        ):
            with self.subTest(preferences=preferences):
                self.assertTrue(validate_config(AppConfig(**valid, **preferences)))

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
    def test_each_stt_provider_uses_its_own_prompt_opt_in(self):
        for primary_enabled, fallback_enabled in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ):
            with self.subTest(primary=primary_enabled, fallback=fallback_enabled):
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
                for call, enabled in zip(post.call_args_list, (primary_enabled, fallback_enabled)):
                    data = call.kwargs["data"]
                    self.assertEqual(data["language"], "cs")
                    self.assertEqual("prompt" in data, enabled)
                    if enabled:
                        self.assertIn("QThread", data["prompt"])

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

    def test_rewrite_guidance_is_separate_from_literal_transcript_and_shared_by_fallback(self):
        raw = "Ask ChatGPT to explain this identifier."
        cfg = AppConfig(
            llm_enabled=True,
            llm_api_key="primary",
            llm_base_url="https://primary.test",
            llm_model="model",
            llm_fallback_enabled=True,
            llm_fallback_api_key="fallback",
            llm_fallback_base_url="https://fallback.test",
            llm_fallback_model="model",
            llm_system_prompt="Keep my exact policy.\r\n",
            llm_prompt_origin="user_saved",
            vocabulary_entries=["Screamer", "QThread"],
            stt_language="cs",
        )
        with patch(
            "src.http_client.post",
            side_effect=[
                httpx.ConnectError("offline"),
                httpx.Response(200, json={"choices": [{"message": {"content": "candidate"}}]}),
            ],
        ) as post:
            result = rewrite(raw, cfg)
        prompts = [call.kwargs["json"]["messages"][0]["content"] for call in post.call_args_list]
        self.assertEqual(prompts[0], prompts[1])
        self.assertTrue(prompts[0].startswith(cfg.llm_system_prompt))
        self.assertIn("QThread", prompts[0])
        self.assertIn("preferred", prompts[0].lower())
        self.assertIn("The speech language is cs.", prompts[0])
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["json"]["messages"][1], {"role": "user", "content": raw})
        self.assertEqual(result.text, "candidate")
        self.assertEqual(cfg.vocabulary_entries, ["Screamer", "QThread"])


class KeylessSttTests(unittest.TestCase):
    def test_cli_reaches_keyless_primary_or_fallback_after_env_backfill(self):
        import src.stt as module

        for cfg in (
            AppConfig(stt_base_url="http://localhost:8000/v1", stt_model="local"),
            AppConfig(
                stt_fallback_enabled=True,
                stt_fallback_base_url="http://localhost:8000/v1",
                stt_fallback_model="local",
            ),
        ):
            with (
                self.subTest(fallback=cfg.stt_fallback_enabled),
                tempfile.TemporaryDirectory() as directory,
            ):
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
                ):
                    runpy.run_path(module.__file__, run_name="__main__")
                backfill.assert_called_once()
                self.assertEqual(post.call_count, 1)
                self.assertNotIn("authorization", post.call_args.kwargs["headers"])
                self.assertIn("Transcription: CLI result", output.getvalue())

    def test_url_and_model_allow_keyless_stt_but_not_keyless_llm(self):
        cfg = AppConfig(stt_base_url="http://127.0.0.1:8000/v1", stt_model="local")
        self.assertEqual(validate_config(cfg), [])
        self.assertFalse(ProviderConfig(base_url=cfg.stt_base_url, model="local").is_complete)
        cfg.llm_enabled = True
        cfg.llm_base_url = "http://127.0.0.1:8001/v1"
        cfg.llm_model = "llm"
        self.assertTrue(any(issue.tab_index == 2 for issue in validate_config(cfg)))

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
