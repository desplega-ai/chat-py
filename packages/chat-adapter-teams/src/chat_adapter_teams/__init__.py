"""Microsoft Teams adapter for chat-py."""

from __future__ import annotations

from .adapter import (
    BOT_FRAMEWORK_ISSUER,
    BOT_FRAMEWORK_JWKS_URL,
    DEFAULT_TEAMS_API_URL,
    TeamsAdapter,
    TeamsAdapterConfig,
    TeamsAuthCertificate,
    TeamsAuthFederated,
    create_teams_adapter,
    verify_bearer_token,
)
from .cards import AUTO_SUBMIT_ACTION_ID, card_to_adaptive_card, card_to_fallback_text
from .errors import handle_teams_error
from .markdown import TeamsFormatConverter
from .thread_id import TeamsThreadId, decode_thread_id, encode_thread_id, is_dm

__version__ = "0.1.0"

__all__ = [
    "AUTO_SUBMIT_ACTION_ID",
    "BOT_FRAMEWORK_ISSUER",
    "BOT_FRAMEWORK_JWKS_URL",
    "DEFAULT_TEAMS_API_URL",
    "TeamsAdapter",
    "TeamsAdapterConfig",
    "TeamsAuthCertificate",
    "TeamsAuthFederated",
    "TeamsFormatConverter",
    "TeamsThreadId",
    "__version__",
    "card_to_adaptive_card",
    "card_to_fallback_text",
    "create_teams_adapter",
    "decode_thread_id",
    "encode_thread_id",
    "handle_teams_error",
    "is_dm",
    "verify_bearer_token",
]
