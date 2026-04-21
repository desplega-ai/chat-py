"""Slack adapter for chat-py.

Python port of ``@chat-adapter/slack``. Re-exports the public surface from the
submodules so callers can ``from chat_adapter_slack import create_slack_adapter``
without having to know the internal layout.
"""

from __future__ import annotations

from .adapter import (
    OPTIONS_LOAD_TIMEOUT_MS,
    SlackAdapter,
    SlackAdapterConfig,
    SlackAdapterMode,
    SlackEvent,
    SlackInstallation,
    SlackOAuthCallbackOptions,
    SlackReactionEvent,
    SlackThreadId,
    channel_id_from_thread_id,
    create_slack_adapter,
    decode_thread_id,
    encode_thread_id,
    is_dm_thread_id,
    parse_slack_message_url,
    verify_signature,
)
from .cards import (
    SlackBlock,
    card_to_block_kit,
    card_to_fallback_text,
    convert_fields_to_block,
    convert_text_to_block,
)
from .crypto import (
    EncryptedTokenData,
    decode_key,
    decrypt_token,
    encrypt_token,
)
from .markdown import SlackFormatConverter, SlackMarkdownConverter
from .modals import (
    ModalMetadata,
    SlackModalResponse,
    SlackOptionObject,
    SlackView,
    decode_modal_metadata,
    encode_modal_metadata,
    modal_to_slack_view,
    select_option_to_slack_option,
)

__version__ = "0.1.0"

__all__ = [
    "OPTIONS_LOAD_TIMEOUT_MS",
    "EncryptedTokenData",
    "ModalMetadata",
    "SlackAdapter",
    "SlackAdapterConfig",
    "SlackAdapterMode",
    "SlackBlock",
    "SlackEvent",
    "SlackFormatConverter",
    "SlackInstallation",
    "SlackMarkdownConverter",
    "SlackModalResponse",
    "SlackOAuthCallbackOptions",
    "SlackOptionObject",
    "SlackReactionEvent",
    "SlackThreadId",
    "SlackView",
    "__version__",
    "card_to_block_kit",
    "card_to_fallback_text",
    "channel_id_from_thread_id",
    "convert_fields_to_block",
    "convert_text_to_block",
    "create_slack_adapter",
    "decode_key",
    "decode_modal_metadata",
    "decode_thread_id",
    "decrypt_token",
    "encode_modal_metadata",
    "encode_thread_id",
    "encrypt_token",
    "is_dm_thread_id",
    "modal_to_slack_view",
    "parse_slack_message_url",
    "select_option_to_slack_option",
    "verify_signature",
]
