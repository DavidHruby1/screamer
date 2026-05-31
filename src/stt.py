"""Speech-to-text via httpx: primary + fallback providers, verbose_json, no_speech_prob filter."""

from __future__ import annotations

import io
import json
import logging

import httpx

from src.utils import AppError, PipelineResult, ScreamerError

log = logging.getLogger(__name__)

_NO_SPEECH_THRESHOLD = 0.7


def transcribe(audio_wav: bytes, config: object) -> PipelineResult:
    """POST WAV to STT endpoint with verbose_json. Primary → fallback if enabled and primary fails.

    Filter: keep if ANY segment no_speech_prob < 0.7. All-above → ScreamerError(AppError.NO_SPEECH).
    HTTP/network errors → ScreamerError(AppError.STT_FAILED).
    Fallback success → PipelineResult with AppError.STT_FALLBACK_USED in warnings.

    *config* must expose the same attribute names as ``AppConfig``.
    """
    warnings: list[AppError] = []

    if not config.stt_api_key and not (config.stt_fallback_enabled and config.stt_fallback_api_key):
        raise ScreamerError(AppError.STT_FAILED, "No STT API key configured")

    # Try primary STT.
    try:
        text = _call_stt(
            api_key=config.stt_api_key,
            base_url=config.stt_base_url,
            model=config.stt_model,
            language=config.stt_language,
            custom_headers=config.stt_custom_headers,
            audio_wav=audio_wav,
        )
        if text is not None:
            return PipelineResult(text=text, warnings=warnings)
    except ScreamerError:
        raise
    except Exception as e:
        log.warning("Primary STT failed: %s", e)
        if not config.stt_fallback_enabled:
            raise ScreamerError(AppError.STT_FAILED, str(e)) from e

    # Try fallback STT.
    if config.stt_fallback_enabled and config.stt_fallback_api_key:
        try:
            text = _call_stt(
                api_key=config.stt_fallback_api_key,
                base_url=config.stt_fallback_base_url,
                model=config.stt_fallback_model,
                language="",
                custom_headers=config.stt_fallback_custom_headers,
                audio_wav=audio_wav,
            )
            if text is not None:
                warnings.append(AppError.STT_FALLBACK_USED)
                return PipelineResult(text=text, warnings=warnings)
        except Exception as e:
            log.warning("Fallback STT failed: %s", e)

    raise ScreamerError(AppError.STT_FAILED, "Both primary and fallback STT failed or returned no speech")


def _call_stt(
    api_key: str,
    base_url: str,
    model: str,
    language: str,
    custom_headers: str,
    audio_wav: bytes,
) -> str | None:
    """POST to an OpenAI-compatible STT endpoint. Returns text, or None if unconfigured."""
    if not api_key:
        return None

    url = (base_url.rstrip("/") if base_url else "") + "/audio/transcriptions"

    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    if custom_headers:
        try:
            headers.update(json.loads(custom_headers))
        except json.JSONDecodeError:
            log.warning("Invalid custom headers JSON; ignoring: %s", custom_headers[:50])

    data: dict[str, str] = {"model": model, "response_format": "verbose_json"}
    if language:
        data["language"] = language

    files = {"file": ("recording.wav", audio_wav, "audio/wav")}

    log.info("STT request: url=%s model=%s", url, model)
    resp = httpx.post(url, headers=headers, data=data, files=files, timeout=60.0)
    resp.raise_for_status()

    result = resp.json()
    segments = result.get("segments", [])

    # Filter: keep if ANY segment has no_speech_prob < threshold.
    if segments:
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
