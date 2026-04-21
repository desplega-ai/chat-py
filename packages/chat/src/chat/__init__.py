"""Unified chat SDK for building bots across Slack, Teams, Google Chat, Discord, Telegram, GitHub, Linear, and WhatsApp — Python port of vercel/chat"""

from chat.errors import (
    ChatError,
    LockError,
    NotImplementedError,
    RateLimitError,
)

__version__ = "0.1.0"

__all__ = [
    "ChatError",
    "LockError",
    "NotImplementedError",
    "RateLimitError",
    "__version__",
]
