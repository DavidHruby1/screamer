"""Shared synchronous HTTP transport for small sequential requests."""

from __future__ import annotations

import logging
import threading

import httpx

_client: httpx.Client | None = None
_lock = threading.Lock()

# httpx logs full request URLs at INFO; httpcore can include them at DEBUG.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _get_client() -> httpx.Client:
    global _client

    client = _client
    if client is not None:
        return client

    with _lock:
        client = _client
        if client is None:
            client = httpx.Client()
            _client = client
        return client


def post(
    url: str,
    *,
    headers: httpx.Headers,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    json: dict[str, object] | None = None,
    timeout: float | httpx.Timeout | None = None,
) -> httpx.Response:
    try:
        return _get_client().post(
            url, headers=headers, data=data, files=files, json=json, timeout=timeout
        )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise RuntimeError(f"HTTP request failed ({type(exc).__name__})") from None


def check_connection(base_url: str, headers: httpx.Headers, model: str = "") -> str:
    """GET ``{base_url}/models`` on an OpenAI-compatible server; return a short verdict.

    Raises ``RuntimeError`` with a URL-free message when the server is unreachable or
    rejects the request. A missing model is reported, not raised: some servers list
    only loaded models.
    """
    url = base_url.rstrip("/") + "/models"
    try:
        response = _get_client().get(url, headers=headers, timeout=httpx.Timeout(10.0))
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise RuntimeError(f"Could not connect ({type(exc).__name__})") from None
    if response.status_code in (401, 403):
        raise RuntimeError(f"The server rejected the API key (status {response.status_code})")
    raise_for_status(response)
    try:
        ids = {str(item.get("id")) for item in response.json().get("data", [])}
    except (ValueError, AttributeError, TypeError):
        return "Connected."
    if model and ids and model not in ids:
        return f"Connected, but the server does not list model {model!r}."
    return "Connected."


def raise_for_status(response: httpx.Response) -> None:
    """Report status without exposing the request URL embedded in httpx errors."""
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP request failed (status {response.status_code})")


def close() -> None:
    global _client

    with _lock:
        client = _client
        _client = None

    if client is not None:
        client.close()
