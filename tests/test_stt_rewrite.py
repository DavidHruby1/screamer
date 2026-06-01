import unittest
from unittest.mock import patch

import httpx

from src.config import AppConfig
from src.rewrite import rewrite
from src.stt import transcribe
from src.utils import AppError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

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

        with patch("src.stt.httpx.post", side_effect=fake_post):
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

        with patch("src.stt.httpx.post", side_effect=fake_post):
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

        with patch("src.rewrite.httpx.post", side_effect=fake_post):
            result = rewrite("fix text", cfg)

        self.assertEqual(result.text, "fixed text")
        self.assertEqual(result.warnings, [])
        self.assertEqual(calls, ["https://primary.test/v1/chat/completions", "https://fallback.test/v1/chat/completions"])


if __name__ == "__main__":
    unittest.main()
