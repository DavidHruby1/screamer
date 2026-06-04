"""Shared synchronous HTTP transport for small sequential requests."""

from __future__ import annotations

import threading

import httpx

_client: httpx.Client | None = None
_lock = threading.Lock()


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
    headers: dict[str, str],
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    json: dict[str, object] | None = None,
    timeout: float | httpx.Timeout | None = None,
) -> httpx.Response:
    return _get_client().post(url, headers=headers, data=data, files=files, json=json, timeout=timeout)


def close() -> None:
    global _client

    with _lock:
        client = _client
        _client = None

    if client is not None:
        client.close()
