"""Thread ID encoding/decoding for the Telegram adapter.

Python port of upstream ``packages/adapter-telegram/src/index.ts`` (the
``encodeThreadId`` / ``decodeThreadId`` helpers).

Thread ID format:

* Chat-only: ``telegram:{chat_id}``
* With forum topic: ``telegram:{chat_id}:{message_thread_id}``
"""

from __future__ import annotations

from chat_adapter_shared import ValidationError

from .types import TelegramThreadId


def encode_thread_id(platform_data: TelegramThreadId) -> str:
    """Encode a :class:`TelegramThreadId` into the canonical thread ID string."""

    chat_id = platform_data["chatId"]
    message_thread_id = platform_data.get("messageThreadId")
    if isinstance(message_thread_id, int):
        return f"telegram:{chat_id}:{message_thread_id}"
    return f"telegram:{chat_id}"


def decode_thread_id(thread_id: str) -> TelegramThreadId:
    """Inverse of :func:`encode_thread_id`.

    Raises :class:`ValidationError` on malformed input or a non-``telegram:``
    prefix.
    """

    parts = thread_id.split(":")
    if parts[0] != "telegram" or not (2 <= len(parts) <= 3):
        raise ValidationError("telegram", f"Invalid Telegram thread ID: {thread_id}")

    chat_id = parts[1]
    if not chat_id:
        raise ValidationError("telegram", f"Invalid Telegram thread ID: {thread_id}")

    if len(parts) == 2:
        return {"chatId": chat_id}

    message_thread_part = parts[2]
    if not message_thread_part:
        return {"chatId": chat_id}

    try:
        message_thread_id = int(message_thread_part)
    except ValueError as err:
        raise ValidationError(
            "telegram",
            f"Invalid Telegram thread topic ID in thread ID: {thread_id}",
        ) from err

    return {"chatId": chat_id, "messageThreadId": message_thread_id}


def channel_id_from_thread_id(thread_id: str) -> str:
    """Derive the channel ID (``telegram:{chat_id}``) from a thread ID."""

    decoded = decode_thread_id(thread_id)
    return f"telegram:{decoded['chatId']}"


__all__ = [
    "TelegramThreadId",
    "channel_id_from_thread_id",
    "decode_thread_id",
    "encode_thread_id",
]
