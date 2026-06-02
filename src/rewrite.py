"""LLM rewrite via httpx: primary + fallback providers, prompt template with system prompt."""

from __future__ import annotations

import logging

import httpx

from src.config import AppConfig, ProviderConfig, parse_custom_headers
from src.utils import AppError, PipelineResult, ScreamerError

log = logging.getLogger(__name__)


def rewrite(text: str, config: AppConfig) -> PipelineResult:
    """Send text to LLM with system prompt. Primary → fallback.

    Returns input text unchanged in ``PipelineResult.text`` if ``config.llm_enabled`` is False.
    Provider errors return the original text with ``AppError.LLM_FAILED`` as a warning.
    """
    if not config.llm_enabled:
        return PipelineResult(text=text)

    system_prompt = config.llm_system_prompt or ""
    language = getattr(config, "stt_language", "")
    if language:
        system_prompt += f"\nThe speech language is {language}."

    primary = config.llm_provider()
    fallback = config.llm_fallback_provider()

    for is_fallback, provider in ((False, primary), (True, fallback.provider)):
        if is_fallback and not fallback.enabled:
            continue
        if not provider.is_complete:
            continue

        try:
            result = _call_llm(provider=provider, system_prompt=system_prompt, user_text=text)
            if result:
                log.debug("%s LLM rewrite: %r → %r", "Fallback" if is_fallback else "Primary", text[:60], result[:60])
                return PipelineResult(text=result)
        except Exception as e:
            log.warning("%s LLM failed: %s", "Fallback" if is_fallback else "Primary", e)
            if not fallback.enabled:
                return PipelineResult(text=text, warnings=[AppError.LLM_FAILED])

    log.debug("LLM rewrite failed or returned empty; using original text")
    return PipelineResult(text=text, warnings=[AppError.LLM_FAILED])


def _call_llm(
    provider: ProviderConfig,
    system_prompt: str,
    user_text: str,
) -> str | None:
    """Call an OpenAI-compatible chat completions endpoint. Returns corrected text or None."""
    if not provider.api_key:
        return None

    url = provider.base_url.rstrip("/") + "/chat/completions"

    headers: dict[str, str] = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    try:
        headers.update(parse_custom_headers(provider.custom_headers))
    except ValueError as e:
        log.warning("Invalid custom headers; ignoring: %s", e)

    body = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.0,
    }

    log.info("LLM request: url=%s model=%s", url, provider.model)
    resp = httpx.post(url, headers=headers, json=body, timeout=30.0)
    resp.raise_for_status()

    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content.strip() if content else None


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from src.config import import_from_env, load_config, setup_logging

    setup_logging()

    cfg = load_config()
    cfg = import_from_env(cfg)

    if not cfg.llm_api_key:
        print(
            "No LLM configuration found. Set up credentials via:\n"
            "  - Place a .env file in the project root\n"
            "  - Or run python -m src.settings_dialog (Phase 2)",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg.llm_enabled = True

    text = sys.argv[1] if len(sys.argv) > 1 else "test sentense wit erors"
    print(f"Input: {text}")

    try:
        result = rewrite(text, cfg)
        print(f"Output: {result.text}")
        if result.warnings:
            print(f"Warnings: {[w.value for w in result.warnings]}")
    except ScreamerError as e:
        print(f"Error: {e.code.value}", file=sys.stderr)
        sys.exit(1)

    print()
    print("Rewrite module OK")
