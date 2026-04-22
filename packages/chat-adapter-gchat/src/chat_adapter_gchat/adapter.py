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
    format converter, user-info cache, and the HTTP webhook + outbound REST
    dispatch surface. Mirrors upstream ``GoogleChatAdapter`` in
    ``packages/adapter-gchat/src/index.ts``.
    """

    name = "gchat"
    lock_scope: Any = "thread"
    """Google Chat threads are the locking unit (same as Slack)."""

    persist_message_history: bool = False

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

        # Dispatch state — populated by :meth:`initialize`.
        self._chat: Any = None

        # REST client injection point. Tests supply an ``AsyncMock``-shaped
        # object here; production wiring builds an ``_HttpxChatRestClient`` on
        # first use so the import cost stays on the hot path only when we
        # actually call the Chat REST API.
        self._rest_client: Any = None
        self._rest_client_factory: Any = None

    # ---------------------------------------------------------- thread id API

    def encode_thread_id(self, platform_data: GoogleChatThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> GoogleChatThreadId:
        return decode_thread_id(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        return channel_id_from_thread_id(thread_id)

    def is_dm(self, thread_id: str) -> bool:
        return is_dm_thread(thread_id)

    def get_channel_visibility(self, channel_id: str) -> str:
        """Google Chat spaces are private by default; DMs stay private too.

        Upstream treats every space as ``"private"`` unless explicitly marked
        as an external/shared space (``SPACE`` with ``externalUserAllowed``).
        We don't have that metadata on the raw thread ID, so we return
        ``"unknown"`` when we can't tell — matches upstream conservatism.
        """

        if is_dm_thread(channel_id):
            return "private"
        return "unknown"

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

    # ------------------------------------------------------------------ lifecycle

    async def initialize(self, chat: Any) -> None:
        """Store the :class:`Chat` reference for dispatch.

        Called by :meth:`Chat._do_initialize` once per adapter. Mirrors
        upstream ``GoogleChatAdapter.initialize`` which captures the chat
        instance so incoming events can reach handler registrations.
        """

        self._chat = chat

    async def disconnect(self) -> None:
        """Release the HTTP client if one was built. No-op otherwise."""

        client = self._rest_client
        if client is None:
            return
        close = getattr(client, "close", None) or getattr(client, "aclose", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
        self._rest_client = None

    # ------------------------------------------------------------------ rest client

    def _get_rest_client(self) -> Any:
        """Return the Chat REST client, building one from the auth config if
        none was injected.

        Tests should assign ``adapter._rest_client = AsyncMock(...)`` directly
        before calling :meth:`post_message` / friends to bypass the real
        Google API client.
        """

        if self._rest_client is not None:
            return self._rest_client
        if self._rest_client_factory is not None:
            self._rest_client = self._rest_client_factory()
            return self._rest_client
        # Lazy build — an httpx-backed minimal REST shim. Kept inline so the
        # happy path doesn't import google-api-python-client (which is heavy)
        # unless callers opt in via the factory hook.
        self._rest_client = _HttpxChatRestClient(self)
        return self._rest_client

    # ------------------------------------------------------------------ webhook

    async def handle_webhook(
        self,
        body: bytes,
        headers: dict[str, str],
        options: Any | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Handle an inbound Google Chat webhook request.

        Mirrors upstream ``handleWebhook`` in ``adapter-gchat/src/index.ts``:

        1. Parse the body as JSON.
        2. Detect Pub/Sub push envelope vs. direct HTTP webhook by shape.
        3. Verify the appropriate bearer (Pub/Sub audience vs. Chat project).
        4. Dispatch into :meth:`_dispatch_event`.
        """

        import json as _json

        h = {k.lower(): v for k, v in headers.items()}
        authorization = h.get("authorization")
        body_str = body.decode("utf-8") if isinstance(body, bytes | bytearray) else str(body)

        try:
            payload: dict[str, Any] = _json.loads(body_str) if body_str else {}
        except _json.JSONDecodeError as err:
            self.logger.error("Invalid Google Chat webhook body", {"error": err})
            return (400, {}, b"")

        if not isinstance(payload, dict):
            return (400, {}, b"")

        # --- Pub/Sub push path -----------------------------------------------
        from .pubsub import decode_pubsub_envelope, is_pubsub_envelope

        if is_pubsub_envelope(payload):
            if not await self.verify_pubsub_bearer(authorization):
                self.logger.warn("Rejected Pub/Sub push: bearer verification failed")
                return (401, {}, b"")

            event_payload, attributes = decode_pubsub_envelope(payload)
            if not event_payload:
                # Empty / malformed — ack so Pub/Sub doesn't retry forever.
                return (200, {}, b"")
            return await self._dispatch_pubsub_event(event_payload, attributes, options)

        # --- Direct HTTP webhook path ----------------------------------------
        if not await self.verify_webhook_bearer(authorization):
            self.logger.warn("Rejected Google Chat webhook: bearer verification failed")
            return (401, {}, b"")

        return await self._dispatch_event(payload, options)

    async def _dispatch_event(
        self,
        payload: dict[str, Any],
        options: Any | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Route a direct HTTP Chat event to handlers.

        The direct HTTP path delivers the classic Google Chat app event shape
        with a top-level ``type`` field (``MESSAGE``, ``ADDED_TO_SPACE``,
        ``REMOVED_FROM_SPACE``, ``CARD_CLICKED``). Mirrors upstream
        ``dispatchEvent``.
        """

        event_type = payload.get("type")

        if event_type == "MESSAGE":
            await self._handle_message_event(payload, options)
            return (200, {"content-type": "application/json"}, b"{}")

        if event_type == "ADDED_TO_SPACE":
            await self._handle_added_to_space(payload)
            return (200, {"content-type": "application/json"}, b"{}")

        if event_type == "REMOVED_FROM_SPACE":
            await self._handle_removed_from_space(payload)
            return (200, {"content-type": "application/json"}, b"{}")

        # Unknown / unhandled — 200 so Google doesn't retry.
        return (200, {"content-type": "application/json"}, b"{}")

    async def _dispatch_pubsub_event(
        self,
        event_payload: dict[str, Any],
        attributes: dict[str, str],
        options: Any | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Route a Pub/Sub-wrapped Workspace Event into the same dispatch.

        Workspace Events are shaped as ``{"message": {...GoogleChatMessage}}``
        or ``{"reaction": {...}}`` with the event type on the CloudEvents
        ``ce-type`` attribute. To reuse :meth:`_dispatch_event` we wrap the
        payload back into the direct-HTTP shape.
        """

        ce_type = attributes.get("ce-type", "")

        if ce_type == "google.workspace.chat.message.v1.created" and "message" in event_payload:
            message = event_payload["message"] or {}
            space = message.get("space") or {}
            shimmed = {
                "type": "MESSAGE",
                "message": message,
                "space": space,
            }
            await self._handle_message_event(shimmed, options)
            return (200, {"content-type": "application/json"}, b"{}")

        if ce_type in (
            "google.workspace.chat.reaction.v1.created",
            "google.workspace.chat.reaction.v1.deleted",
        ):
            # Reactions via Pub/Sub — forward to Chat. The reaction branch is
            # intentionally minimal: upstream dispatches a reaction event to
            # chat.process_reaction but we keep it best-effort until the
            # integration matrix in Phase 10 pins the exact shape.
            return (200, {"content-type": "application/json"}, b"{}")

        return (200, {"content-type": "application/json"}, b"{}")

    # ----------------------------------------------- inbound event handlers

    async def _handle_message_event(
        self,
        payload: dict[str, Any],
        options: Any | None,
    ) -> None:
        if self._chat is None:
            return

        message_raw = payload.get("message") or {}
        space = payload.get("space") or message_raw.get("space") or {}

        thread_name = (message_raw.get("thread") or {}).get("name") or ""
        space_name = space.get("name") or ""
        thread_id_payload: GoogleChatThreadId = {"spaceName": space_name}
        if thread_name:
            thread_id_payload["threadName"] = thread_name
        if space.get("singleUserBotDm") or space.get("type") == "DM":
            thread_id_payload["isDM"] = True
        thread_id = self.encode_thread_id(thread_id_payload)

        message = self._build_message(message_raw, thread_id)

        # Google Chat mention semantics: if ``argumentText`` starts with a
        # leading space it means the bot was @-mentioned (the mention token is
        # stripped by Google). Upstream uses the same heuristic.
        argument_text = message_raw.get("argumentText", "")
        is_mention = bool(argument_text and argument_text.startswith(" ")) or any(
            annotation.get("type") == "USER_MENTION"
            and (annotation.get("userMention") or {}).get("user", {}).get("type") == "BOT"
            for annotation in (message_raw.get("annotations") or [])
        )
        if is_mention:
            message.is_mention = True

        self._chat.process_message(self, thread_id, message, options)

    def _build_message(self, message_raw: dict[str, Any], thread_id: str) -> Any:
        """Build a :class:`chat.message.Message` from a Google Chat message payload."""

        from datetime import UTC, datetime

        from chat.markdown import parse_markdown
        from chat.message import Message
        from chat.types import Author, MessageMetadata

        sender = message_raw.get("sender") or {}
        sender_type = sender.get("type", "HUMAN")
        is_bot = sender_type == "BOT"

        author = Author(
            user_id=sender.get("name", ""),
            user_name=sender.get("displayName", "") or "",
            full_name=sender.get("displayName", "") or "",
            is_bot=is_bot,
            is_me=bool(self.bot_user_id and sender.get("name") == self.bot_user_id),
        )

        import contextlib

        create_time_raw = message_raw.get("createTime")
        date_sent = datetime.now(UTC)
        if create_time_raw:
            with contextlib.suppress(ValueError):
                date_sent = datetime.fromisoformat(create_time_raw.replace("Z", "+00:00"))

        text = message_raw.get("text", "") or ""

        return Message(
            id=message_raw.get("name", ""),
            thread_id=thread_id,
            text=text,
            formatted=parse_markdown(text),
            raw=message_raw,
            author=author,
            metadata=MessageMetadata(date_sent=date_sent, edited=False),
        )

    async def _handle_added_to_space(self, payload: dict[str, Any]) -> None:
        """Handle an ``ADDED_TO_SPACE`` event by subscribing to the space.

        Mirrors upstream: when the bot is added to a space, create a
        Workspace Events subscription (if a Pub/Sub topic is configured) so
        we receive every message in the space — not just @-mentions. When no
        Pub/Sub topic is configured, this is a no-op + info log.
        """

        space = payload.get("space") or {}
        space_name = space.get("name", "")
        if not space_name:
            return

        if not self.pubsub_topic:
            self.logger.info(
                "Added to space — skipping Workspace Events subscription (no pubsubTopic)",
                {"space": space_name},
            )
            return

        auth = self.get_auth_options()
        if auth is None:
            self.logger.warn(
                "Added to space — no auth configured; cannot create subscription",
                {"space": space_name},
            )
            return

        try:
            from .workspace_events import create_space_subscription

            result = await create_space_subscription(
                {"spaceName": space_name, "pubsubTopic": self.pubsub_topic},
                auth,
            )
            self.logger.info("Created Workspace Events subscription", dict(result))
        except Exception as err:
            self.logger.error(
                "Failed to create Workspace Events subscription",
                {"error": err, "space": space_name},
            )

    async def _handle_removed_from_space(self, payload: dict[str, Any]) -> None:
        """Handle a ``REMOVED_FROM_SPACE`` event by tearing down the sub.

        Mirrors upstream: when the bot is removed, delete the cached
        subscription so we stop receiving events for the space.
        """

        space = payload.get("space") or {}
        space_name = space.get("name", "")
        if not space_name or self._chat is None:
            return

        state = getattr(self._chat, "_state_adapter", None)
        if state is None:
            get_state = getattr(self._chat, "get_state", None)
            if callable(get_state):
                state = get_state()
        if state is None:
            return

        cache_key = f"{_SPACE_SUB_KEY_PREFIX}{space_name}"
        try:
            cached = await state.get(cache_key)
        except Exception:
            cached = None
        if not cached:
            return

        subscription_name = None
        if isinstance(cached, dict):
            subscription_name = cached.get("subscriptionName") or cached.get("subscription_name")
        elif hasattr(cached, "subscription_name"):
            subscription_name = cached.subscription_name

        auth = self.get_auth_options()
        if auth is None or not subscription_name:
            with _safe():
                await state.delete(cache_key)
            return

        try:
            from .workspace_events import delete_space_subscription

            await delete_space_subscription(subscription_name, auth)
        except Exception as err:
            self.logger.error(
                "Failed to delete Workspace Events subscription",
                {"error": err, "subscription": subscription_name},
            )
        with _safe():
            await state.delete(cache_key)

    # ------------------------------------------------------------------ outbound

    async def post_message(
        self,
        thread_id: str,
        message: Any,
    ) -> dict[str, Any]:
        """Post a message to a Google Chat space / thread.

        Accepts the :class:`AdapterPostableMessage` union plus Google Chat
        Card v2 (via :func:`card_to_google_card`). Returns a
        :class:`RawMessage` dict.
        """

        decoded = self.decode_thread_id(thread_id)
        space_name = decoded.get("spaceName", "")
        thread_name = decoded.get("threadName")

        text, cards_v2 = self._postable_to_gchat(message)

        body: dict[str, Any] = {}
        if text:
            body["text"] = text
        if cards_v2:
            body["cardsV2"] = cards_v2
        if thread_name:
            body["thread"] = {"name": thread_name}

        client = self._get_rest_client()
        response = await _call_rest(
            client,
            "spaces.messages.create",
            parent=space_name,
            body=body,
            messageReplyOption="REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD" if thread_name else None,
        )

        message_name = ""
        if isinstance(response, dict):
            message_name = response.get("name", "") or ""
        returned_thread = (
            (response.get("thread") or {}).get("name") if isinstance(response, dict) else None
        )
        result_thread_payload: GoogleChatThreadId = {"spaceName": space_name}
        if returned_thread:
            result_thread_payload["threadName"] = returned_thread
        elif thread_name:
            result_thread_payload["threadName"] = thread_name
        return {
            "id": message_name,
            "raw": response,
            "threadId": self.encode_thread_id(result_thread_payload),
        }

    async def edit_message(
        self,
        thread_id: str,
        message_id: str,
        message: Any,
    ) -> dict[str, Any]:
        """Edit an existing Google Chat message via ``spaces.messages.patch``.

        Upstream uses ``update_mask=text,cards_v2`` so both fields get
        replaced; we mirror that semantics.
        """

        text, cards_v2 = self._postable_to_gchat(message)
        body: dict[str, Any] = {"text": text or ""}
        if cards_v2:
            body["cardsV2"] = cards_v2

        client = self._get_rest_client()
        response = await _call_rest(
            client,
            "spaces.messages.update",
            name=message_id,
            body=body,
            updateMask="text,cards_v2" if cards_v2 else "text",
        )
        return {
            "id": message_id,
            "raw": response,
            "threadId": thread_id,
        }

    async def delete_message(self, thread_id: str, message_id: str) -> None:
        """Delete a Google Chat message via ``spaces.messages.delete``."""

        client = self._get_rest_client()
        await _call_rest(client, "spaces.messages.delete", name=message_id)

    async def add_reaction(
        self,
        thread_id: str,
        message_id: str,
        emoji: str,
    ) -> None:
        """Add a reaction via ``spaces.messages.reactions.create``."""

        from chat.emoji import default_emoji_resolver

        unicode_emoji = default_emoji_resolver.to_gchat(emoji)
        client = self._get_rest_client()
        await _call_rest(
            client,
            "spaces.messages.reactions.create",
            parent=message_id,
            body={"emoji": {"unicode": unicode_emoji}},
        )

    async def remove_reaction(
        self,
        thread_id: str,
        message_id: str,
        emoji: str,
    ) -> None:
        """Remove a reaction via ``spaces.messages.reactions.delete``.

        Google Chat's reactions resource exposes reactions by name
        (``spaces/.../messages/.../reactions/R_ID``). Upstream resolves the
        reaction ID by listing + filtering; we mirror that.
        """

        from chat.emoji import default_emoji_resolver

        unicode_emoji = default_emoji_resolver.to_gchat(emoji)
        client = self._get_rest_client()

        listing = await _call_rest(
            client,
            "spaces.messages.reactions.list",
            parent=message_id,
            filter=f'emoji.unicode = "{unicode_emoji}"',
        )
        reactions = (listing or {}).get("reactions") or []
        for reaction in reactions:
            name = reaction.get("name")
            if name:
                await _call_rest(client, "spaces.messages.reactions.delete", name=name)

    async def post_channel_message(self, channel_id: str, message: Any) -> dict[str, Any]:
        """Post a top-level (new-thread) message to a Google Chat space."""

        # ``channel_id`` is ``gchat:{spaceName}`` at the adapter boundary.
        parts = channel_id.split(":", 1)
        space_name = parts[1] if len(parts) == 2 else channel_id
        return await self.post_message(f"gchat:{space_name}", message)

    async def fetch_messages(
        self,
        thread_id: str,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Fetch messages from a Chat thread via ``spaces.messages.list``.

        Google Chat doesn't expose thread-scoped listing directly — the list
        endpoint is space-scoped with a ``filter`` for ``thread.name``.
        """

        decoded = self.decode_thread_id(thread_id)
        space_name = decoded.get("spaceName", "")
        thread_name = decoded.get("threadName")

        kwargs: dict[str, Any] = {"parent": space_name}
        if thread_name:
            kwargs["filter"] = f'thread.name = "{thread_name}"'
        if isinstance(options, dict):
            if options.get("cursor") is not None:
                kwargs["pageToken"] = options["cursor"]
            if options.get("limit") is not None:
                kwargs["pageSize"] = options["limit"]

        client = self._get_rest_client()
        response = await _call_rest(client, "spaces.messages.list", **kwargs)
        response = response or {}
        return {
            "messages": response.get("messages", []),
            "nextCursor": response.get("nextPageToken") or None,
        }

    async def fetch_channel_info(self, channel_id: str) -> dict[str, Any]:
        parts = channel_id.split(":", 1)
        space_name = parts[1] if len(parts) == 2 else channel_id
        client = self._get_rest_client()
        try:
            info = await _call_rest(client, "spaces.get", name=space_name) or {}
        except Exception as err:
            self.logger.warn("spaces.get failed", {"error": err})
            info = {}
        return {
            "id": channel_id,
            "name": info.get("displayName") or space_name,
            "isDM": bool(info.get("singleUserBotDm")),
            "metadata": info,
        }

    async def fetch_channel_messages(
        self,
        channel_id: str,
        options: Any | None = None,
    ) -> dict[str, Any]:
        parts = channel_id.split(":", 1)
        space_name = parts[1] if len(parts) == 2 else channel_id
        return await self.fetch_messages(f"gchat:{space_name}", options)

    async def list_threads(
        self,
        channel_id: str,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Enumerate threads in a space. No first-class Chat endpoint exists —
        mirror Slack by deduplicating on ``thread.name`` from listed messages.
        """

        result = await self.fetch_channel_messages(channel_id, options)
        seen: set[str] = set()
        threads: list[dict[str, Any]] = []
        for msg in result.get("messages", []):
            thread_name = (msg.get("thread") or {}).get("name")
            if thread_name and thread_name not in seen:
                seen.add(thread_name)
                threads.append(msg)
        return {"threads": threads, "nextCursor": result.get("nextCursor")}

    async def subscribe(self, thread_id: str) -> None:
        """No-op — Google Chat subscription happens at the space level via
        Workspace Events (see :meth:`_handle_added_to_space`).
        """

        return None

    async def unsubscribe(self, thread_id: str) -> None:
        return None

    async def open_dm(self, user_id: str) -> str:
        """Open a DM space with ``user_id`` and return the thread ID.

        Google Chat DMs use ``spaces.findDirectMessage`` (or ``spaces.setup``
        when the DM doesn't exist). Upstream tries the find path first and
        falls back to setup; we mirror that.
        """

        client = self._get_rest_client()
        try:
            space = await _call_rest(client, "spaces.findDirectMessage", name=user_id)
        except Exception:
            space = None
        if not space:
            space = await _call_rest(
                client,
                "spaces.setup",
                body={
                    "space": {"spaceType": "DIRECT_MESSAGE"},
                    "memberships": [{"member": {"name": user_id, "type": "HUMAN"}}],
                },
            )
        space_name = (space or {}).get("name", "")
        return self.encode_thread_id({"spaceName": space_name, "isDM": True})

    async def open_modal(self, trigger_id: str, view: Any) -> Any:
        """Google Chat has no Slack-style modal — raise ``NotImplementedError``.

        Upstream returns the card directly via ``cardsV2``; this matches
        Slack's Protocol surface with an intentional gap documented in
        ``docs/parity.md``.
        """

        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "Google Chat does not support Slack-style modals; post a Card v2 instead.",
            feature="modals",
        )

    async def start_typing(self, thread_id: str) -> None:
        """Google Chat has no public typing indicator — no-op (mirrors upstream)."""

        return None

    async def stream(
        self,
        thread_id: str,
        chunks: Any,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Stream ``chunks`` to a Google Chat message, editing it periodically.

        Mirrors the Slack streaming shape: post placeholder, edit every
        ``streaming_update_interval_ms``, flush on close.
        """

        import asyncio
        import time

        interval_ms = 500
        if isinstance(options, dict) and options.get("streamingUpdateIntervalMs") is not None:
            interval_ms = int(options["streamingUpdateIntervalMs"])
        placeholder = "..."
        if isinstance(options, dict) and options.get("placeholder") is not None:
            placeholder = str(options["placeholder"])

        initial = await self.post_message(thread_id, {"markdown": placeholder})
        message_id = initial["id"]

        accumulated = ""
        last_update = time.monotonic()
        interval_s = max(interval_ms, 1) / 1000.0

        try:
            async for chunk in chunks:
                accumulated += str(chunk)
                now = time.monotonic()
                if now - last_update >= interval_s:
                    await self.edit_message(
                        thread_id, message_id, {"markdown": accumulated or placeholder}
                    )
                    last_update = now
                    await asyncio.sleep(0)
        finally:
            await self.edit_message(thread_id, message_id, {"markdown": accumulated or placeholder})
        return {"id": message_id, "raw": initial.get("raw"), "threadId": thread_id}

    # ------------------------------------------------------------------ postable helpers

    def _postable_to_gchat(
        self,
        message: Any,
    ) -> tuple[str, list[dict[str, Any]] | None]:
        """Convert an :class:`AdapterPostableMessage` to ``(text, cardsV2)``.

        Mirrors the Slack adapter's ``_postable_to_slack`` — returns a text
        field and an optional ``cardsV2`` list the REST body can embed
        directly.
        """

        from .cards import card_to_google_card

        if isinstance(message, str):
            return message, None

        if not isinstance(message, dict):
            return "", None

        # Card element — detected by ``type == "card"``.
        if message.get("type") == "card":
            gcard = card_to_google_card(message, {"endpointUrl": self.endpoint_url or ""})
            return "", [gcard]

        # PostableRaw — raw bypasses formatting.
        if "raw" in message and isinstance(message["raw"], str):
            return message["raw"], None

        # PostableMarkdown — convert via format converter.
        if "markdown" in message:
            formatted = self.format_converter.from_ast(
                __import__("chat.markdown", fromlist=["parse_markdown"]).parse_markdown(
                    str(message["markdown"])
                )
            )
            return formatted, None

        # PostableAst — mdast root passed through.
        if "ast" in message:
            return self.format_converter.from_ast(message["ast"]), None

        return "", None


# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------


class _SafeContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: Any) -> bool:
        return True


def _safe() -> _SafeContext:
    """Swallow exceptions in a small critical-path block (logging handled elsewhere)."""

    return _SafeContext()


async def _call_rest(client: Any, method_path: str, **kwargs: Any) -> Any:
    """Invoke a REST method on a pluggable Chat client.

    The client may be either:

    - A Google API Python Client-style ``Resource`` tree (exposing
      ``spaces().messages().create(...).execute()``).
    - A flat async-callable shim (what tests inject as ``AsyncMock``): the
      method path is resolved by attribute walking (``client.spaces.messages.create``).

    Both return JSON-shaped ``dict``s.
    """

    # Walk attributes to find the handler.
    target: Any = client
    for part in method_path.split("."):
        target = getattr(target, part)
    # Drop ``None`` kwargs before invoking — matches upstream's cleanup of
    # optional fields (googleapiclient rejects ``foo=None`` for some params).
    clean = {k: v for k, v in kwargs.items() if v is not None}
    result = target(**clean)
    if hasattr(result, "__await__"):
        return await result
    # Google API client: ``.execute()`` is sync; wrap in ``to_thread`` to keep
    # the adapter's async contract.
    if hasattr(result, "execute"):
        import asyncio

        return await asyncio.to_thread(result.execute)
    return result


class _HttpxChatRestClient:
    """Minimal httpx-backed Chat REST shim matching the attribute-walk shape.

    Production wiring falls back to this when no ``_rest_client_factory`` is
    configured. It exposes ``client.spaces.messages.create(...)`` etc. and
    dispatches to the corresponding Chat REST v1 endpoint.
    """

    def __init__(self, adapter: GoogleChatAdapter) -> None:
        self._adapter = adapter

    def __getattr__(self, name: str) -> _RestNode:
        return _RestNode(self._adapter, [name])

    async def close(self) -> None:
        return None


class _RestNode:
    """One level in the attribute-walk path."""

    def __init__(self, adapter: GoogleChatAdapter, path: list[str]) -> None:
        self._adapter = adapter
        self._path = path

    def __getattr__(self, name: str) -> _RestNode:
        return _RestNode(self._adapter, [*self._path, name])

    async def __call__(self, **kwargs: Any) -> Any:
        method = self._path[-1]
        resource_path = ".".join(self._path[:-1])
        return await _chat_rest_request(self._adapter, resource_path, method, kwargs)


async def _chat_rest_request(
    adapter: GoogleChatAdapter,
    resource_path: str,
    method: str,
    params: dict[str, Any],
) -> Any:
    """Map ``(resource, method, params)`` to a Chat REST v1 HTTP call.

    Kept deliberately small — this covers the handful of endpoints Phase 3
    exercises (``spaces.messages.create/update/delete/list``, reactions,
    space info). Anything else falls through to a generic best-effort map.
    """

    import httpx

    from .workspace_events import _get_access_token

    auth_opts = adapter.get_auth_options()
    if auth_opts is None:
        raise RuntimeError("Google Chat REST call attempted without auth configured")

    token = await _get_access_token(
        auth_opts,
        [
            "https://www.googleapis.com/auth/chat.bot",
            "https://www.googleapis.com/auth/chat.messages",
            "https://www.googleapis.com/auth/chat.messages.reactions",
        ],
    )

    api_base = adapter.api_url or "https://chat.googleapis.com/v1"

    body = params.pop("body", None)
    # Translate the handful of endpoints this port uses.
    if resource_path == "spaces.messages" and method == "create":
        parent = params.pop("parent", "")
        url = f"{api_base}/{parent}/messages"
        http_method = "POST"
    elif resource_path == "spaces.messages" and method == "update":
        name = params.pop("name", "")
        url = f"{api_base}/{name}"
        http_method = "PATCH"
    elif resource_path == "spaces.messages" and method == "delete":
        name = params.pop("name", "")
        url = f"{api_base}/{name}"
        http_method = "DELETE"
    elif resource_path == "spaces.messages" and method == "list":
        parent = params.pop("parent", "")
        url = f"{api_base}/{parent}/messages"
        http_method = "GET"
    elif resource_path == "spaces.messages.reactions" and method == "create":
        parent = params.pop("parent", "")
        url = f"{api_base}/{parent}/reactions"
        http_method = "POST"
    elif resource_path == "spaces.messages.reactions" and method == "list":
        parent = params.pop("parent", "")
        url = f"{api_base}/{parent}/reactions"
        http_method = "GET"
    elif resource_path == "spaces.messages.reactions" and method == "delete":
        name = params.pop("name", "")
        url = f"{api_base}/{name}"
        http_method = "DELETE"
    elif resource_path == "spaces" and method == "get":
        name = params.pop("name", "")
        url = f"{api_base}/{name}"
        http_method = "GET"
    elif resource_path == "spaces" and method == "findDirectMessage":
        name = params.pop("name", "")
        url = f"{api_base}/spaces:findDirectMessage"
        params["name"] = name
        http_method = "GET"
    elif resource_path == "spaces" and method == "setup":
        url = f"{api_base}/spaces:setup"
        http_method = "POST"
    else:
        raise NotImplementedError(f"Google Chat REST method not wired: {resource_path}.{method}")

    async with httpx.AsyncClient() as client:
        resp = await client.request(
            http_method,
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            params={k: v for k, v in params.items() if v is not None},
            json=body,
        )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()


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
