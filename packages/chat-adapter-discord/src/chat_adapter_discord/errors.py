"""Discord error translation helpers.

Upstream ``packages/adapter-discord/src/index.ts`` throws ``NetworkError``
inline with a ``${status} ${body}`` message. This module centralizes that
translation and promotes recognized status codes to their typed counterparts
from ``chat_adapter_shared``.
"""

from __future__ import annotations

import json
import math
from typing import Any, NoReturn

import httpx
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
)


def _extract_retry_after(response: httpx.Response, body: Any) -> int | None:
    """Return retry-after in seconds (rounded up) or ``None`` if absent."""

    # Discord returns ``X-RateLimit-Reset-After`` (float seconds). Fall back to
    # the standard ``Retry-After`` header and finally the JSON body's
    # ``retry_after`` field.
    header = response.headers.get("x-ratelimit-reset-after") or response.headers.get("retry-after")
    if header is not None:
        try:
            return max(1, math.ceil(float(header)))
        except (TypeError, ValueError):
            pass

    if isinstance(body, dict):
        raw = body.get("retry_after")
        if isinstance(raw, (int, float)):
            return max(1, math.ceil(float(raw)))

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
    try:
        return json.dumps(body, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(body)


def handle_discord_error(response: httpx.Response, operation: str) -> NoReturn:
    """Translate a failed Discord REST response into a typed adapter error.

    Mirrors upstream ``throw new NetworkError("discord", ...)`` for unknown
    statuses while promoting recognized codes to their typed counterparts.
    """

    status = response.status_code
    body = _extract_body(response)
    summary = _summarize_body(body)

    if status == 401:
        detail = summary or "unauthorized"
        raise AuthenticationError(
            "discord",
            f"Authentication failed for {operation}: {detail}",
        )

    if status == 403:
        raise PermissionError("discord", operation)

    if status == 404:
        raise ResourceNotFoundError("discord", operation)

    if status == 429:
        retry_after = _extract_retry_after(response, body)
        raise AdapterRateLimitError("discord", retry_after)

    message = f"Discord API error during {operation}: {status} {summary}".rstrip()
    raise NetworkError("discord", message)


__all__ = ["handle_discord_error"]
