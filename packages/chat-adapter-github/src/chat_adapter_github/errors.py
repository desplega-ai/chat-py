"""GitHub error translation helpers.

Upstream ``packages/adapter-github/src/index.ts`` defers to Octokit's own
``RequestError`` subclasses. This module performs the equivalent translation
for :mod:`httpx` responses so ``GitHubAdapter`` can raise the typed adapter
errors defined in :mod:`chat_adapter_shared`.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any, NoReturn

import httpx
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
)


def _extract_retry_after(response: httpx.Response) -> int | None:
    """Return retry-after seconds (rounded up) or ``None`` if absent.

    GitHub sets ``Retry-After`` on secondary rate limits and
    ``X-RateLimit-Reset`` (epoch seconds) on primary rate limits.
    """

    header = response.headers.get("retry-after")
    if header is not None:
        try:
            return max(1, math.ceil(float(header)))
        except (TypeError, ValueError):
            pass

    reset = response.headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            reset_at = int(reset)
            now = int(datetime.now(UTC).timestamp())
            delta = reset_at - now
            if delta > 0:
                return delta
        except (TypeError, ValueError):
            pass

    return None


def _extract_body(response: httpx.Response) -> Any:
    """Best-effort JSON decode of the response body."""

    text = response.text or ""
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text


def _summarize_body(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        # GitHub error shape: {"message": "...", "documentation_url": "..."}
        message = body.get("message")
        if isinstance(message, str) and message:
            return message
    try:
        return json.dumps(body, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(body)


def handle_github_error(response: httpx.Response, operation: str) -> NoReturn:
    """Translate a failed GitHub REST response into a typed adapter error.

    * ``401`` → :class:`AuthenticationError`
    * ``403`` → :class:`AdapterRateLimitError` when the rate-limit header is
      zero (GitHub uses 403 for primary rate limits), else :class:`PermissionError`
    * ``404`` → :class:`ResourceNotFoundError`
    * ``429`` → :class:`AdapterRateLimitError`
    * Otherwise → :class:`NetworkError`
    """

    status = response.status_code
    body = _extract_body(response)
    summary = _summarize_body(body)

    if status == 401:
        detail = summary or "unauthorized"
        raise AuthenticationError(
            "github",
            f"Authentication failed for {operation}: {detail}",
        )

    if status == 403:
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining == "0":
            retry_after = _extract_retry_after(response)
            raise AdapterRateLimitError("github", retry_after)
        raise PermissionError("github", operation)

    if status == 404:
        raise ResourceNotFoundError("github", operation)

    if status == 429:
        retry_after = _extract_retry_after(response)
        raise AdapterRateLimitError("github", retry_after)

    message = f"GitHub API error during {operation}: {status} {summary}".rstrip()
    raise NetworkError("github", message)


__all__ = ["handle_github_error"]
