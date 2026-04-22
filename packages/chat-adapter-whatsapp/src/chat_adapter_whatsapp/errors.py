"""WhatsApp / Meta Graph API error translation helpers.

The WhatsApp Cloud API returns errors using the standard Meta Graph API
envelope::

    {"error": {"message": "...", "code": 100, "type": "OAuthException", ...}}

This module maps common HTTP status / error code combinations to the typed
adapter errors from :mod:`chat_adapter_shared`.

See https://developers.facebook.com/docs/graph-api/guides/error-handling and
https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes.
"""

from __future__ import annotations

from typing import Any, NoReturn

from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
)

from .types import WhatsAppApiErrorEnvelope


def _extract_error_body(data: WhatsAppApiErrorEnvelope | dict[str, Any]) -> dict[str, Any]:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return error
    return {}


def throw_whatsapp_api_error(
    method: str,
    status: int,
    data: WhatsAppApiErrorEnvelope | dict[str, Any],
) -> NoReturn:
    """Raise a typed adapter error based on a failed WhatsApp Cloud API response.

    Mapping mirrors upstream ``packages/adapter-whatsapp/src/index.ts`` plus
    the Meta Graph API conventions:

    * 429 → :class:`AdapterRateLimitError` — Meta does not return a
      ``retry_after`` for WhatsApp errors, so the value is left as ``None``.
    * 401 → :class:`AuthenticationError` (token invalid / expired).
    * 403 → :class:`PermissionError` scoped to the calling method.
    * 404 → :class:`ResourceNotFoundError` scoped to the calling method.
    * any other 4xx → :class:`ValidationError` with the Meta error message.
    * 5xx (or any other status) → :class:`NetworkError` wrapping the message
      together with the HTTP status and the Meta error ``code`` when present.
    """

    error_body = _extract_error_body(data)
    raw_code = error_body.get("code")
    error_code = raw_code if isinstance(raw_code, int) else status
    description = (
        error_body.get("message")
        or error_body.get("error_user_msg")
        or f"WhatsApp API {method} failed"
    )

    if status == 429 or error_code == 429:
        raise AdapterRateLimitError("whatsapp", None)

    if status == 401 or error_code in (190, 102):
        raise AuthenticationError("whatsapp", description)

    if status == 403 or error_code == 10:
        raise PermissionError("whatsapp", method)

    if status == 404:
        raise ResourceNotFoundError("whatsapp", method)

    if 400 <= status < 500:
        raise ValidationError("whatsapp", description)

    raise NetworkError(
        "whatsapp",
        f"{description} (status {status}, error {error_code})",
    )


__all__ = [
    "throw_whatsapp_api_error",
]
