"""Telegram error translation helpers.

Python port of the private ``throwTelegramApiError`` helper in upstream
``packages/adapter-telegram/src/index.ts``. Translates Bot API error
envelopes into typed adapter errors from :mod:`chat_adapter_shared`.
"""

from __future__ import annotations

from typing import NoReturn

from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
)

from .types import TelegramApiResponse


def throw_telegram_api_error(
    method: str,
    status: int,
    data: TelegramApiResponse,
) -> NoReturn:
    """Raise a typed adapter error based on a failed Telegram Bot API response.

    Mirrors upstream 1:1:

    * 429 → :class:`AdapterRateLimitError` with ``retry_after`` from
      ``parameters.retry_after`` when present.
    * 401 → :class:`AuthenticationError` with the Bot API description.
    * 403 → :class:`PermissionError` scoped to the calling method.
    * 404 → :class:`ResourceNotFoundError` scoped to the calling method.
    * any other 4xx → :class:`ValidationError` with the description.
    * everything else → :class:`NetworkError` wrapping status + error code.
    """

    error_code = data.get("error_code") if "error_code" in data else status
    if not isinstance(error_code, int):
        error_code = status

    description = data.get("description") or f"Telegram API {method} failed"

    if error_code == 429:
        retry_after: int | None = None
        params = data.get("parameters")
        if isinstance(params, dict):
            candidate = params.get("retry_after")
            if isinstance(candidate, int):
                retry_after = candidate
        raise AdapterRateLimitError("telegram", retry_after)

    if error_code == 401:
        raise AuthenticationError("telegram", description)

    if error_code == 403:
        raise PermissionError("telegram", method)

    if error_code == 404:
        raise ResourceNotFoundError("telegram", method)

    if 400 <= error_code < 500:
        raise ValidationError("telegram", description)

    raise NetworkError(
        "telegram",
        f"{description} (status {status}, error {error_code})",
    )


__all__ = [
    "throw_telegram_api_error",
]
