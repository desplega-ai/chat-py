"""Teams error translation helpers.

Python port of upstream ``packages/adapter-teams/src/errors.ts``.

Translates Microsoft Bot Framework / Graph / HTTP errors into the shared
``chat_adapter_shared`` exception hierarchy.
"""

from __future__ import annotations

from typing import Any, NoReturn

from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
)


def _extract_status(err: Any) -> int | None:
    """Pull an HTTP status code out of a Teams-shaped error object."""

    # Prefer innerHttpError.statusCode (matches the TeamsSDK HttpError shape).
    inner = _attr(err, "innerHttpError")
    if inner is not None:
        status = _attr(inner, "statusCode")
        if isinstance(status, int):
            return status

    for key in ("statusCode", "status", "code"):
        value = _attr(err, key)
        if isinstance(value, int):
            return value
    return None


def _attr(obj: Any, key: str) -> Any:
    """Read ``key`` from a dict or fall back to attribute access."""

    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def handle_teams_error(error: Any, operation: str) -> NoReturn:
    """Translate a raw Teams error into a typed adapter error and re-raise.

    Mirrors upstream ``handleTeamsError(error, operation): never``.
    """

    if error is not None and (isinstance(error, dict) or hasattr(error, "__dict__")):
        message = _attr(error, "message")
        status_code = _extract_status(error)
        original = error if isinstance(error, BaseException) else None

        if status_code == 401:
            detail = message or "unauthorized"
            raise AuthenticationError(
                "teams",
                f"Authentication failed for {operation}: {detail}",
            )

        permission_hint = isinstance(message, str) and "permission" in message.lower()
        if status_code == 403 or permission_hint:
            raise PermissionError("teams", operation)

        if status_code == 404:
            raise NetworkError(
                "teams",
                f"Resource not found during {operation}: conversation or message may no longer exist",
                original,
            )

        if status_code == 429:
            retry_after_raw = _attr(error, "retryAfter")
            retry_after = retry_after_raw if isinstance(retry_after_raw, int) else None
            raise AdapterRateLimitError("teams", retry_after)

        if isinstance(message, str) and message:
            raise NetworkError(
                "teams",
                f"Teams API error during {operation}: {message}",
                original,
            )

    original = error if isinstance(error, BaseException) else None
    raise NetworkError(
        "teams",
        f"Teams API error during {operation}: {error!s}",
        original,
    )


__all__ = ["handle_teams_error"]
