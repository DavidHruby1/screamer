"""Speech-to-text via httpx: primary + fallback providers, verbose_json, no_speech_prob filter."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from src.config import AppConfig, ProviderConfig, parse_custom_headers
from src.utils import AppError, ScreamerError

log = logging.getLogger(__name__)

_NO_SPEECH_THRESHOLD = 0.7


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start: float | None = None
    end: float | None = None
    no_speech_prob: float | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    warnings: list[AppError] = field(default_factory=list)


def is_meaningful_transcript(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if not any(ch.isalnum() for ch in normalized):
        return False
    if normalized in {"...", "\a", ".", ".."}:
        return False
    return True


def transcribe(audio_wav: bytes, config: AppConfig) -> TranscriptionResult:
    """POST WAV to STT endpoint with verbose_json. Primary → fallback if enabled and primary fails.

    Filter: keep if ANY segment no_speech_prob < 0.7. All-above → ScreamerError(AppError.NO_SPEECH).
    HTTP/network errors → ScreamerError(AppError.STT_FAILED).
    Fallback success → PipelineResult with AppError.STT_FALLBACK_USED in warnings.

    *config* supplies primary and fallback providers via ``AppConfig``.
    """
    warnings: list[AppError] = []

    primary = config.stt_provider()
    fallback = config.stt_fallback_provider()

    if not primary.is_complete and not fallback.is_complete:
        raise ScreamerError(AppError.STT_FAILED, "No STT API key configured")

    for is_fallback, provider, language in (
        (False, primary, config.stt_language),
        (True, fallback.provider, ""),
    ):
        if is_fallback and not fallback.enabled:
            continue
        if not provider.is_complete:
            continue

        try:
            result = _call_stt(provider=provider, language=language, audio_wav=audio_wav)
            if result is not None:
                if is_fallback:
                    warnings.append(AppError.STT_FALLBACK_USED)
                return TranscriptionResult(text=result.text, segments=result.segments, warnings=warnings)
        except ScreamerError:
            raise
        except Exception as e:
            log.warning("%s STT failed: %s", "Fallback" if is_fallback else "Primary", e)
            if not fallback.enabled:
                raise ScreamerError(AppError.STT_FAILED, str(e)) from e

    raise ScreamerError(AppError.STT_FAILED, "Both primary and fallback STT failed or returned no speech")


def _call_stt(
    provider: ProviderConfig,
    language: str,
    audio_wav: bytes,
) -> TranscriptionResult | None:
    """POST to an OpenAI-compatible STT endpoint. Returns text, or None if unconfigured."""
    if not provider.api_key:
        return None

    url = provider.base_url.rstrip("/") + "/audio/transcriptions"

    headers: dict[str, str] = {"Authorization": f"Bearer {provider.api_key}"}
    try:
        headers.update(parse_custom_headers(provider.custom_headers))
    except ValueError as e:
        log.warning("Invalid custom headers; ignoring: %s", e)

    data: dict[str, str] = {"model": provider.model, "response_format": "verbose_json"}
    if language:
        data["language"] = language

    files = {"file": ("recording.wav", audio_wav, "audio/wav")}

    log.info("STT request: url=%s model=%s", url, provider.model)
    resp = httpx.post(url, headers=headers, data=data, files=files, timeout=60.0)
    resp.raise_for_status()

    result = resp.json()
    segments = result.get("segments", [])
    parsed_segments = [
        TranscriptSegment(
            text=str(seg.get("text") or ""),
            start=seg.get("start"),
            end=seg.get("end"),
            no_speech_prob=seg.get("no_speech_prob"),
        )
        for seg in segments
    ]

    # Filter: keep if ANY segment has no_speech_prob < threshold.
    if segments:
        has_speech = any(seg.get("no_speech_prob", 0.0) < _NO_SPEECH_THRESHOLD for seg in segments)
        if not has_speech:
            log.debug("All segments above no_speech_prob threshold; filtering out")
            raise ScreamerError(AppError.NO_SPEECH)

    text = (result.get("text") or "").strip()
    if not is_meaningful_transcript(text):
        raise ScreamerError(AppError.NO_SPEECH)

    log.debug("STT result: %s", text[:80])
    return TranscriptionResult(text=text, segments=parsed_segments)


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
