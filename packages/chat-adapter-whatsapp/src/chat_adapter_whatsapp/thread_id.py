"""Thread ID encoding / decoding for the WhatsApp adapter.

Python port of upstream ``packages/adapter-whatsapp/src/index.ts`` (the
``encodeThreadId`` / ``decodeThreadId`` helpers).

WhatsApp conversations are always 1:1 between a business phone number and a
user. The thread ID format is::

    whatsapp:{phoneNumberId}:{userWaId}

There is no separate channel concept, so :func:`channel_id_from_thread_id`
returns the thread ID itself.
"""

from __future__ import annotations

from chat_adapter_shared import ValidationError

from .types import WhatsAppThreadId

_PREFIX = "whatsapp:"


def encode_thread_id(platform_data: WhatsAppThreadId) -> str:
    """Encode a :class:`WhatsAppThreadId` into the canonical thread ID string."""

    return f"{_PREFIX}{platform_data['phoneNumberId']}:{platform_data['userWaId']}"


def decode_thread_id(thread_id: str) -> WhatsAppThreadId:
    """Inverse of :func:`encode_thread_id`.

    Raises :class:`ValidationError` on malformed input or a non-``whatsapp:``
    prefix.
    """

    if not thread_id.startswith(_PREFIX):
        raise ValidationError("whatsapp", f"Invalid WhatsApp thread ID: {thread_id}")

    without_prefix = thread_id[len(_PREFIX) :]
    if not without_prefix:
        raise ValidationError("whatsapp", f"Invalid WhatsApp thread ID format: {thread_id}")

    parts = without_prefix.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValidationError("whatsapp", f"Invalid WhatsApp thread ID format: {thread_id}")

    return {"phoneNumberId": parts[0], "userWaId": parts[1]}


def channel_id_from_thread_id(thread_id: str) -> str:
    """Derive the channel ID from a WhatsApp thread ID.

    Every conversation is a 1:1 DM, so the channel ID *is* the thread ID.
    Validates the input by round-tripping through :func:`decode_thread_id`.
    """

    decode_thread_id(thread_id)
    return thread_id


__all__ = [
    "WhatsAppThreadId",
    "channel_id_from_thread_id",
    "decode_thread_id",
    "encode_thread_id",
]
