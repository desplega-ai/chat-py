"""Telegram adapter for chat-py.

Python port of upstream ``packages/adapter-telegram``. Exposes
:class:`TelegramAdapter` and helpers for thread-id encoding, webhook secret
verification, card-to-inline-keyboard translation, and a Telegram
MarkdownV2-flavoured format converter.
"""

from __future__ import annotations

from chat_adapter_telegram.adapter import (
    TELEGRAM_API_BASE,
    TELEGRAM_SECRET_TOKEN_HEADER,
    TelegramAdapter,
    TelegramRuntimeMode,
    apply_telegram_entities,
    create_telegram_adapter,
)
from chat_adapter_telegram.cards import (
    card_to_telegram_inline_keyboard,
    decode_telegram_callback_data,
    empty_telegram_inline_keyboard,
    encode_telegram_callback_data,
)
from chat_adapter_telegram.errors import throw_telegram_api_error
from chat_adapter_telegram.markdown import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    TelegramFormatConverter,
    TelegramParseMode,
    ends_with_orphan_backslash,
    escape_markdown_v2,
    find_unescaped_positions,
    to_bot_api_parse_mode,
    truncate_for_telegram,
)
from chat_adapter_telegram.thread_id import (
    TelegramThreadId,
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
)
from chat_adapter_telegram.types import (
    TelegramAdapterConfig,
    TelegramAdapterMode,
    TelegramApiResponse,
    TelegramCallbackQuery,
    TelegramChat,
    TelegramFile,
    TelegramInlineKeyboardButton,
    TelegramInlineKeyboardMarkup,
    TelegramLongPollingConfig,
    TelegramMessage,
    TelegramMessageEntity,
    TelegramMessageReactionUpdated,
    TelegramRawMessage,
    TelegramReactionType,
    TelegramUpdate,
    TelegramUser,
    TelegramWebhookInfo,
)

__version__ = "0.1.0"

__all__ = [
    "TELEGRAM_API_BASE",
    "TELEGRAM_CAPTION_LIMIT",
    "TELEGRAM_MESSAGE_LIMIT",
    "TELEGRAM_SECRET_TOKEN_HEADER",
    "TelegramAdapter",
    "TelegramAdapterConfig",
    "TelegramAdapterMode",
    "TelegramApiResponse",
    "TelegramCallbackQuery",
    "TelegramChat",
    "TelegramFile",
    "TelegramFormatConverter",
    "TelegramInlineKeyboardButton",
    "TelegramInlineKeyboardMarkup",
    "TelegramLongPollingConfig",
    "TelegramMessage",
    "TelegramMessageEntity",
    "TelegramMessageReactionUpdated",
    "TelegramParseMode",
    "TelegramRawMessage",
    "TelegramReactionType",
    "TelegramRuntimeMode",
    "TelegramThreadId",
    "TelegramUpdate",
    "TelegramUser",
    "TelegramWebhookInfo",
    "__version__",
    "apply_telegram_entities",
    "card_to_telegram_inline_keyboard",
    "channel_id_from_thread_id",
    "create_telegram_adapter",
    "decode_telegram_callback_data",
    "decode_thread_id",
    "empty_telegram_inline_keyboard",
    "encode_telegram_callback_data",
    "encode_thread_id",
    "ends_with_orphan_backslash",
    "escape_markdown_v2",
    "find_unescaped_positions",
    "throw_telegram_api_error",
    "to_bot_api_parse_mode",
    "truncate_for_telegram",
]
