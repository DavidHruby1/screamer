"""Speech-to-text via shared HTTP transport with Groq-aware request shaping."""

from __future__ import annotations

import logging
from time import perf_counter

from src import http_client
from src.config import AppConfig, ProviderConfig, parse_custom_headers
from src.utils import AppError, PipelineResult, ScreamerError, log_duration

log = logging.getLogger(__name__)

_NO_SPEECH_THRESHOLD = 0.7


def transcribe(audio_wav: bytes, config: AppConfig) -> PipelineResult:
    """POST WAV to STT endpoint. Groq uses ``response_format=json``; others keep ``verbose_json``.

    Filter: keep if ANY segment no_speech_prob < 0.7 for non-Groq providers. All-above → ScreamerError(AppError.NO_SPEECH).
    HTTP/network errors → ScreamerError(AppError.STT_FAILED).
    Fallback success → PipelineResult with AppError.STT_FALLBACK_USED in warnings.

    *config* supplies primary and fallback providers via ``AppConfig``.
    """
    with log_duration(log, "STT transcription"):
        warnings: list[AppError] = []

        primary = config.stt_provider()
        fallback = config.stt_fallback_provider()

        if not primary.is_complete and not fallback.is_complete:
            raise ScreamerError(AppError.STT_FAILED, "No STT API key configured")

        for is_fallback, provider, language in (
            (False, primary, config.stt_language),
            (True, fallback.provider, config.stt_language),
        ):
            if is_fallback and not fallback.enabled:
                continue
            if not provider.is_complete:
                continue

            try:
                text = _call_stt(provider=provider, language=language, audio_wav=audio_wav)
                if text is not None:
                    if is_fallback:
                        warnings.append(AppError.STT_FALLBACK_USED)
                    return PipelineResult(text=text, warnings=warnings)
            except ScreamerError:
                raise
            except Exception as e:
                log.warning("%s STT failed: %s", "Fallback" if is_fallback else "Primary", e)
                if not fallback.enabled:
                    raise ScreamerError(AppError.STT_FAILED, str(e)) from e

        raise ScreamerError(
            AppError.STT_FAILED, "Both primary and fallback STT failed or returned no speech"
        )


def _call_stt(
    provider: ProviderConfig,
    language: str,
    audio_wav: bytes,
) -> str | None:
    """POST to an OpenAI-compatible STT endpoint. Returns text, or None if unconfigured."""
    if not provider.api_key:
        return None

    url = provider.base_url.rstrip("/") + "/audio/transcriptions"
    response_format = "json" if provider.is_groq else "verbose_json"

    headers: dict[str, str] = {"Authorization": f"Bearer {provider.api_key}"}
    try:
        headers.update(parse_custom_headers(provider.custom_headers))
    except ValueError as e:
        log.warning("Invalid custom headers; ignoring: %s", e)

    data: dict[str, str] = {"model": provider.model, "response_format": response_format}
    if language:
        data["language"] = language

    files = {"file": ("recording.wav", audio_wav, "audio/wav")}

    log.info(
        "STT request: url=%s model=%s response_format=%s language=%s bytes=%d",
        url,
        provider.model,
        response_format,
        language or "auto",
        len(audio_wav),
    )
    request_started = perf_counter()
    resp = http_client.post(url, headers=headers, data=data, files=files, timeout=60.0)
    request_elapsed = perf_counter() - request_started

    log.info(
        "STT response: status=%s model=%s response_format=%s language=%s bytes=%d elapsed=%.3fs x_groq_region=%s cf_ray=%s",
        resp.status_code,
        provider.model,
        response_format,
        language or "auto",
        len(audio_wav),
        request_elapsed,
        resp.headers.get("x-groq-region") or "-",
        resp.headers.get("cf-ray") or "-",
    )
    resp.raise_for_status()

    result = resp.json()
    segments = result.get("segments", [])

    # Filter: keep if ANY segment has no_speech_prob < threshold.
    if segments and not provider.is_groq:
        has_speech = any(seg.get("no_speech_prob", 0.0) < _NO_SPEECH_THRESHOLD for seg in segments)
        if not has_speech:
            log.debug("All segments above no_speech_prob threshold; filtering out")
            raise ScreamerError(AppError.NO_SPEECH)

    text = (result.get("text") or "").strip()
    if not text:
        raise ScreamerError(AppError.NO_SPEECH)

    log.debug("STT result: %s", text[:80])
    return text


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    from src.config import import_from_env, load_config, setup_logging

    setup_logging()

    cfg = load_config()
    cfg = import_from_env(cfg)

    if not cfg.stt_api_key:
        print(
            "No API configuration found. Set up credentials via:\n"
            "  - Place a .env file in the project root\n"
            "  - Or run python -m src.settings_dialog (Phase 2)",
            file=sys.stderr,
        )
        sys.exit(1)

    wav_path = sys.argv[1] if len(sys.argv) > 1 else "test.wav"
    if not os.path.exists(wav_path):
        print(f"WAV file not found: {wav_path}", file=sys.stderr)
        sys.exit(1)

    with open(wav_path, "rb") as f:
        wav_bytes = f.read()

    try:
        result = transcribe(wav_bytes, cfg)
        print(f"Transcription: {result.text}")
        if result.warnings:
            print(f"Warnings: {[w.value for w in result.warnings]}")
    except ScreamerError as e:
        print(f"Error: {e.code.value}", file=sys.stderr)
        if e.detail:
            print(f"Detail: {e.detail}", file=sys.stderr)
        sys.exit(1)

    print()
    print("STT module OK")
