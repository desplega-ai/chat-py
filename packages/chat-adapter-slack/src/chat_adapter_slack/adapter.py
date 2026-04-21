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

import hashlib
import hmac
import os
import re
import time
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from chat_adapter_shared import (
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
