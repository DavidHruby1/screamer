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
