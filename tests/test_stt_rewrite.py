import unittest
from unittest.mock import patch

import httpx

import src.http_client as http_client
from src.config import AppConfig
from src.rewrite import rewrite
from src.stt import transcribe
from src.utils import AppError, ScreamerError


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class SttRewriteFallbackTests(unittest.TestCase):
    def test_stt_uses_fallback_after_primary_http_failure(self) -> None:
        cfg = AppConfig(
            stt_api_key="primary",
            stt_base_url="https://primary.test/v1",
            stt_model="stt-primary",
            stt_fallback_enabled=True,
            stt_fallback_api_key="fallback",
            stt_fallback_base_url="https://fallback.test/v1",
            stt_fallback_model="stt-fallback",
        )

        calls: list[str] = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if "primary" in url:
                raise httpx.ConnectError("primary failed")
            return FakeResponse({"text": "fallback text", "segments": [{"no_speech_prob": 0.1}]})

        with patch("src.http_client.post", side_effect=fake_post):
            result = transcribe(b"wav", cfg)

        self.assertEqual(result.text, "fallback text")
        self.assertEqual(result.warnings, [AppError.STT_FALLBACK_USED])
        self.assertEqual(calls, ["https://primary.test/v1/audio/transcriptions", "https://fallback.test/v1/audio/transcriptions"])

    def test_stt_merges_custom_headers(self) -> None:
        cfg = AppConfig(
            stt_api_key="primary",
            stt_base_url="https://primary.test/v1",
            stt_model="stt-primary",
            stt_custom_headers='{"X-Test": "yes"}',
        )
        captured_headers: dict[str, str] = {}

        def fake_post(_url, **kwargs):
            captured_headers.update(kwargs["headers"])
            return FakeResponse({"text": "hello", "segments": [{"no_speech_prob": 0.1}]})

        with patch("src.http_client.post", side_effect=fake_post):
            transcribe(b"wav", cfg)

        self.assertEqual(captured_headers["Authorization"], "Bearer primary")
        self.assertEqual(captured_headers["X-Test"], "yes")

    def test_rewrite_uses_fallback_after_primary_http_failure(self) -> None:
        cfg = AppConfig(
            stt_api_key="stt",
            stt_base_url="https://stt.test/v1",
            stt_model="stt",
            llm_enabled=True,
            llm_api_key="primary",
            llm_base_url="https://primary.test/v1",
            llm_model="llm-primary",
            llm_fallback_enabled=True,
            llm_fallback_api_key="fallback",
            llm_fallback_base_url="https://fallback.test/v1",
            llm_fallback_model="llm-fallback",
        )

        calls: list[str] = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if "primary" in url:
                raise httpx.ConnectError("primary failed")
            return FakeResponse({"choices": [{"message": {"content": "fixed text"}}]})

        with patch("src.http_client.post", side_effect=fake_post):
            result = rewrite("fix text", cfg)

        self.assertEqual(result.text, "fixed text")
        self.assertEqual(result.warnings, [])
        self.assertEqual(calls, ["https://primary.test/v1/chat/completions", "https://fallback.test/v1/chat/completions"])

    def test_stt_groq_uses_json_response_format(self) -> None:
        cfg = AppConfig(
            stt_api_key="groq",
            stt_base_url="https://api.groq.com/openai/v1",
            stt_model="whisper-large-v3-turbo",
            stt_language="en",
        )
        captured: dict[str, object] = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["data"])
            return FakeResponse({"text": "hello", "segments": [{"no_speech_prob": 0.1}]})

        with patch("src.http_client.post", side_effect=fake_post):
            result = transcribe(b"wav", cfg)

        self.assertEqual(result.text, "hello")
        self.assertEqual(captured["response_format"], "json")
        self.assertEqual(captured["language"], "en")

    def test_stt_non_groq_keeps_verbose_json(self) -> None:
        cfg = AppConfig(
            stt_api_key="openai",
            stt_base_url="https://api.openai.com/v1",
            stt_model="whisper-1",
        )
        captured: dict[str, object] = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["data"])
            return FakeResponse({"text": "hello", "segments": [{"no_speech_prob": 0.1}]})

        with patch("src.http_client.post", side_effect=fake_post):
            result = transcribe(b"wav", cfg)

        self.assertEqual(result.text, "hello")
        self.assertEqual(captured["response_format"], "verbose_json")

    def test_stt_empty_text_raises_no_speech(self) -> None:
        cfg = AppConfig(
            stt_api_key="openai",
            stt_base_url="https://api.openai.com/v1",
            stt_model="whisper-1",
        )

        def fake_post(_url, **kwargs):
            return FakeResponse({"text": "", "segments": [{"no_speech_prob": 0.1}]})

        with patch("src.http_client.post", side_effect=fake_post):
            with self.assertRaises(ScreamerError) as ctx:
                transcribe(b"wav", cfg)

        self.assertEqual(ctx.exception.code, AppError.NO_SPEECH)

    def test_rewrite_groq_sets_dynamic_completion_cap(self) -> None:
        cfg = AppConfig(
            llm_enabled=True,
            llm_api_key="groq",
            llm_base_url="https://api.groq.com/openai/v1",
            llm_model="llama-3.1-8b-instant",
        )
        captured: dict[str, object] = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["json"])
            return FakeResponse({"choices": [{"message": {"content": "fixed text"}, "finish_reason": "stop"}]})

        with patch("src.http_client.post", side_effect=fake_post):
            result = rewrite("hello world" * 80, cfg)

        self.assertEqual(result.text, "fixed text")
        self.assertEqual(captured["temperature"], 0.0)
        self.assertIn("max_completion_tokens", captured)
        self.assertGreaterEqual(captured["max_completion_tokens"], 128)
        self.assertLessEqual(captured["max_completion_tokens"], 1024)

    def test_rewrite_groq_cap_scales_for_long_dictation(self) -> None:
        cfg = AppConfig(
            llm_enabled=True,
            llm_api_key="groq",
            llm_base_url="https://api.groq.com/openai/v1",
            llm_model="llama-3.1-8b-instant",
        )
        captured: dict[str, object] = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["json"])
            return FakeResponse({"choices": [{"message": {"content": "fixed text"}, "finish_reason": "stop"}]})

        with patch("src.http_client.post", side_effect=fake_post):
            rewrite("a" * 10_000, cfg)

        # 10_000 chars -> ~2500 estimated input tokens -> cap int(2500 * 1.5) + 32 = 3782
        self.assertEqual(captured["max_completion_tokens"], 3782)

    def test_rewrite_length_finish_keeps_original_text(self) -> None:
        cfg = AppConfig(
            llm_enabled=True,
            llm_api_key="groq",
            llm_base_url="https://api.groq.com/openai/v1",
            llm_model="llama-3.1-8b-instant",
        )

        def fake_post(_url, **kwargs):
            return FakeResponse({"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})

        with patch("src.http_client.post", side_effect=fake_post):
            result = rewrite("raw text", cfg)

        self.assertEqual(result.text, "raw text")
        self.assertEqual(result.warnings, [AppError.LLM_FAILED])

    def test_rewrite_length_finish_keeps_original_text_for_non_groq(self) -> None:
        cfg = AppConfig(
            llm_enabled=True,
            llm_api_key="openai",
            llm_base_url="https://api.openai.com/v1",
            llm_model="gpt-4o-mini",
        )

        def fake_post(_url, **kwargs):
            return FakeResponse({"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})

        with patch("src.http_client.post", side_effect=fake_post):
            result = rewrite("raw text", cfg)

        self.assertEqual(result.text, "raw text")
        self.assertEqual(result.warnings, [AppError.LLM_FAILED])

    def test_transport_close_is_idempotent(self) -> None:
        close_calls: list[int] = []

        class FakeClient:
            def post(self, *args, **kwargs):
                return FakeResponse({"text": "x"})

            def close(self):
                close_calls.append(1)

        with patch("src.http_client.httpx.Client", return_value=FakeClient()):
            http_client.close()
            http_client.post("https://example.test", headers={})
            http_client.close()
            http_client.close()

        self.assertEqual(len(close_calls), 1)


if __name__ == "__main__":
    unittest.main()
