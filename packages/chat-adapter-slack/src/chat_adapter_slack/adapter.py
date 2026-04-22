"""Slack adapter — public types, signature verification, and adapter façade.

Python port of upstream ``packages/adapter-slack/src/index.ts``.

Scope of this port (Phase 2, Apr 2026):

- Public types (:class:`SlackAdapterConfig`, :class:`SlackThreadId`, …)
- Webhook signature verification (``verify_signature``) using stdlib ``hmac``
- Thread ID codec (``encode_thread_id`` / ``decode_thread_id``)
- Channel visibility & DM detection
- Slack message URL parser
- :class:`SlackAdapter` class wired to ``slack_sdk.web.async_client.AsyncWebClient``
- :func:`create_slack_adapter` factory with env-var precedence rules

The full event dispatch / streaming / file-upload surface (mirroring upstream's
~4.7K-line ``index.ts``) depends on the ``Chat``/``Adapter`` Protocols and
``StreamingMarkdownRenderer`` from ``chat`` core part B. Methods that require
those types raise :class:`NotImplementedError` until the chat core catches up —
see ``docs/parity.md`` for the current split.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import AsyncIterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from urllib.parse import parse_qs

from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    ValidationError,
)

from .crypto import decode_key

if TYPE_CHECKING:
    from chat import Logger
    from slack_sdk.web.async_client import AsyncWebClient


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_SLACK_USER_ID_PATTERN = re.compile(r"^[A-Z0-9_]+$")
_SLACK_USER_ID_EXACT_PATTERN = re.compile(r"^U[A-Z0-9]+$")

# Slack message URL format: https://{workspace}.slack.com/archives/{channel}/p{ts}
# where ``{ts}`` is the message timestamp with the ``.`` stripped.
_SLACK_MESSAGE_URL_PATTERN = re.compile(
    r"^https?://[^/]+\.slack\.com/archives/([A-Z0-9]+)/p(\d+)(?:\?.*)?$"
)

# Slack's block_suggestion responses need to land within 3s. Leave headroom.
OPTIONS_LOAD_TIMEOUT_MS = 2500

# User / channel / reverse index caches persist for 8 days.
_USER_CACHE_TTL_MS = 8 * 24 * 60 * 60 * 1000
_CHANNEL_CACHE_TTL_MS = 8 * 24 * 60 * 60 * 1000
_REVERSE_INDEX_TTL_MS = 8 * 24 * 60 * 60 * 1000

SlackAdapterMode = Literal["webhook", "socket"]


# ---------------------------------------------------------------------------
# Public config / data types
# ---------------------------------------------------------------------------


class SlackAdapterConfig(TypedDict, total=False):
    """Configuration for :class:`SlackAdapter` / :func:`create_slack_adapter`."""

    apiUrl: str
    appToken: str
    botToken: str
    botUserId: str
    clientId: str
    clientSecret: str
    encryptionKey: str
    installationKeyPrefix: str
    logger: Logger
    mode: SlackAdapterMode
    signingSecret: str
    socketForwardingSecret: str
    userName: str


class SlackOAuthCallbackOptions(TypedDict, total=False):
    """Options passed to :meth:`SlackAdapter.handle_oauth_callback`."""

    redirectUri: str


class SlackInstallation(TypedDict, total=False):
    """Per-workspace installation record (multi-workspace mode)."""

    botToken: str
    botUserId: str
    teamName: str


class SlackThreadId(TypedDict):
    """Decoded Slack-specific thread ID data."""

    channel: str
    threadTs: str


class _SlackEventBlockElement(TypedDict, total=False):
    type: str
    url: str
    text: str


class _SlackEventBlock(TypedDict, total=False):
    type: str
    elements: list[dict[str, Any]]


class _SlackEventFile(TypedDict, total=False):
    id: str
    mimetype: str
    url_private: str
    name: str
    size: int
    original_w: int
    original_h: int


class SlackEvent(TypedDict, total=False):
    """Raw Slack event payload (``event`` field of an Events API envelope)."""

    blocks: list[dict[str, Any]]
    bot_id: str
    channel: str
    channel_type: str
    edited: dict[str, str]
    files: list[dict[str, Any]]
    latest_reply: str
    reply_count: int
    subtype: str
    team: str
    team_id: str
    text: str
    thread_ts: str
    ts: str
    type: str
    user: str
    username: str


class SlackReactionEvent(TypedDict, total=False):
    """``reaction_added`` / ``reaction_removed`` event payload."""

    event_ts: str
    item: dict[str, str]
    item_user: str
    reaction: str
    type: str
    user: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_next_mention(text: str) -> int:
    """Return the index of the next ``<@`` or ``<#`` token, or ``-1``."""

    at_idx = text.find("<@")
    hash_idx = text.find("<#")
    if at_idx == -1:
        return hash_idx
    if hash_idx == -1:
        return at_idx
    return min(at_idx, hash_idx)


def parse_slack_message_url(url: str) -> tuple[str, str] | None:
    """Parse a ``https://x.slack.com/archives/CHAN/pTS`` URL.

    Returns ``(channel, message_ts)`` — the timestamp is reconstituted with a
    ``.`` at the standard ``1234567890.123456`` split. Returns ``None`` if the
    URL does not match.
    """

    match = _SLACK_MESSAGE_URL_PATTERN.match(url)
    if not match:
        return None
    channel = match.group(1)
    ts_compact = match.group(2)
    ts = f"{ts_compact[:-6]}.{ts_compact[-6:]}" if len(ts_compact) >= 7 else ts_compact
    return channel, ts


def encode_thread_id(platform_data: SlackThreadId) -> str:
    """Build a canonical thread ID string: ``slack:{channel}:{threadTs}``."""

    channel = platform_data["channel"]
    thread_ts = platform_data.get("threadTs", "") or ""
    return f"slack:{channel}:{thread_ts}"


def decode_thread_id(thread_id: str) -> SlackThreadId:
    """Inverse of :func:`encode_thread_id`.

    Accepts both 2-part (``slack:C123``) and 3-part (``slack:C123:TS``) forms.
    Raises :class:`ValidationError` on malformed input.
    """

    parts = thread_id.split(":")
    if len(parts) < 2 or len(parts) > 3 or parts[0] != "slack":
        raise ValidationError("slack", f"Invalid Slack thread ID: {thread_id}")
    return {
        "channel": parts[1],
        "threadTs": parts[2] if len(parts) == 3 else "",
    }


def channel_id_from_thread_id(thread_id: str) -> str:
    """Strip the ``:threadTs`` suffix and return ``slack:{channel}``."""

    decoded = decode_thread_id(thread_id)
    return f"slack:{decoded['channel']}"


def is_dm_thread_id(thread_id: str) -> bool:
    """DMs use Slack channel IDs starting with ``D``."""

    return decode_thread_id(thread_id)["channel"].startswith("D")


# ---------------------------------------------------------------------------
# Signature verification (stdlib — matches upstream byte-for-byte)
# ---------------------------------------------------------------------------


def verify_signature(
    body: str,
    timestamp: str | None,
    signature: str | None,
    signing_secret: str | None,
    *,
    max_skew_seconds: int = 300,
    now_seconds: float | None = None,
) -> bool:
    """Verify a Slack webhook signature.

    Slack computes ``v0=<hex_hmac_sha256(signing_secret, "v0:TS:BODY")>`` and
    puts it in the ``X-Slack-Signature`` header, with ``TS`` in
    ``X-Slack-Request-Timestamp``. We recompute it and compare with
    :func:`hmac.compare_digest` to avoid timing leaks.

    Returns ``False`` (never raises) if any part is missing or the timestamp
    drifts more than 5 minutes from now — mirroring upstream behavior.
    """

    if not (timestamp and signature and signing_secret):
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    now = now_seconds if now_seconds is not None else time.time()
    if abs(int(now) - ts_int) > max_skew_seconds:
        return False

    base = f"v0:{timestamp}:{body}".encode()
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()

    return hmac.compare_digest(signature, expected)


# ---------------------------------------------------------------------------
# SlackAdapter class
# ---------------------------------------------------------------------------


class SlackAdapter:
    """Slack platform adapter for chat-py.

    The class holds the Slack Web API client, OAuth configuration, and
    webhook-verification state. Full event dispatch (``handle_webhook``,
    ``post``, ``edit``, ``react``, streaming, file upload) is implemented in
    terms of the :class:`chat.Adapter` Protocol — see module docstring for the
    current part-B split.
    """

    name = "slack"
    lock_scope: Literal["thread", "channel"] | None = "thread"
    persist_message_history: bool = False

    def __init__(self, config: SlackAdapterConfig | None = None) -> None:
        cfg: SlackAdapterConfig = dict(config or {})  # type: ignore[assignment]

        signing_secret = cfg.get("signingSecret") or os.environ.get("SLACK_SIGNING_SECRET")
        mode: SlackAdapterMode = cfg.get("mode", "webhook")
        if mode == "webhook" and not signing_secret:
            raise ValidationError(
                "slack",
                "signingSecret is required for webhook mode. Set "
                "SLACK_SIGNING_SECRET or provide it in config.",
            )

        # Zero-config mode lets us fall back to env vars for auth tokens.
        zero_config = not (
            cfg.get("signingSecret")
            or cfg.get("botToken")
            or cfg.get("clientId")
            or cfg.get("clientSecret")
        )

        bot_token = cfg.get("botToken") or (
            os.environ.get("SLACK_BOT_TOKEN") if zero_config else None
        )
        client_id = cfg.get("clientId") or (
            os.environ.get("SLACK_CLIENT_ID") if zero_config else None
        )
        client_secret = cfg.get("clientSecret") or (
            os.environ.get("SLACK_CLIENT_SECRET") if zero_config else None
        )

        # Lazy import to keep optional-dep surface small when adapter is only
        # imported for its types/helpers.
        from slack_sdk.web.async_client import AsyncWebClient

        api_url = cfg.get("apiUrl") or os.environ.get("SLACK_API_URL")
        client_kwargs: dict[str, Any] = {}
        if api_url:
            client_kwargs["base_url"] = api_url
        self.client: AsyncWebClient = AsyncWebClient(token=bot_token, **client_kwargs)
        self.signing_secret: str | None = signing_secret
        self._default_bot_token: str | None = bot_token
        self.user_name: str = cfg.get("userName") or "bot"
        self._bot_user_id: str | None = cfg.get("botUserId") or None
        self._bot_id: str | None = None

        self.app_token: str | None = cfg.get("appToken")
        self.mode: SlackAdapterMode = mode
        self.socket_forwarding_secret: str | None = cfg.get("socketForwardingSecret") or cfg.get(
            "appToken"
        )

        self.client_id: str | None = client_id
        self.client_secret: str | None = client_secret
        self.installation_key_prefix: str = cfg.get("installationKeyPrefix") or "slack:installation"

        encryption_key = cfg.get("encryptionKey") or os.environ.get("SLACK_ENCRYPTION_KEY")
        self.encryption_key: bytes | None = decode_key(encryption_key) if encryption_key else None

        # Logger is optional — defer importing ConsoleLogger until needed so
        # tests that mock out chat stay lightweight.
        logger = cfg.get("logger")
        if logger is None:
            from chat import ConsoleLogger

            logger = ConsoleLogger("info").child("slack")
        self.logger: Logger = logger

        # Cache of channels known to be Slack-Connect (external/shared).
        self._external_channels: set[str] = set()

        # Format converter (reused across calls for regex caching).
        from .markdown import SlackFormatConverter

        self.format_converter = SlackFormatConverter()

        # Chat reference — set by :meth:`initialize`.
        self._chat: Any = None

        # In-flight streams keyed by the temp message ts so we can update the
        # final text on stream close. Map: message_ts -> accumulated buffer.
        self._active_streams: dict[str, str] = {}

    # ------------------------------------------------------------------ props

    @property
    def is_socket_mode(self) -> bool:
        return self.mode == "socket"

    @property
    def bot_user_id(self) -> str | None:
        """Current bot user ID (``U_BOT_xxx``) used for mention detection."""

        return self._bot_user_id

    # ---------------------------------------------------------- thread id API

    def encode_thread_id(self, platform_data: SlackThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> SlackThreadId:
        return decode_thread_id(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        return channel_id_from_thread_id(thread_id)

    def is_dm(self, thread_id: str) -> bool:
        return is_dm_thread_id(thread_id)

    def get_channel_visibility(
        self, thread_id: str
    ) -> Literal["external", "private", "workspace", "unknown"]:
        """Resolve channel visibility from the thread ID prefix / cache.

        - ``external``: Slack Connect channel (tracked via webhook payloads)
        - ``private``: Private channel (``G``) or DM (``D``)
        - ``workspace``: Public channel (``C``)
        - ``unknown``: Fallback when the channel prefix is non-standard
        """

        channel = self.decode_thread_id(thread_id)["channel"]
        if channel in self._external_channels:
            return "external"
        if channel.startswith(("G", "D")):
            return "private"
        if channel.startswith("C"):
            return "workspace"
        return "unknown"

    # -------------------------------------------------------- token accessors

    def _get_token(self) -> str:
        """Resolve the bot token to use for Slack Web API calls.

        Raises :class:`AuthenticationError` if neither a request-context token
        nor a default bot token is configured.
        """

        if self._default_bot_token:
            return self._default_bot_token
        raise AuthenticationError(
            "slack",
            "No bot token available. Configure botToken for single-workspace "
            "mode or install the app per-workspace for multi-workspace mode.",
        )

    # ----------------------------------------------------- signature helpers

    def verify_signature(
        self,
        body: str,
        timestamp: str | None,
        signature: str | None,
    ) -> bool:
        """Instance wrapper around :func:`verify_signature`."""

        return verify_signature(body, timestamp, signature, self.signing_secret)

    # ------------------------------------------------------------------ lifecycle

    async def initialize(self, chat: Any) -> None:
        """Store the :class:`Chat` reference for dispatch.

        Called by :meth:`Chat._do_initialize` once per adapter. Socket-mode
        connects on initialize; webhook mode is a no-op aside from wiring.
        """

        self._chat = chat
        # Socket mode is lit up in Phase 2 — this stub keeps webhook-mode callers
        # from hitting AttributeError until then.

    async def disconnect(self) -> None:
        """Tear down any long-lived connections (Socket Mode in Phase 2)."""

        return None

    # ------------------------------------------------------------------ webhook

    async def handle_webhook(
        self,
        body: bytes,
        headers: dict[str, str],
        options: Any | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Handle an inbound Slack webhook request.

        Mirrors upstream ``handleWebhook`` in ``adapter-slack/src/index.ts``:

        1. Verify the request signature.
        2. Branch on body shape — JSON envelope vs. ``x-www-form-urlencoded``
           interactivity / slash-command payloads.
        3. Dispatch via :meth:`_dispatch_envelope` (shared with Phase 2 Socket
           Mode).
        """

        # Normalize header keys to lowercase (Slack uses ``x-slack-*`` but some
        # framework glue capitalizes them).
        h = {k.lower(): v for k, v in headers.items()}
        timestamp = h.get("x-slack-request-timestamp")
        signature = h.get("x-slack-signature")
        content_type = (h.get("content-type") or "").split(";", 1)[0].strip()

        body_str = body.decode("utf-8") if isinstance(body, bytes | bytearray) else str(body)

        if not self.verify_signature(body_str, timestamp, signature):
            self.logger.warn("Rejected Slack webhook: signature mismatch or stale timestamp")
            return (401, {}, b"")

        # Parse payload — interactivity / slash commands arrive as form-urlencoded.
        payload: dict[str, Any]
        if content_type == "application/x-www-form-urlencoded":
            form = parse_qs(body_str, keep_blank_values=True)
            flat = {k: v[0] if v else "" for k, v in form.items()}
            if "payload" in flat:
                # Interactivity (block_actions / view_submission / view_closed /
                # shortcut / message_action / block_suggestion).
                try:
                    payload = json.loads(flat["payload"])
                except json.JSONDecodeError as err:
                    self.logger.error("Invalid interactivity payload", {"error": err})
                    return (400, {}, b"")
                payload["_kind"] = "interactivity"
            else:
                # Slash command.
                payload = dict(flat)
                payload["_kind"] = "slash_command"
        else:
            try:
                payload = json.loads(body_str)
            except json.JSONDecodeError as err:
                self.logger.error("Invalid Slack webhook body", {"error": err})
                return (400, {}, b"")

        # URL verification handshake — return the challenge verbatim.
        if payload.get("type") == "url_verification":
            return (
                200,
                {"content-type": "application/json"},
                json.dumps({"challenge": payload.get("challenge", "")}).encode("utf-8"),
            )

        return await self._dispatch_envelope(payload, options)

    async def _dispatch_envelope(
        self,
        payload: dict[str, Any],
        options: Any | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Route a parsed Slack envelope to the appropriate handler.

        Shared between webhook (Phase 1) and Socket Mode (Phase 2). Returns a
        ``(status, headers, body)`` tuple matching the HTTP response Slack
        expects for webhook delivery; Socket Mode ignores the body and just
        ack's the envelope.
        """

        kind = payload.get("_kind")

        # --- interactivity -------------------------------------------------
        if kind == "interactivity":
            itype = payload.get("type")
            if itype == "block_actions":
                await self._handle_block_actions(payload, options)
                return (200, {}, b"")
            if itype == "view_submission":
                return await self._handle_view_submission(payload, options)
            if itype == "view_closed":
                await self._handle_view_closed(payload, options)
                return (200, {}, b"")
            if itype == "block_suggestion":
                # Phase-1 stub: return empty options set.
                return (
                    200,
                    {"content-type": "application/json"},
                    json.dumps({"options": []}).encode("utf-8"),
                )
            # Other interactivity shapes (message_action, shortcut) fall through
            # as 200 no-ops for now — not part of the Phase-1 critical surface.
            return (200, {}, b"")

        # --- slash command -------------------------------------------------
        if kind == "slash_command":
            await self._handle_slash_command(payload, options)
            return (200, {}, b"")

        # --- Events API ----------------------------------------------------
        if payload.get("type") == "event_callback":
            event = payload.get("event") or {}
            event_type = event.get("type")
            if event_type == "app_mention":
                await self._handle_message_event(event, is_mention=True, options=options)
                return (200, {}, b"")
            if event_type == "message":
                await self._handle_message_event(event, is_mention=False, options=options)
                return (200, {}, b"")
            if event_type in ("reaction_added", "reaction_removed"):
                await self._handle_reaction_event(event, options=options)
                return (200, {}, b"")
            if event_type == "assistant_thread_started":
                if self._chat is not None:
                    self._chat.process_assistant_thread_started(event, options)
                return (200, {}, b"")
            if event_type == "assistant_context_changed":
                if self._chat is not None:
                    self._chat.process_assistant_context_changed(event, options)
                return (200, {}, b"")
            if event_type == "app_home_opened":
                if self._chat is not None:
                    self._chat.process_app_home_opened(event, options)
                return (200, {}, b"")
            if event_type == "member_joined_channel":
                if self._chat is not None:
                    self._chat.process_member_joined_channel(event, options)
                return (200, {}, b"")

        # Unknown / unhandled — 200 so Slack doesn't retry.
        return (200, {}, b"")

    # ----------------------------------------------- inbound event builders

    def _author_from_event(self, event: dict[str, Any]) -> Any:
        """Build a chat ``Author`` from a Slack event.

        We can't hit ``users.info`` inline (3-second timeout budget), so we
        build a minimal ``Author`` from the fields present on the event. The
        ``is_me`` check compares against the adapter's ``bot_user_id``.
        """

        from chat.types import Author

        user_id = event.get("user") or event.get("bot_id") or ""
        user_name = event.get("username") or user_id
        is_me = bool(self._bot_user_id and user_id == self._bot_user_id)
        is_bot = bool(event.get("bot_id"))
        return Author(
            user_id=user_id,
            user_name=user_name,
            full_name=user_name,
            is_bot=is_bot,
            is_me=is_me,
        )

    def _build_message(self, event: dict[str, Any], thread_id: str) -> Any:
        """Build a chat :class:`Message` from a Slack message event."""

        from chat.markdown import parse_markdown
        from chat.message import Message
        from chat.types import MessageMetadata

        text = event.get("text", "") or ""
        ts = event.get("ts", "")
        date_sent = datetime.now(UTC)
        if ts:
            with contextlib.suppress(TypeError, ValueError):
                date_sent = datetime.fromtimestamp(float(ts), tz=UTC)

        formatted = parse_markdown(text)
        author = self._author_from_event(event)
        return Message(
            id=ts,
            thread_id=thread_id,
            text=text,
            formatted=formatted,
            raw=event,
            author=author,
            metadata=MessageMetadata(date_sent=date_sent, edited=False),
        )

    async def _handle_message_event(
        self,
        event: dict[str, Any],
        *,
        is_mention: bool,
        options: Any | None,
    ) -> None:
        if self._chat is None:
            return

        # Skip bot's own messages / edits / deletes.
        subtype = event.get("subtype")
        if subtype in ("message_changed", "message_deleted", "channel_join", "channel_leave"):
            return
        bot_id = event.get("bot_id")
        if bot_id and self._bot_id and bot_id == self._bot_id:
            return

        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        thread_id = encode_thread_id({"channel": channel, "threadTs": thread_ts})

        message = self._build_message(event, thread_id)
        if is_mention:
            message.is_mention = True

        self._chat.process_message(self, thread_id, message, options)

    async def _handle_reaction_event(
        self,
        event: dict[str, Any],
        *,
        options: Any | None,
    ) -> None:
        if self._chat is None:
            return

        from chat.emoji import default_emoji_resolver
        from chat.types import Author

        item = event.get("item") or {}
        channel = item.get("channel", "")
        message_ts = item.get("ts", "")
        thread_id = encode_thread_id({"channel": channel, "threadTs": message_ts})

        raw_emoji = event.get("reaction", "")
        emoji = default_emoji_resolver.from_slack(raw_emoji)
        user_id = event.get("user", "")
        is_me = bool(self._bot_user_id and user_id == self._bot_user_id)
        author = Author(
            user_id=user_id,
            user_name=user_id,
            full_name=user_id,
            is_bot=False,
            is_me=is_me,
        )

        reaction_event: dict[str, Any] = {
            "adapter": self,
            "emoji": emoji,
            "rawEmoji": raw_emoji,
            "added": event.get("type") == "reaction_added",
            "threadId": thread_id,
            "messageId": message_ts,
            "user": author,
            "raw": event,
        }
        self._chat.process_reaction(reaction_event, options)

    async def _handle_block_actions(
        self,
        payload: dict[str, Any],
        options: Any | None,
    ) -> None:
        if self._chat is None:
            return

        from chat.types import Author

        user = payload.get("user") or {}
        user_id = user.get("id", "")
        is_me = bool(self._bot_user_id and user_id == self._bot_user_id)
        author = Author(
            user_id=user_id,
            user_name=user.get("username") or user.get("name") or user_id,
            full_name=user.get("name") or user_id,
            is_bot=False,
            is_me=is_me,
        )

        actions = payload.get("actions") or []
        channel = (payload.get("channel") or {}).get("id", "")
        message = payload.get("message") or {}
        thread_ts = message.get("thread_ts") or message.get("ts", "")
        thread_id = encode_thread_id({"channel": channel, "threadTs": thread_ts}) if channel else ""
        trigger_id = payload.get("trigger_id")

        for action in actions:
            action_event: dict[str, Any] = {
                "adapter": self,
                "actionId": action.get("action_id", ""),
                "value": action.get("value"),
                "triggerId": trigger_id,
                "threadId": thread_id or None,
                "messageId": message.get("ts"),
                "user": author,
                "raw": payload,
            }
            self._chat.process_action(action_event, options)

    async def _handle_view_submission(
        self,
        payload: dict[str, Any],
        options: Any | None,
    ) -> tuple[int, dict[str, str], bytes]:
        if self._chat is None:
            return (200, {}, b"")

        from chat.types import Author

        view = payload.get("view") or {}
        callback_id = view.get("callback_id", "")
        values = ((view.get("state") or {}).get("values")) or {}

        user = payload.get("user") or {}
        user_id = user.get("id", "")
        author = Author(
            user_id=user_id,
            user_name=user.get("username") or user.get("name") or user_id,
            full_name=user.get("name") or user_id,
            is_bot=False,
            is_me=False,
        )

        # Flatten form values into a simple ``{action_id: value}`` dict.
        flat_values: dict[str, Any] = {}
        for _block_id, block_values in values.items():
            if not isinstance(block_values, dict):
                continue
            for action_id, action_state in block_values.items():
                if not isinstance(action_state, dict):
                    continue
                atype = action_state.get("type")
                if atype == "plain_text_input":
                    flat_values[action_id] = action_state.get("value")
                elif atype in ("static_select", "external_select", "radio_buttons"):
                    selected = action_state.get("selected_option") or {}
                    flat_values[action_id] = selected.get("value")
                elif atype == "multi_static_select":
                    flat_values[action_id] = [
                        opt.get("value") for opt in action_state.get("selected_options") or []
                    ]
                elif atype in ("datepicker", "timepicker"):
                    flat_values[action_id] = action_state.get("selected_date") or action_state.get(
                        "selected_time"
                    )
                elif atype == "checkboxes":
                    flat_values[action_id] = [
                        opt.get("value") for opt in action_state.get("selected_options") or []
                    ]
                else:
                    flat_values[action_id] = action_state.get("value")

        from .modals import decode_modal_metadata

        context = decode_modal_metadata(view.get("private_metadata"))
        context_id = context.get("contextId") if isinstance(context, dict) else None

        event: dict[str, Any] = {
            "adapter": self,
            "callbackId": callback_id,
            "values": flat_values,
            "user": author,
            "raw": payload,
            "viewId": view.get("id"),
        }
        response = await self._chat.process_modal_submit(event, context_id, options)

        if response:
            # Handlers may return a Slack-shaped view update; echo as JSON.
            return (
                200,
                {"content-type": "application/json"},
                json.dumps(response).encode("utf-8"),
            )
        return (
            200,
            {"content-type": "application/json"},
            json.dumps({"response_action": "clear"}).encode("utf-8"),
        )

    async def _handle_view_closed(
        self,
        payload: dict[str, Any],
        options: Any | None,
    ) -> None:
        if self._chat is None:
            return

        from chat.types import Author

        view = payload.get("view") or {}
        user = payload.get("user") or {}
        user_id = user.get("id", "")
        author = Author(
            user_id=user_id,
            user_name=user.get("username") or user.get("name") or user_id,
            full_name=user.get("name") or user_id,
            is_bot=False,
            is_me=False,
        )

        from .modals import decode_modal_metadata

        context = decode_modal_metadata(view.get("private_metadata"))
        context_id = context.get("contextId") if isinstance(context, dict) else None

        event: dict[str, Any] = {
            "adapter": self,
            "callbackId": view.get("callback_id", ""),
            "user": author,
            "raw": payload,
        }
        self._chat.process_modal_close(event, context_id, options)

    async def _handle_slash_command(
        self,
        payload: dict[str, Any],
        options: Any | None,
    ) -> None:
        if self._chat is None:
            return

        from chat.types import Author

        user_id = payload.get("user_id", "")
        is_me = bool(self._bot_user_id and user_id == self._bot_user_id)
        author = Author(
            user_id=user_id,
            user_name=payload.get("user_name", user_id) or user_id,
            full_name=payload.get("user_name", user_id) or user_id,
            is_bot=False,
            is_me=is_me,
        )
        channel = payload.get("channel_id", "")
        channel_id_full = f"slack:{channel}" if channel else ""

        event: dict[str, Any] = {
            "adapter": self,
            "command": payload.get("command", ""),
            "text": payload.get("text", ""),
            "channelId": channel_id_full,
            "triggerId": payload.get("trigger_id"),
            "user": author,
            "raw": payload,
        }
        self._chat.process_slash_command(event, options)

    # ------------------------------------------------------------------ outbound

    async def post_message(self, thread_id: str, message: Any) -> dict[str, Any]:
        """Post a message to a Slack thread / channel.

        Accepts the :class:`AdapterPostableMessage` union (str / PostableRaw /
        PostableMarkdown / PostableAst / card). Returns a :class:`RawMessage`
        dict.
        """

        decoded = decode_thread_id(thread_id)
        channel = decoded["channel"]
        thread_ts = decoded.get("threadTs") or None

        text, blocks = self._postable_to_slack(message)

        try:
            response = await self.client.chat_postMessage(
                channel=channel,
                text=text or " ",
                blocks=blocks,
                thread_ts=thread_ts,
            )
        except Exception as err:  # pragma: no cover - re-raised below
            self._translate_slack_error(err)
            raise

        response_data = response.get("message") if isinstance(response, dict) else None
        ts = (response.get("ts") if isinstance(response, dict) else None) or (
            response_data.get("ts") if isinstance(response_data, dict) else None
        )
        result_channel = response.get("channel") if isinstance(response, dict) else channel
        returned_thread_ts = thread_ts or ts
        result_thread_id = encode_thread_id(
            {"channel": result_channel or channel, "threadTs": returned_thread_ts or ""}
        )
        return {
            "id": ts or "",
            "raw": response_data or response,
            "threadId": result_thread_id,
        }

    async def edit_message(
        self,
        thread_id: str,
        message_id: str,
        message: Any,
    ) -> dict[str, Any]:
        """Edit an existing Slack message via ``chat.update``."""

        decoded = decode_thread_id(thread_id)
        channel = decoded["channel"]
        text, blocks = self._postable_to_slack(message)

        try:
            response = await self.client.chat_update(
                channel=channel,
                ts=message_id,
                text=text or " ",
                blocks=blocks,
            )
        except Exception as err:  # pragma: no cover - re-raised below
            self._translate_slack_error(err)
            raise

        return {
            "id": message_id,
            "raw": response,
            "threadId": thread_id,
        }

    async def delete_message(self, thread_id: str, message_id: str) -> None:
        """Delete a Slack message via ``chat.delete``."""

        decoded = decode_thread_id(thread_id)
        try:
            await self.client.chat_delete(channel=decoded["channel"], ts=message_id)
        except Exception as err:
            self._translate_slack_error(err)
            raise

    async def add_reaction(
        self,
        thread_id: str,
        message_id: str,
        emoji: str,
    ) -> None:
        """Add a reaction to a Slack message via ``reactions.add``."""

        from chat.emoji import default_emoji_resolver

        decoded = decode_thread_id(thread_id)
        slack_emoji = default_emoji_resolver.to_slack(emoji)
        try:
            await self.client.reactions_add(
                channel=decoded["channel"],
                timestamp=message_id,
                name=slack_emoji,
            )
        except Exception as err:
            self._translate_slack_error(err)
            raise

    async def remove_reaction(
        self,
        thread_id: str,
        message_id: str,
        emoji: str,
    ) -> None:
        """Remove a reaction from a Slack message via ``reactions.remove``."""

        from chat.emoji import default_emoji_resolver

        decoded = decode_thread_id(thread_id)
        slack_emoji = default_emoji_resolver.to_slack(emoji)
        try:
            await self.client.reactions_remove(
                channel=decoded["channel"],
                timestamp=message_id,
                name=slack_emoji,
            )
        except Exception as err:
            self._translate_slack_error(err)
            raise

    async def post_channel_message(
        self,
        channel_id: str,
        message: Any,
    ) -> dict[str, Any]:
        """Post a top-level (non-threaded) message to a Slack channel."""

        # ``channel_id`` is ``slack:{channel}`` at the adapter boundary.
        parts = channel_id.split(":", 1)
        channel = parts[1] if len(parts) == 2 else channel_id
        thread_id = encode_thread_id({"channel": channel, "threadTs": ""})
        return await self.post_message(thread_id, message)

    async def fetch_messages(
        self,
        thread_id: str,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Fetch messages from a Slack thread via ``conversations.replies``."""

        decoded = decode_thread_id(thread_id)
        limit = None
        cursor = None
        if isinstance(options, dict):
            limit = options.get("limit")
            cursor = options.get("cursor")
        kwargs: dict[str, Any] = {
            "channel": decoded["channel"],
            "ts": decoded.get("threadTs") or "",
        }
        if limit is not None:
            kwargs["limit"] = limit
        if cursor is not None:
            kwargs["cursor"] = cursor
        try:
            response = await self.client.conversations_replies(**kwargs)
        except Exception as err:
            self._translate_slack_error(err)
            raise
        return {
            "messages": response.get("messages", []),
            "nextCursor": (response.get("response_metadata") or {}).get("next_cursor") or None,
        }

    async def fetch_channel_info(self, channel_id: str) -> dict[str, Any]:
        """Fetch Slack channel info via ``conversations.info``."""

        parts = channel_id.split(":", 1)
        channel = parts[1] if len(parts) == 2 else channel_id
        try:
            response = await self.client.conversations_info(channel=channel)
        except Exception as err:
            self._translate_slack_error(err)
            raise
        info = response.get("channel") or {}
        return {
            "id": channel_id,
            "name": info.get("name") or f"#{channel}",
            "isDM": bool(info.get("is_im")),
            "metadata": info,
        }

    async def fetch_channel_messages(
        self,
        channel_id: str,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Fetch the top-level history for a channel via ``conversations.history``."""

        parts = channel_id.split(":", 1)
        channel = parts[1] if len(parts) == 2 else channel_id
        kwargs: dict[str, Any] = {"channel": channel}
        if isinstance(options, dict):
            if options.get("limit") is not None:
                kwargs["limit"] = options["limit"]
            if options.get("cursor") is not None:
                kwargs["cursor"] = options["cursor"]
        try:
            response = await self.client.conversations_history(**kwargs)
        except Exception as err:
            self._translate_slack_error(err)
            raise
        return {
            "messages": response.get("messages", []),
            "nextCursor": (response.get("response_metadata") or {}).get("next_cursor") or None,
        }

    async def list_threads(
        self,
        channel_id: str,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Enumerate threads in a channel. Slack has no first-class endpoint —
        upstream lists history and filters messages with ``reply_count > 0``.
        """

        result = await self.fetch_channel_messages(channel_id, options)
        threads = [m for m in result.get("messages", []) if m.get("reply_count")]
        return {
            "threads": threads,
            "nextCursor": result.get("nextCursor"),
        }

    async def subscribe(self, thread_id: str) -> None:
        """Subscribe the bot to a thread — no-op on Slack (subscription is
        implicit via message posting + ``conversations.mark``).
        """

        return None

    async def unsubscribe(self, thread_id: str) -> None:
        """Unsubscribe — no-op on Slack (same reasoning as :meth:`subscribe`)."""

        return None

    async def open_dm(self, user_id: str) -> str:
        """Open a DM conversation with ``user_id`` and return the thread ID."""

        try:
            response = await self.client.conversations_open(users=user_id)
        except Exception as err:
            self._translate_slack_error(err)
            raise
        channel = (response.get("channel") or {}).get("id", "")
        return encode_thread_id({"channel": channel, "threadTs": ""})

    async def open_modal(
        self,
        trigger_id: str,
        view: Any,
        context_id: str | None = None,
    ) -> Any:
        """Open a Slack modal via ``views.open``."""

        from .modals import encode_modal_metadata, modal_to_slack_view

        slack_view = modal_to_slack_view(view)
        if context_id is not None:
            meta_raw = encode_modal_metadata({"contextId": context_id})
            if meta_raw is not None:
                slack_view["private_metadata"] = meta_raw

        try:
            return await self.client.views_open(trigger_id=trigger_id, view=slack_view)
        except Exception as err:
            self._translate_slack_error(err)
            raise

    async def start_typing(self, thread_id: str) -> None:
        """Slack has no public typing-indicator API in the Web API — no-op.

        Socket-mode / RTM can send ``typing`` events but the Bolt / Events API
        surface used here doesn't expose it. Upstream is also a no-op.
        """

        return None

    async def stream(
        self,
        thread_id: str,
        chunks: AsyncIterable[str],
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Stream ``chunks`` to a Slack message, editing it periodically.

        Mirrors upstream ``StreamingMarkdownRenderer`` semantics:

        1. Post an initial placeholder message (the returned ``ts``).
        2. Accumulate chunks into a buffer. Every ``streaming_update_interval_ms``
           (default 500ms via chat config, 500ms here) update the message.
        3. On stream close, send the final update with the accumulated text.
        """

        interval_ms = 500
        if isinstance(options, dict) and options.get("streamingUpdateIntervalMs") is not None:
            interval_ms = int(options["streamingUpdateIntervalMs"])
        placeholder = "..."
        if isinstance(options, dict) and options.get("placeholder") is not None:
            placeholder = str(options["placeholder"])

        # Step 1: post placeholder.
        initial = await self.post_message(thread_id, {"markdown": placeholder})
        message_id = initial["id"]

        accumulated = ""
        last_update = time.monotonic()
        interval_s = max(interval_ms, 1) / 1000.0

        try:
            async for chunk in chunks:
                accumulated += chunk
                now = time.monotonic()
                if now - last_update >= interval_s:
                    await self.edit_message(
                        thread_id, message_id, {"markdown": accumulated or placeholder}
                    )
                    last_update = now
        except asyncio.CancelledError:
            raise
        # Final update with the complete text.
        final = await self.edit_message(
            thread_id, message_id, {"markdown": accumulated or placeholder}
        )
        # Preserve the message ID from the original post.
        final["id"] = message_id
        return final

    # ------------------------------------------------------------------ helpers

    def _postable_to_slack(self, message: Any) -> tuple[str, list[dict[str, Any]] | None]:
        """Convert an :class:`AdapterPostableMessage` to ``(text, blocks)``.

        ``blocks`` is ``None`` when the message is plain mrkdwn (Slack accepts
        ``text`` alone). Cards map to Block Kit via :func:`card_to_block_kit`;
        markdown / ast inputs are rendered via :class:`SlackFormatConverter`.
        """

        # Plain string → mrkdwn text.
        if isinstance(message, str):
            return self.format_converter.render_postable(message), None

        if not isinstance(message, dict):
            return str(message), None

        # Card element (has ``children`` and card-shaped metadata).
        if "children" in message and any(k in message for k in ("title", "subtitle", "imageUrl")):
            from .cards import card_to_block_kit, card_to_fallback_text

            blocks = card_to_block_kit(message)
            text = card_to_fallback_text(message)
            return text, blocks

        # Raw / markdown / AST postable.
        if "raw" in message:
            return str(message["raw"]), None
        if "markdown" in message:
            rendered = self.format_converter.render_postable({"markdown": message["markdown"]})
            return rendered, None
        if "ast" in message:
            rendered = self.format_converter.render_postable({"ast": message["ast"]})
            return rendered, None

        # Fallback — just JSON-ify. Upstream raises here but we stay lenient.
        return json.dumps(message), None

    def _translate_slack_error(self, err: Exception) -> None:
        """Translate a :class:`slack_sdk.errors.SlackApiError` to chat errors.

        Raises :class:`RateLimitError` / :class:`AuthenticationError` /
        :class:`ChatError`. No-ops on non-Slack errors — the caller re-raises.
        """

        from chat.errors import ChatError, RateLimitError

        # Lazy import — slack_sdk is an optional dep.
        try:
            from slack_sdk.errors import SlackApiError
        except ImportError:  # pragma: no cover
            return

        if not isinstance(err, SlackApiError):
            return

        response = getattr(err, "response", None)
        slack_error = ""
        if response is not None:
            slack_error = (
                response.get("error")
                if isinstance(response, dict)
                else getattr(response, "data", {}).get("error", "")
            ) or ""

        if slack_error == "ratelimited":
            retry_after = None
            headers = (
                response.get("headers")
                if isinstance(response, dict)
                else getattr(response, "headers", {})
            ) or {}
            retry_header = headers.get("Retry-After") or headers.get("retry-after")
            if retry_header:
                with contextlib.suppress(TypeError, ValueError):
                    retry_after = int(retry_header) * 1000
            raise RateLimitError(
                f"Slack rate limit hit: {slack_error}",
                retry_after_ms=retry_after,
                cause=err,
            ) from err

        if slack_error in ("invalid_auth", "not_authed", "token_revoked", "account_inactive"):
            raise AuthenticationError("slack", f"Slack auth failed: {slack_error}") from err

        # Fallback — wrap in a generic ChatError so callers don't leak the
        # slack_sdk exception type (mirrors upstream behavior).
        raise ChatError(
            f"Slack API error: {slack_error or str(err)}", "SLACK_API_ERROR", cause=err
        ) from err


# Keep the unused import warning at bay for ``AdapterRateLimitError`` — exported
# for compatibility with adapter-shared consumers that reach in for this symbol.
_ = AdapterRateLimitError


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_slack_adapter(
    config: SlackAdapterConfig | None = None,
) -> SlackAdapter:
    """Create a :class:`SlackAdapter` with env-var fallbacks filled in.

    Mirrors upstream's ``createSlackAdapter`` logic: socket-mode requires an
    app-token, webhook-mode requires a signing secret, and the OAuth
    credentials are validated against the selected mode.
    """

    cfg: SlackAdapterConfig = dict(config or {})  # type: ignore[assignment]
    mode: SlackAdapterMode = cfg.get("mode", "webhook")
    app_token = cfg.get("appToken") or os.environ.get("SLACK_APP_TOKEN")

    if mode == "socket":
        if not app_token:
            raise ValidationError(
                "slack",
                "appToken is required for socket mode. Set SLACK_APP_TOKEN or "
                "provide it in config.",
            )
        if cfg.get("clientId") or cfg.get("clientSecret"):
            raise ValidationError(
                "slack",
                "Multi-workspace (clientId/clientSecret) is not supported in socket mode.",
            )

    signing_secret = cfg.get("signingSecret") or os.environ.get("SLACK_SIGNING_SECRET")
    if mode == "webhook" and not signing_secret:
        raise ValidationError(
            "slack",
            "signingSecret is required. Set SLACK_SIGNING_SECRET or provide it in config.",
        )

    zero_config = config is None

    resolved: SlackAdapterConfig = {
        "mode": mode,
    }
    if app_token is not None:
        resolved["appToken"] = app_token
    if signing_secret is not None:
        resolved["signingSecret"] = signing_secret
    bot_token = cfg.get("botToken") or (os.environ.get("SLACK_BOT_TOKEN") if zero_config else None)
    if bot_token is not None:
        resolved["botToken"] = bot_token
    client_id = cfg.get("clientId") or (os.environ.get("SLACK_CLIENT_ID") if zero_config else None)
    if client_id is not None:
        resolved["clientId"] = client_id
    client_secret = cfg.get("clientSecret") or (
        os.environ.get("SLACK_CLIENT_SECRET") if zero_config else None
    )
    if client_secret is not None:
        resolved["clientSecret"] = client_secret
    encryption_key = cfg.get("encryptionKey") or os.environ.get("SLACK_ENCRYPTION_KEY")
    if encryption_key is not None:
        resolved["encryptionKey"] = encryption_key
    if "installationKeyPrefix" in cfg:
        resolved["installationKeyPrefix"] = cfg["installationKeyPrefix"]
    if "logger" in cfg:
        resolved["logger"] = cfg["logger"]
    if "userName" in cfg:
        resolved["userName"] = cfg["userName"]
    if "botUserId" in cfg:
        resolved["botUserId"] = cfg["botUserId"]
    if "apiUrl" in cfg:
        resolved["apiUrl"] = cfg["apiUrl"]
    socket_forwarding_secret = cfg.get("socketForwardingSecret") or os.environ.get(
        "SLACK_SOCKET_FORWARDING_SECRET"
    )
    if socket_forwarding_secret is not None:
        resolved["socketForwardingSecret"] = socket_forwarding_secret

    return SlackAdapter(resolved)


__all__ = [
    "OPTIONS_LOAD_TIMEOUT_MS",
    "SlackAdapter",
    "SlackAdapterConfig",
    "SlackAdapterMode",
    "SlackEvent",
    "SlackInstallation",
    "SlackOAuthCallbackOptions",
    "SlackReactionEvent",
    "SlackThreadId",
    "channel_id_from_thread_id",
    "create_slack_adapter",
    "decode_thread_id",
    "encode_thread_id",
    "is_dm_thread_id",
    "parse_slack_message_url",
    "verify_signature",
]
