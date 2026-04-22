"""Linear adapter for chat-py.

Python port of upstream ``packages/adapter-linear``. Exposes
:class:`LinearAdapter` and helpers for thread-id encoding, webhook signature
verification, card-to-markdown translation, OAuth handling, and a
Linear-flavoured format converter.
"""

from __future__ import annotations

from chat_adapter_linear.adapter import (
    LINEAR_API_URL,
    LINEAR_OAUTH_TOKEN_URL,
    LinearAdapter,
    create_linear_adapter,
    verify_linear_signature,
)
from chat_adapter_linear.cards import card_to_linear_markdown, card_to_plain_text
from chat_adapter_linear.errors import handle_linear_error, handle_linear_graphql_body
from chat_adapter_linear.markdown import LinearFormatConverter
from chat_adapter_linear.thread_id import (
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
)
from chat_adapter_linear.types import (
    LinearActorData,
    LinearAdapterAPIKeyConfig,
    LinearAdapterBaseConfig,
    LinearAdapterClientCredentialsConfig,
    LinearAdapterConfig,
    LinearAdapterMode,
    LinearAdapterMultiTenantConfig,
    LinearAdapterOAuthConfig,
    LinearAgentSessionCommentRawMessage,
    LinearAgentSessionThreadId,
    LinearClientCredentialsConfig,
    LinearCommentData,
    LinearCommentRawMessage,
    LinearInstallation,
    LinearOAuthCallbackOptions,
    LinearOAuthTokenResponse,
    LinearRawMessage,
    LinearThreadId,
)
from chat_adapter_linear.utils import (
    assert_agent_session_thread,
    get_user_name_from_profile_url,
    render_message_to_linear_markdown,
)

__version__ = "0.1.0"

__all__ = [
    "LINEAR_API_URL",
    "LINEAR_OAUTH_TOKEN_URL",
    "LinearActorData",
    "LinearAdapter",
    "LinearAdapterAPIKeyConfig",
    "LinearAdapterBaseConfig",
    "LinearAdapterClientCredentialsConfig",
    "LinearAdapterConfig",
    "LinearAdapterMode",
    "LinearAdapterMultiTenantConfig",
    "LinearAdapterOAuthConfig",
    "LinearAgentSessionCommentRawMessage",
    "LinearAgentSessionThreadId",
    "LinearClientCredentialsConfig",
    "LinearCommentData",
    "LinearCommentRawMessage",
    "LinearFormatConverter",
    "LinearInstallation",
    "LinearOAuthCallbackOptions",
    "LinearOAuthTokenResponse",
    "LinearRawMessage",
    "LinearThreadId",
    "assert_agent_session_thread",
    "card_to_linear_markdown",
    "card_to_plain_text",
    "channel_id_from_thread_id",
    "create_linear_adapter",
    "decode_thread_id",
    "encode_thread_id",
    "get_user_name_from_profile_url",
    "handle_linear_error",
    "handle_linear_graphql_body",
    "render_message_to_linear_markdown",
    "verify_linear_signature",
]
