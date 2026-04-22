"""Thread ID encoding/decoding for the Microsoft Teams adapter.

Python port of upstream ``packages/adapter-teams/src/thread-id.ts``.

Thread ID format: ``teams:{b64url(conversationId)}:{b64url(serviceUrl)}``.

Both the Teams conversation ID and service URL contain characters that collide
with the ``:`` delimiter, so each segment is base64url-encoded. The
``conversationId`` retains its ``;messageid=N`` suffix for thread replies — it
is the caller's job (via :func:`strip_message_id`) to drop that when routing by
channel and preserve it when posting a reply to a specific message.
"""

from __future__ import annotations

import base64
from typing import TypedDict

from chat_adapter_shared import ValidationError


class TeamsThreadId(TypedDict, total=False):
    """Decoded Microsoft Teams thread ID data."""

    conversationId: str
    replyToId: str
    serviceUrl: str


def _b64url_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def encode_thread_id(platform_data: TeamsThreadId) -> str:
    """Build the canonical Teams thread ID string."""

    conversation_id = platform_data["conversationId"]
    service_url = platform_data["serviceUrl"]
    return f"teams:{_b64url_encode(conversation_id)}:{_b64url_encode(service_url)}"


def decode_thread_id(thread_id: str) -> TeamsThreadId:
    """Inverse of :func:`encode_thread_id`.

    Raises :class:`ValidationError` on malformed input.
    """

    parts = thread_id.split(":")
    if len(parts) != 3 or parts[0] != "teams":
        raise ValidationError("teams", f"Invalid Teams thread ID: {thread_id}")

    conversation_id = _b64url_decode(parts[1])
    service_url = _b64url_decode(parts[2])
    return {"conversationId": conversation_id, "serviceUrl": service_url}


def is_dm(thread_id: str) -> bool:
    """Return ``True`` when the encoded conversation is a DM (not a ``19:`` group)."""

    conversation_id = decode_thread_id(thread_id)["conversationId"]
    return not conversation_id.startswith("19:")


__all__ = [
    "TeamsThreadId",
    "decode_thread_id",
    "encode_thread_id",
    "is_dm",
]
