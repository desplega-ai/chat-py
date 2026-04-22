"""Discord adapter for chat-py.

Python port of upstream ``packages/adapter-discord``. Exposes
:class:`DiscordAdapter` and a set of helpers for thread-id encoding, webhook
signature verification, card translation, and Discord-flavoured markdown.
"""

from __future__ import annotations

from chat_adapter_discord.adapter import (
    DISCORD_API_BASE,
    DISCORD_MAX_CONTENT_LENGTH,
    DiscordAdapter,
    DiscordAdapterConfig,
    DiscordSlashCommandContext,
    create_discord_adapter,
    parse_slash_command,
    verify_discord_signature,
)
from chat_adapter_discord.cards import (
    BUTTON_STYLE_DANGER,
    BUTTON_STYLE_LINK,
    BUTTON_STYLE_PRIMARY,
    BUTTON_STYLE_SECONDARY,
    BUTTON_STYLE_SUCCESS,
    card_to_discord_payload,
    card_to_fallback_text,
)
from chat_adapter_discord.errors import handle_discord_error
from chat_adapter_discord.markdown import DiscordFormatConverter
from chat_adapter_discord.thread_id import (
    DiscordThreadId,
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
    is_dm,
)

__version__ = "0.1.0"

__all__ = [
    "BUTTON_STYLE_DANGER",
    "BUTTON_STYLE_LINK",
    "BUTTON_STYLE_PRIMARY",
    "BUTTON_STYLE_SECONDARY",
    "BUTTON_STYLE_SUCCESS",
    "DISCORD_API_BASE",
    "DISCORD_MAX_CONTENT_LENGTH",
    "DiscordAdapter",
    "DiscordAdapterConfig",
    "DiscordFormatConverter",
    "DiscordSlashCommandContext",
    "DiscordThreadId",
    "card_to_discord_payload",
    "card_to_fallback_text",
    "channel_id_from_thread_id",
    "create_discord_adapter",
    "decode_thread_id",
    "encode_thread_id",
    "handle_discord_error",
    "is_dm",
    "parse_slash_command",
    "verify_discord_signature",
]
