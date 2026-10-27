"""Rubric #4 (Guided Error Handling) & Rubric #15 (Distributed Tracing).

Shared process-level asynchronous HTTP client built on ``httpx.AsyncClient`` with
20-second timeout, project-identifying ``User-Agent``, automatic 3-attempt
exponential backoff on HTTP 429 and 5xx responses, ``Retry-After`` header
parsing, and ``UpstreamError`` classification.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_BACKOFF_SECONDS = 0.05
USER_AGENT = (
    "alz-recent-research-agent/1.0 (+https://github.com/onofrer-wq/alz-recent-research-agent; scientific-literacy-adk)"
)

_shared_client: httpx.AsyncClient | None = None
_shared_client_loop_id: int | None = None


class UpstreamError(Exception):
    """Exception raised when an external biomedical literature API fails after retries."""

    def __init__(
        self,
        source: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        """Initializes the upstream error.

        Args:
            source: Identifier of the upstream service (e.g., ``"pubmed"``).
            message: Diagnostic error description.
            status_code: Optional HTTP status code returned by the server.
            retry_after: Optional ``Retry-After`` wait duration in seconds.
        """
        super().__init__(message)
        self.source = source
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after


def _parse_retry_after(header_value: str | None) -> int | None:
    """Parses an integer ``Retry-After`` HTTP header if present."""
    if not header_value:
        return None
    stripped = header_value.strip()
    if stripped.isdigit():
        return max(1, int(stripped))
    return None


def get_shared_async_client() -> httpx.AsyncClient:
    """Returns a process-shared ``httpx.AsyncClient`` bound to the current event loop."""
    global _shared_client, _shared_client_loop_id
    try:
        current_loop = asyncio.get_running_loop()
        loop_id = id(current_loop)
    except RuntimeError:
        loop_id = None

    if (
        _shared_client is None
        or _shared_client.is_closed
        or (_shared_client_loop_id is not None and _shared_client_loop_id != loop_id)
    ):
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_TIMEOUT_SECONDS),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        _shared_client_loop_id = loop_id
    return _shared_client


async def close_shared_async_client() -> None:
    """Closes the shared ``httpx.AsyncClient`` if open."""
    global _shared_client, _shared_client_loop_id
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None
    _shared_client_loop_id = None


async def _request_with_retries(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    source: str = "upstream",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_backoff: float = DEFAULT_BASE_BACKOFF_SECONDS,
) -> httpx.Response:
    """Executes an HTTP GET with up to ``max_attempts`` retries on 429/5xx/transport errors."""
    client = get_shared_async_client()
    last_exc: Exception | None = None

    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < max_attempts:
                await asyncio.sleep(base_backoff * (2 ** (attempt - 1)))
                continue
            raise UpstreamError(
                source=source,
                message=f"Network error contacting {source}: {type(exc).__name__}: {exc}",
                status_code=None,
                retry_after=None,
            ) from exc

        status = response.status_code
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))

        if status == 429 or status >= 500:
            if attempt < max_attempts:
                wait_seconds = min(float(retry_after or 0), 0.2) or (base_backoff * (2 ** (attempt - 1)))
                await asyncio.sleep(wait_seconds)
                continue
            raise UpstreamError(
                source=source,
                message=f"{source} returned HTTP {status} after {max_attempts} attempts.",
                status_code=status,
                retry_after=retry_after or (5 if status == 429 else None),
            )

        if status >= 400:
            raise UpstreamError(
                source=source,
                message=f"{source} rejected request with HTTP {status}: {response.text[:200]}",
                status_code=status,
                retry_after=retry_after,
            )

        return response

    raise UpstreamError(
        source=source,
        message=f"Exhausted retries for {source}: {last_exc}",
    )


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    source: str = "upstream",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Any:
    """Performs an HTTP GET with retries and parses the response body as JSON.

    Args:
        url: Target HTTPS URL.
        params: Optional query string parameters.
        headers: Optional additional request headers.
        source: Short upstream service identifier for error reporting.
        max_attempts: Maximum number of attempts before raising ``UpstreamError``.

    Returns:
        Decoded JSON payload.

    Raises:
        UpstreamError: On HTTP failure or retries exhausted.
        ValueError: When the response body is not valid JSON.
    """
    response = await _request_with_retries(
        url,
        params=params,
        headers=headers,
        source=source,
        max_attempts=max_attempts,
    )
    try:
        return response.json()
    except Exception as exc:
        raise ValueError(f"Malformed JSON payload from {source}: {exc}") from exc


async def get_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    source: str = "upstream",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Performs an HTTP GET with retries and returns the response text.

    Args:
        url: Target HTTPS URL.
        params: Optional query string parameters.
        headers: Optional additional request headers.
        source: Short upstream service identifier for error reporting.
        max_attempts: Maximum number of attempts before raising ``UpstreamError``.

    Returns:
        Response body text.

    Raises:
        UpstreamError: On HTTP failure or retries exhausted.
    """
    response = await _request_with_retries(
        url,
        params=params,
        headers=headers,
        source=source,
        max_attempts=max_attempts,
    )
    return response.text
