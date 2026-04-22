"""Google Chat adapter for chat-py.

Python port of ``@chat-adapter/gchat``. Re-exports the public surface from
the submodules so callers can ``from chat_adapter_gchat import
create_google_chat_adapter`` without knowing the internal layout.
"""

from __future__ import annotations

from .adapter import (
    SUBSCRIPTION_CACHE_TTL_MS,
    SUBSCRIPTION_REFRESH_BUFFER_MS,
    GoogleChatAdapter,
    GoogleChatAdapterConfig,
    GoogleChatEvent,
    GoogleChatMessage,
    GoogleChatMessageAnnotation,
    GoogleChatSpace,
    GoogleChatUser,
    SpaceSubscriptionInfo,
    channel_id_from_thread_id,
    create_google_chat_adapter,
    verify_bearer_token,
)
from .cards import (
    CardConversionOptions,
    GoogleChatCard,
    GoogleChatCardBody,
    GoogleChatCardHeader,
    GoogleChatCardSection,
    GoogleChatWidget,
    card_to_fallback_text,
    card_to_google_card,
)
from .markdown import GoogleChatFormatConverter
from .thread_utils import (
    GoogleChatThreadId,
    decode_thread_id,
    encode_thread_id,
    is_dm_thread,
)
from .user_info import CachedUserInfo, UserInfoCache
from .workspace_events import (
    AdcAuth,
    CreateSpaceSubscriptionOptions,
    CustomAuth,
    PubSubMessage,
    PubSubPushMessage,
    ServiceAccountAuth,
    ServiceAccountCredentials,
    SpaceSubscriptionResult,
    WorkspaceEventNotification,
    WorkspaceEventsAuthOptions,
    create_space_subscription,
    decode_pubsub_message,
    delete_space_subscription,
    list_space_subscriptions,
)

__version__ = "0.1.0"

__all__ = [
    "SUBSCRIPTION_CACHE_TTL_MS",
    "SUBSCRIPTION_REFRESH_BUFFER_MS",
    "AdcAuth",
    "CachedUserInfo",
    "CardConversionOptions",
    "CreateSpaceSubscriptionOptions",
    "CustomAuth",
    "GoogleChatAdapter",
    "GoogleChatAdapterConfig",
    "GoogleChatCard",
    "GoogleChatCardBody",
    "GoogleChatCardHeader",
    "GoogleChatCardSection",
    "GoogleChatEvent",
    "GoogleChatFormatConverter",
    "GoogleChatMessage",
    "GoogleChatMessageAnnotation",
    "GoogleChatSpace",
    "GoogleChatThreadId",
    "GoogleChatUser",
    "GoogleChatWidget",
    "PubSubMessage",
    "PubSubPushMessage",
    "ServiceAccountAuth",
    "ServiceAccountCredentials",
    "SpaceSubscriptionInfo",
    "SpaceSubscriptionResult",
    "UserInfoCache",
    "WorkspaceEventNotification",
    "WorkspaceEventsAuthOptions",
    "__version__",
    "card_to_fallback_text",
    "card_to_google_card",
    "channel_id_from_thread_id",
    "create_google_chat_adapter",
    "create_space_subscription",
    "decode_pubsub_message",
    "decode_thread_id",
    "delete_space_subscription",
    "encode_thread_id",
    "is_dm_thread",
    "list_space_subscriptions",
    "verify_bearer_token",
]
