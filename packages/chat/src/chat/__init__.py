"""Unified chat SDK for building bots across Slack, Teams, Google Chat, Discord, Telegram, GitHub, Linear, and WhatsApp — Python port of vercel/chat"""

from chat.emoji import (
    DEFAULT_EMOJI_MAP,
    EmojiResolver,
    EmojiValueImpl,
    convert_emoji_placeholders,
    create_emoji,
    default_emoji_resolver,
    emoji,
    get_emoji,
)
from chat.errors import (
    ChatError,
    LockError,
    NotImplementedError,
    RateLimitError,
)
from chat.logger import ConsoleLogger, Logger, LogLevel
from chat.postable_object import (
    POSTABLE_OBJECT,
    PostableObject,
    PostableObjectContext,
    is_postable_object,
    post_postable_object,
)
from chat.types import (
    THREAD_STATE_TTL_MS,
    ChannelVisibility,
    EmojiValue,
    WellKnownEmoji,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_EMOJI_MAP",
    "POSTABLE_OBJECT",
    "THREAD_STATE_TTL_MS",
    "ChannelVisibility",
    "ChatError",
    "ConsoleLogger",
    "EmojiResolver",
    "EmojiValue",
    "EmojiValueImpl",
    "LockError",
    "LogLevel",
    "Logger",
    "NotImplementedError",
    "PostableObject",
    "PostableObjectContext",
    "RateLimitError",
    "WellKnownEmoji",
    "__version__",
    "convert_emoji_placeholders",
    "create_emoji",
    "default_emoji_resolver",
    "emoji",
    "get_emoji",
    "is_postable_object",
    "post_postable_object",
]
