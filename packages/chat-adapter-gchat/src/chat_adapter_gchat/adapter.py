"""Google Chat adapter — public types, JWT verification, and adapter façade.

Python port of upstream ``packages/adapter-gchat/src/index.ts``.

Scope of this port (Phase 2, Apr 2026):

- Public config types (``GoogleChatAdapterConfig`` union, ``ServiceAccountCredentials``)
- Google Chat event payload types (:class:`GoogleChatMessage`,
  :class:`GoogleChatSpace`, :class:`GoogleChatUser`, :class:`GoogleChatEvent`)
- Bearer JWT verification helper (:func:`verify_bearer_token`) using
  :mod:`google.oauth2.id_token`
- Thread ID codec + DM detection (delegated to :mod:`thread_utils`)
- :class:`GoogleChatAdapter` class with config resolution, auth client
  construction, and the subset of ``Adapter`` methods required for
  ``parse_message`` / ``encode_thread_id`` / ``decode_thread_id`` / ``is_dm``.
- :func:`create_google_chat_adapter` factory

The full event dispatch / streaming / REST surface (mirroring upstream's
~2.7K-line ``index.ts``) depends on the ``Chat``/``Adapter`` Protocols and the
``StreamingMarkdownRenderer`` integration from ``chat`` core part B. Methods
that require those types raise :class:`NotImplementedError` for now — see
``docs/parity.md`` for the current split.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from chat_adapter_shared import ValidationError

from .markdown import GoogleChatFormatConverter
from .thread_utils import (
    GoogleChatThreadId,
    decode_thread_id,
    encode_thread_id,
    is_dm_thread,
)
from .user_info import UserInfoCache
from .workspace_events import ServiceAccountCredentials, WorkspaceEventsAuthOptions

if TYPE_CHECKING:
    from chat import Logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Refresh Workspace Events subscriptions 1h before they expire.
SUBSCRIPTION_REFRESH_BUFFER_MS = 60 * 60 * 1000
# Cache subscription info for 25h (longer than the max 24h subscription TTL).
SUBSCRIPTION_CACHE_TTL_MS = 25 * 60 * 60 * 1000

_SPACE_SUB_KEY_PREFIX = "gchat:space-sub:"

# OAuth scopes needed for full bot functionality (mentions, reactions, DMs).
# ``chat.spaces.create`` requires domain-wide delegation to take effect.
_DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/chat.bot",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.messages.reactions.create",
    "https://www.googleapis.com/auth/chat.messages.reactions",
    "https://www.googleapis.com/auth/chat.spaces.create",
]


# ---------------------------------------------------------------------------
# Public config / data types
# ---------------------------------------------------------------------------


class GoogleChatAdapterConfig(TypedDict, total=False):
    """Config for :class:`GoogleChatAdapter` / :func:`create_google_chat_adapter`.

    Mirrors upstream's ``GoogleChatAdapterConfig`` union (service-account,
    ADC, custom-auth, auto-from-env). Supplying ``credentials`` and
    ``useApplicationDefaultCredentials`` simultaneously is invalid — the
    constructor raises :class:`ValidationError`.
    """

    apiUrl: str
    auth: Any
    credentials: ServiceAccountCredentials
    endpointUrl: str
    googleChatProjectNumber: str
    impersonateUser: str
    logger: Logger
    pubsubAudience: str
    pubsubTopic: str
    useApplicationDefaultCredentials: bool
    userName: str


class GoogleChatMessageAnnotation(TypedDict, total=False):
    type: str
    startIndex: int
    length: int
    userMention: dict[str, Any]


class GoogleChatMessage(TypedDict, total=False):
    """Raw Google Chat message payload (the shape emitted by the Chat API)."""

    annotations: list[GoogleChatMessageAnnotation]
    argumentText: str
    attachment: list[dict[str, Any]]
    createTime: str
    formattedText: str
    name: str
    sender: dict[str, Any]
    space: dict[str, Any]
    text: str
    thread: dict[str, str]


class GoogleChatSpace(TypedDict, total=False):
    """Google Chat space payload."""

    displayName: str
    name: str
    singleUserBotDm: bool
    spaceThreadingState: str
    spaceType: str
    type: str


class GoogleChatUser(TypedDict, total=False):
    """Google Chat user payload."""

    displayName: str
    email: str
    name: str
    type: str


class _GoogleChatEventChat(TypedDict, total=False):
    user: GoogleChatUser
    eventTime: str
    messagePayload: dict[str, Any]
    addedToSpacePayload: dict[str, Any]
    removedFromSpacePayload: dict[str, Any]
    buttonClickedPayload: dict[str, Any]


class GoogleChatEvent(TypedDict, total=False):
    """Google Workspace Add-ons event envelope.

    This is the shape delivered to HTTP-endpoint-style Google Chat apps via
    the Workspace Add-ons framework.
    """

    chat: _GoogleChatEventChat
    commonEventObject: dict[str, Any]


@dataclass(slots=True)
class SpaceSubscriptionInfo:
    """Cached Workspace Events subscription info for a space."""

    subscription_name: str
    expire_time_ms: int


# ---------------------------------------------------------------------------
# Thread ID helpers (instance-method wrappers)
# ---------------------------------------------------------------------------


def channel_id_from_thread_id(thread_id: str) -> str:
    """Return the channel-scoped thread ID (``gchat:{spaceName}``)."""

    decoded = decode_thread_id(thread_id)
    return f"gchat:{decoded['spaceName']}"


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------


async def verify_bearer_token(
    authorization_header: str | None,
    expected_audience: str,
    *,
    allowed_issuers: tuple[str, ...] = (
        "https://accounts.google.com",
        "accounts.google.com",
        "chat@system.gserviceaccount.com",
    ),
) -> bool:
    """Verify a Google-signed Bearer JWT from an ``Authorization`` header.

    Used for both direct Google Chat webhooks (issued by
    ``chat@system.gserviceaccount.com``) and Pub/Sub push messages (issued by
    ``accounts.google.com``).

    Returns ``True`` if the token is valid, ``False`` otherwise. Never raises
    — mirrors upstream behavior.
    """

    if not authorization_header or not authorization_header.startswith("Bearer "):
        return False

    token = authorization_header[len("Bearer ") :]
    if not token:
        return False

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        payload = id_token.verify_token(
            token,
            google_requests.Request(),
            audience=expected_audience,
            certs_url="https://www.googleapis.com/oauth2/v1/certs",
        )
    except Exception:
        return False

    issuer = payload.get("iss")
    return issuer in allowed_issuers


# ---------------------------------------------------------------------------
# GoogleChatAdapter class
# ---------------------------------------------------------------------------


class GoogleChatAdapter:
    """Google Chat platform adapter for chat-py.

    Holds the auth configuration, Pub/Sub / webhook verification settings,
    format converter, and user-info cache. Higher-level event dispatch
    (``handle_webhook``, streaming ``post_message``, reactions) lands in a
    follow-up chunk once chat core part B settles on the final async
    :class:`~chat.types.Adapter` Protocol.
    """

    name = "gchat"

    def __init__(self, config: GoogleChatAdapterConfig | None = None) -> None:
        cfg: GoogleChatAdapterConfig = dict(config or {})  # type: ignore[assignment]

        # Mutually exclusive auth fields (mirrors upstream TS union).
        has_credentials = "credentials" in cfg and cfg.get("credentials") is not None
        has_adc = bool(cfg.get("useApplicationDefaultCredentials"))
        has_custom_auth = "auth" in cfg and cfg.get("auth") is not None
        if sum((has_credentials, has_adc, has_custom_auth)) > 1:
            raise ValidationError(
                "gchat",
                "Only one of `credentials`, `useApplicationDefaultCredentials`, "
                "or `auth` may be provided.",
            )

        self.user_name: str = cfg.get("userName") or "bot"
        self.bot_user_id: str | None = None

        self.pubsub_topic: str | None = cfg.get("pubsubTopic") or os.environ.get(
            "GOOGLE_CHAT_PUBSUB_TOPIC"
        )
        self.impersonate_user: str | None = cfg.get("impersonateUser") or os.environ.get(
            "GOOGLE_CHAT_IMPERSONATE_USER"
        )
        self.endpoint_url: str | None = cfg.get("endpointUrl")
        self.google_chat_project_number: str | None = cfg.get(
            "googleChatProjectNumber"
        ) or os.environ.get("GOOGLE_CHAT_PROJECT_NUMBER")
        self.pubsub_audience: str | None = cfg.get("pubsubAudience") or os.environ.get(
            "GOOGLE_CHAT_PUBSUB_AUDIENCE"
        )
        self.api_url: str | None = cfg.get("apiUrl") or os.environ.get("GOOGLE_CHAT_API_URL")

        self.credentials: ServiceAccountCredentials | None = None
        self.use_adc: bool = False
        self.custom_auth: Any = None

        if has_credentials:
            self.credentials = cfg["credentials"]
        elif has_adc:
            self.use_adc = True
        elif has_custom_auth:
            self.custom_auth = cfg["auth"]
        else:
            # Auto-detect from env vars.
            env_credentials = os.environ.get("GOOGLE_CHAT_CREDENTIALS")
            if env_credentials:
                import json

                try:
                    raw = json.loads(env_credentials)
                except json.JSONDecodeError as err:
                    raise ValidationError(
                        "gchat",
                        "GOOGLE_CHAT_CREDENTIALS is not valid JSON.",
                    ) from err
                self.credentials = ServiceAccountCredentials(
                    client_email=raw.get("client_email", ""),
                    private_key=raw.get("private_key", ""),
                    project_id=raw.get("project_id"),
                )
            elif os.environ.get("GOOGLE_CHAT_USE_ADC") == "true":
                self.use_adc = True
            else:
                raise ValidationError(
                    "gchat",
                    "Authentication is required. Set GOOGLE_CHAT_CREDENTIALS or "
                    "GOOGLE_CHAT_USE_ADC=true, or provide credentials/auth in config.",
                )

        # Logger — defer chat import so optional-import users still work.
        logger = cfg.get("logger")
        if logger is None:
            from chat import ConsoleLogger

            logger = ConsoleLogger("info").child("gchat")
        self.logger: Logger = logger

        self.format_converter = GoogleChatFormatConverter()
        self.user_info_cache = UserInfoCache(None, self.logger)
        self._pending_subscriptions: dict[str, Any] = {}
        self._warned_no_webhook_verification = False
        self._warned_no_pubsub_verification = False

    # ---------------------------------------------------------- thread id API

    def encode_thread_id(self, platform_data: GoogleChatThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> GoogleChatThreadId:
        return decode_thread_id(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        return channel_id_from_thread_id(thread_id)

    def is_dm(self, thread_id: str) -> bool:
        return is_dm_thread(thread_id)

    # ------------------------------------------------------ parse_message

    def parse_message(self, raw: Any) -> Any:
        """Translate a ``GoogleChatEvent`` into a :class:`~chat.message.Message`.

        Raises :class:`ValidationError` when the event is not a message event.
        """

        event: GoogleChatEvent = raw
        chat_section = event.get("chat") if isinstance(event, dict) else None
        message_payload = chat_section.get("messagePayload") if chat_section else None
        if not message_payload:
            raise ValidationError("gchat", "Cannot parse non-message event")

        message = message_payload.get("message") or {}
        space = message_payload.get("space") or {}

        thread_name = (message.get("thread") or {}).get("name") or message.get("name")
        thread_id = self.encode_thread_id(
            {
                "spaceName": space.get("name", ""),
                "threadName": thread_name or "",
            }
        )

        return self._parse_google_chat_message(event, thread_id)

    def _parse_google_chat_message(self, event: GoogleChatEvent, thread_id: str) -> Any:
        """Best-effort translation of a Google Chat message to :class:`Message`.

        We keep this lightweight for the Phase 2 slice — the full translation
        (annotations → mentions, attachments → files) lands with the rest of
        the webhook dispatcher in the next PR.
        """

        from datetime import UTC, datetime

        from chat import Message
        from chat.markdown import parse_markdown
        from chat.types import Author, MessageMetadata

        message_payload = event["chat"].get("messagePayload") or {}
        message = message_payload.get("message") or {}
        sender = message.get("sender") or {}

        sender_type = sender.get("type", "HUMAN")
        is_bot = sender_type == "BOT"

        author = Author(
            user_id=sender.get("name", ""),
            user_name=sender.get("displayName", "") or "",
            full_name=sender.get("displayName", "") or "",
            is_bot=is_bot,
            is_me=bool(self.bot_user_id and sender.get("name") == self.bot_user_id),
        )

        create_time_raw = message.get("createTime")
        try:
            date_sent = (
                datetime.fromisoformat(create_time_raw.replace("Z", "+00:00"))
                if create_time_raw
                else datetime.now(UTC)
            )
        except ValueError:
            date_sent = datetime.now(UTC)

        text = message.get("text", "") or ""

        return Message(
            id=message.get("name", ""),
            thread_id=thread_id,
            text=text,
            formatted=parse_markdown(text),
            raw=event,
            author=author,
            metadata=MessageMetadata(date_sent=date_sent, edited=False),
            attachments=[],
            links=[],
            is_mention=any(
                annotation.get("type") == "USER_MENTION"
                and (annotation.get("userMention") or {}).get("user", {}).get("type") == "BOT"
                for annotation in (message.get("annotations") or [])
            ),
        )

    # ------------------------------------------------------ render_formatted

    def render_formatted(self, content: Any) -> str:
        return self.format_converter.from_ast(content)

    # ------------------------------------------------------ auth helpers

    def get_auth_options(self) -> WorkspaceEventsAuthOptions | None:
        """Build ``WorkspaceEventsAuthOptions`` for the configured auth method."""

        if self.credentials is not None:
            opts: dict[str, Any] = {"credentials": self.credentials}
            if self.impersonate_user:
                opts["impersonateUser"] = self.impersonate_user
            return opts  # type: ignore[return-value]
        if self.use_adc:
            opts = {"useApplicationDefaultCredentials": True}
            if self.impersonate_user:
                opts["impersonateUser"] = self.impersonate_user
            return opts  # type: ignore[return-value]
        if self.custom_auth is not None:
            return {"auth": self.custom_auth}
        return None

    # ---------------------------------------------------- JWT verify wrapper

    async def verify_webhook_bearer(self, authorization_header: str | None) -> bool:
        """Verify a direct Google Chat webhook ``Authorization`` header.

        Returns ``True`` when verification is disabled (no
        ``googleChatProjectNumber`` configured); otherwise delegates to
        :func:`verify_bearer_token`.
        """

        if not self.google_chat_project_number:
            if not self._warned_no_webhook_verification:
                self._warned_no_webhook_verification = True
                self.logger.warn(
                    "Google Chat webhook verification is disabled. Set "
                    "GOOGLE_CHAT_PROJECT_NUMBER or googleChatProjectNumber to "
                    "verify incoming requests."
                )
            return True
        return await verify_bearer_token(authorization_header, self.google_chat_project_number)

    async def verify_pubsub_bearer(self, authorization_header: str | None) -> bool:
        """Verify a Pub/Sub push ``Authorization`` header."""

        if not self.pubsub_audience:
            if not self._warned_no_pubsub_verification:
                self._warned_no_pubsub_verification = True
                self.logger.warn(
                    "Pub/Sub webhook verification is disabled. Set "
                    "GOOGLE_CHAT_PUBSUB_AUDIENCE or pubsubAudience to verify "
                    "incoming requests."
                )
            return True
        return await verify_bearer_token(authorization_header, self.pubsub_audience)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_google_chat_adapter(
    config: GoogleChatAdapterConfig | None = None,
) -> GoogleChatAdapter:
    """Create a :class:`GoogleChatAdapter` with env-var fallbacks filled in.

    Thin wrapper around the constructor to match the upstream
    ``createGoogleChatAdapter`` entrypoint.
    """

    return GoogleChatAdapter(config)


__all__ = [
    "SUBSCRIPTION_CACHE_TTL_MS",
    "SUBSCRIPTION_REFRESH_BUFFER_MS",
    "GoogleChatAdapter",
    "GoogleChatAdapterConfig",
    "GoogleChatEvent",
    "GoogleChatMessage",
    "GoogleChatMessageAnnotation",
    "GoogleChatSpace",
    "GoogleChatUser",
    "SpaceSubscriptionInfo",
    "channel_id_from_thread_id",
    "create_google_chat_adapter",
    "verify_bearer_token",
]
