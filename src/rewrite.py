"""LLM rewrite via shared HTTP transport: primary + fallback providers, prompt template with system prompt."""

from __future__ import annotations

import logging
from time import perf_counter

from src import http_client
from src.config import AppConfig, ProviderConfig, parse_custom_headers
from src.utils import AppError, PipelineResult, ScreamerError, log_duration

log = logging.getLogger(__name__)


def rewrite(text: str, config: AppConfig) -> PipelineResult:
    """Send text to LLM with system prompt. Primary → fallback.

    Returns input text unchanged in ``PipelineResult.text`` if ``config.llm_enabled`` is False.
    Provider errors return the original text with ``AppError.LLM_FAILED`` as a warning.
    """
    with log_duration(log, "LLM rewrite"):
        if not config.llm_enabled:
            return PipelineResult(text=text)

        system_prompt = config.llm_system_prompt or ""
        language = config.stt_language
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

    body: dict[str, object] = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.0,
    }
    output_cap = _groq_completion_cap(user_text) if provider.is_groq else None
    if output_cap is not None:
        body["max_completion_tokens"] = output_cap

    log.info(
        "LLM request: url=%s model=%s input_chars=%d output_cap=%s",
        url,
        provider.model,
        len(user_text),
        output_cap if output_cap is not None else "none",
    )
    request_started = perf_counter()
    resp = http_client.post(url, headers=headers, json=body, timeout=30.0)
    request_elapsed = perf_counter() - request_started

    log.info(
        "LLM response: status=%s model=%s input_chars=%d output_cap=%s elapsed=%.3fs x_groq_region=%s cf_ray=%s",
        resp.status_code,
        provider.model,
        len(user_text),
        output_cap if output_cap is not None else "none",
        request_elapsed,
        resp.headers.get("x-groq-region") or "-",
        resp.headers.get("cf-ray") or "-",
    )

    resp.raise_for_status()

    data = resp.json()
    choices = data.get("choices") or [{}]
    finish_reason = choices[0].get("finish_reason")
    log.info(
        "LLM response detail: model=%s input_chars=%d output_cap=%s finish_reason=%s elapsed=%.3fs x_groq_region=%s cf_ray=%s",
        provider.model,
        len(user_text),
        output_cap if output_cap is not None else "none",
        finish_reason or "-",
        request_elapsed,
        resp.headers.get("x-groq-region") or "-",
        resp.headers.get("cf-ray") or "-",
    )

    # Treat any truncated completion as unusable; partial rewrites are worse than no rewrite.
    if finish_reason == "length":
        log.warning("LLM rewrite stopped at length; leaving text unchanged")
        return None

    content = choices[0].get("message", {}).get("content", "")
    return content.strip() if content else None


def _groq_completion_cap(user_text: str) -> int:
    estimated_input_tokens = max(1, len(user_text) // 4)
    cap = int(estimated_input_tokens * 1.5) + 32
    return max(128, min(1024, cap))


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
