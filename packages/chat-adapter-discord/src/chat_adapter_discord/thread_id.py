"""Thread ID encoding/decoding for the Discord adapter.

Python port of upstream ``packages/adapter-discord/src/index.ts`` (the
``encodeThreadId`` / ``decodeThreadId`` / ``channelIdFromThreadId`` / ``isDM``
helpers).

Thread ID format: ``discord:{guildId}:{channelId}[:{threadId}]``.

Discord IDs are snowflake integers, so no base64 encoding is needed. ``guildId``
is set to ``"@me"`` for DM conversations.
"""

from __future__ import annotations

from typing import TypedDict

from chat_adapter_shared import ValidationError


class DiscordThreadId(TypedDict, total=False):
    """Decoded Discord thread ID data."""

    guildId: str
    channelId: str
    threadId: str


def encode_thread_id(platform_data: DiscordThreadId) -> str:
    """Build the canonical Discord thread ID string."""

    guild_id = platform_data["guildId"]
    channel_id = platform_data["channelId"]
    thread_id = platform_data.get("threadId")
    thread_part = f":{thread_id}" if thread_id else ""
    return f"discord:{guild_id}:{channel_id}{thread_part}"


def decode_thread_id(thread_id: str) -> DiscordThreadId:
    """Inverse of :func:`encode_thread_id`.

    Raises :class:`ValidationError` on malformed input.
    """

    parts = thread_id.split(":")
    if len(parts) < 3 or parts[0] != "discord":
        raise ValidationError("discord", f"Invalid Discord thread ID: {thread_id}")

    decoded: DiscordThreadId = {
        "guildId": parts[1],
        "channelId": parts[2],
    }
    if len(parts) >= 4 and parts[3]:
        decoded["threadId"] = parts[3]
    return decoded


def is_dm(thread_id: str) -> bool:
    """Return ``True`` when the encoded conversation is a DM (``guildId == '@me'``)."""

    return decode_thread_id(thread_id)["guildId"] == "@me"


def channel_id_from_thread_id(thread_id: str) -> str:
    """Derive the channel ID from a Discord thread ID.

    ``discord:{guildId}:{channelId}:{threadId}`` →
    ``discord:{guildId}:{channelId}``. If already a channel ID (3 parts), returns
    the input unchanged.
    """

    parts = thread_id.split(":")
    return ":".join(parts[:3])


__all__ = [
    "DiscordThreadId",
    "channel_id_from_thread_id",
    "decode_thread_id",
    "encode_thread_id",
    "is_dm",
]
