"""Telegram adapter for chat-py.

Python port of upstream ``packages/adapter-telegram``. Exposes
:class:`TelegramAdapter` and helpers for thread-id encoding, webhook secret
verification, card-to-inline-keyboard translation, and a Telegram
MarkdownV2-flavoured format converter.
"""

from __future__ import annotations

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
    "TelegramAdapterConfig",
    "TelegramAdapterMode",
    "TelegramApiResponse",
    "TelegramCallbackQuery",
    "TelegramChat",
    "TelegramFile",
    "TelegramInlineKeyboardButton",
    "TelegramInlineKeyboardMarkup",
    "TelegramLongPollingConfig",
    "TelegramMessage",
    "TelegramMessageEntity",
    "TelegramMessageReactionUpdated",
    "TelegramRawMessage",
    "TelegramReactionType",
    "TelegramThreadId",
    "TelegramUpdate",
    "TelegramUser",
    "TelegramWebhookInfo",
    "__version__",
    "channel_id_from_thread_id",
    "decode_thread_id",
    "encode_thread_id",
]
