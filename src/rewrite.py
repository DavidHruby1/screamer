"""LLM rewrite via httpx: primary + fallback providers, prompt template with system prompt."""

from __future__ import annotations

import json
import logging

import httpx

from src.utils import AppError, PipelineResult, ScreamerError

log = logging.getLogger(__name__)


def rewrite(text: str, config: object) -> PipelineResult:
    """Send text to LLM with system prompt. Primary → fallback.

    Returns input text unchanged in ``PipelineResult.text`` if ``config.llm_enabled`` is False.
    Error → ``ScreamerError(AppError.LLM_FAILED)``.
    """
    if not config.llm_enabled:
        return PipelineResult(text=text)

    system_prompt = config.llm_system_prompt or ""
    language = getattr(config, "stt_language", "")
    if language:
        system_prompt += f"\nThe speech language is {language}."

    # Try primary LLM.
    try:
        result = _call_llm(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            model=config.llm_model,
            custom_headers=config.llm_custom_headers,
            system_prompt=system_prompt,
            user_text=text,
        )
        if result:
            log.debug("LLM rewrite: %r → %r", text[:60], result[:60])
            return PipelineResult(text=result)
    except Exception as e:
        log.warning("Primary LLM failed: %s", e)
        if not config.llm_fallback_enabled:
            return PipelineResult(text=text, warnings=[AppError.LLM_FAILED])

    # Try fallback LLM.
    if config.llm_fallback_enabled and config.llm_fallback_api_key:
        try:
            result = _call_llm(
                api_key=config.llm_fallback_api_key,
                base_url=config.llm_fallback_base_url,
                model=config.llm_fallback_model,
                custom_headers=config.llm_fallback_custom_headers,
                system_prompt=system_prompt,
                user_text=text,
            )
            if result:
                log.debug("LLM fallback rewrite: %r → %r", text[:60], result[:60])
                return PipelineResult(text=result)
        except Exception as e:
            log.warning("Fallback LLM failed: %s", e)

    log.debug("LLM rewrite failed or returned empty; using original text")
    return PipelineResult(text=text, warnings=[AppError.LLM_FAILED])


def _call_llm(
    api_key: str,
    base_url: str,
    model: str,
    custom_headers: str,
    system_prompt: str,
    user_text: str,
) -> str | None:
    """Call an OpenAI-compatible chat completions endpoint. Returns corrected text or None."""
    if not api_key:
        return None

    url = (base_url.rstrip("/") if base_url else "") + "/chat/completions"

    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if custom_headers:
        try:
            headers.update(json.loads(custom_headers))
        except json.JSONDecodeError:
            log.warning("Invalid custom headers JSON; ignoring")

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.3,
    }

    log.info("LLM request: url=%s model=%s", url, model)
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
