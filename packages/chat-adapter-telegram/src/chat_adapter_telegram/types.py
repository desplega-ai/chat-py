"""Telegram adapter types.

Python port of upstream ``packages/adapter-telegram/src/types.ts``. All
types mirror the Telegram Bot API payload shapes verbatim so JSON parsed
from webhooks / REST responses can flow through without further munging.

See https://core.telegram.org/bots/api for the canonical definitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from chat import Logger


class TelegramLongPollingConfig(TypedDict, total=False):
    """Telegram long-polling configuration.

    See https://core.telegram.org/bots/api#getupdates.
    """

    allowedUpdates: list[str]
    deleteWebhook: bool
    dropPendingUpdates: bool
    limit: int
    retryDelayMs: int
    timeout: int


class TelegramAdapterConfig(TypedDict, total=False):
    """Telegram adapter configuration."""

    apiBaseUrl: str
    apiUrl: str
    botToken: str
    logger: Logger
    longPolling: TelegramLongPollingConfig
    mode: TelegramAdapterMode
    secretToken: str
    userName: str


TelegramAdapterMode = Literal["auto", "webhook", "polling"]


class TelegramThreadId(TypedDict, total=False):
    """Decoded Telegram thread ID components."""

    chatId: str
    messageThreadId: int


class TelegramUser(TypedDict, total=False):
    """Telegram user object.

    See https://core.telegram.org/bots/api#user.
    """

    first_name: str
    id: int
    is_bot: bool
    language_code: str
    last_name: str
    username: str


class TelegramChat(TypedDict, total=False):
    """Telegram chat object.

    See https://core.telegram.org/bots/api#chat.
    """

    first_name: str
    id: int
    last_name: str
    title: str
    type: Literal["private", "group", "supergroup", "channel"]
    username: str


class TelegramMessageEntity(TypedDict, total=False):
    """Telegram message entity (mentions, links, commands, etc).

    See https://core.telegram.org/bots/api#messageentity.
    """

    language: str
    length: int
    offset: int
    type: str
    url: str
    user: TelegramUser


class TelegramFile(TypedDict, total=False):
    """Telegram file metadata."""

    file_id: str
    file_path: str
    file_size: int
    file_unique_id: str


class TelegramPhotoSize(TelegramFile, total=False):
    """Telegram photo size object."""

    height: int
    width: int


class TelegramAudio(TelegramFile, total=False):
    duration: int
    file_name: str
    mime_type: str
    performer: str
    title: str


class TelegramVideo(TelegramFile, total=False):
    file_name: str
    height: int
    mime_type: str
    width: int


class TelegramVoice(TelegramFile, total=False):
    duration: int
    mime_type: str


class TelegramDocument(TelegramFile, total=False):
    file_name: str
    mime_type: str


class TelegramSticker(TelegramFile, total=False):
    emoji: str


class TelegramMessage(TypedDict, total=False):
    """Telegram message.

    See https://core.telegram.org/bots/api#message.
    """

    audio: TelegramAudio
    caption: str
    caption_entities: list[TelegramMessageEntity]
    chat: TelegramChat
    date: int
    document: TelegramDocument
    edit_date: int
    entities: list[TelegramMessageEntity]
    from_: TelegramUser
    message_id: int
    message_thread_id: int
    photo: list[TelegramPhotoSize]
    sender_chat: TelegramChat
    sticker: TelegramSticker
    text: str
    video: TelegramVideo
    voice: TelegramVoice


class TelegramInlineKeyboardButton(TypedDict, total=False):
    """Telegram inline keyboard button.

    See https://core.telegram.org/bots/api#inlinekeyboardbutton.
    """

    callback_data: str
    text: str
    url: str


class TelegramInlineKeyboardMarkup(TypedDict):
    """Telegram inline keyboard markup.

    See https://core.telegram.org/bots/api#inlinekeyboardmarkup.
    """

    inline_keyboard: list[list[TelegramInlineKeyboardButton]]


class TelegramCallbackQuery(TypedDict, total=False):
    """Telegram callback query (inline keyboard button click).

    See https://core.telegram.org/bots/api#callbackquery.
    """

    chat_instance: str
    data: str
    from_: TelegramUser
    id: str
    inline_message_id: str
    message: TelegramMessage


class TelegramEmojiReaction(TypedDict):
    type: Literal["emoji"]
    emoji: str


class TelegramCustomEmojiReaction(TypedDict):
    type: Literal["custom_emoji"]
    custom_emoji_id: str


TelegramReactionType = TelegramEmojiReaction | TelegramCustomEmojiReaction


class TelegramMessageReactionUpdated(TypedDict, total=False):
    """Telegram message reaction update.

    See https://core.telegram.org/bots/api#messagereactionupdated.
    """

    actor_chat: TelegramChat
    chat: TelegramChat
    date: int
    message_id: int
    message_thread_id: int
    new_reaction: list[TelegramReactionType]
    old_reaction: list[TelegramReactionType]
    user: TelegramUser


class TelegramUpdate(TypedDict, total=False):
    """Telegram webhook update payload.

    See https://core.telegram.org/bots/api#update.
    """

    callback_query: TelegramCallbackQuery
    channel_post: TelegramMessage
    edited_channel_post: TelegramMessage
    edited_message: TelegramMessage
    message: TelegramMessage
    message_reaction: TelegramMessageReactionUpdated
    update_id: int


class TelegramApiResponseParameters(TypedDict, total=False):
    retry_after: int


class TelegramApiResponse(TypedDict, total=False):
    """Telegram API response envelope."""

    description: str
    error_code: int
    ok: bool
    parameters: TelegramApiResponseParameters
    result: NotRequired[object]


class TelegramWebhookInfo(TypedDict, total=False):
    """Telegram webhook info response.

    See https://core.telegram.org/bots/api#getwebhookinfo.
    """

    allowed_updates: list[str]
    has_custom_certificate: bool
    ip_address: str
    last_error_date: int
    last_error_message: str
    max_connections: int
    pending_update_count: int
    url: str


TelegramRawMessage = TelegramMessage
"""Alias matching upstream — kept so downstream code can annotate against
``TelegramRawMessage`` even though it's just :class:`TelegramMessage`."""


__all__ = [
    "TelegramAdapterConfig",
    "TelegramAdapterMode",
    "TelegramApiResponse",
    "TelegramApiResponseParameters",
    "TelegramAudio",
    "TelegramCallbackQuery",
    "TelegramChat",
    "TelegramCustomEmojiReaction",
    "TelegramDocument",
    "TelegramEmojiReaction",
    "TelegramFile",
    "TelegramInlineKeyboardButton",
    "TelegramInlineKeyboardMarkup",
    "TelegramLongPollingConfig",
    "TelegramMessage",
    "TelegramMessageEntity",
    "TelegramMessageReactionUpdated",
    "TelegramPhotoSize",
    "TelegramRawMessage",
    "TelegramReactionType",
    "TelegramSticker",
    "TelegramThreadId",
    "TelegramUpdate",
    "TelegramUser",
    "TelegramVideo",
    "TelegramVoice",
    "TelegramWebhookInfo",
]
