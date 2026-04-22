"""Discord adapter for chat-py.

Python port of upstream ``packages/adapter-discord/src/index.ts``.

Uses Discord's HTTP Interactions API for serverless compatibility — webhook
signature verification uses Ed25519 via :mod:`nacl.signing`. The scope of this
port is the webhook path: PING / MessageComponent / ApplicationCommand
interactions plus forwarded Gateway events (``x-discord-gateway-token`` header).
The discord.js Gateway client is intentionally not ported; operators that
need persistent socket listening should forward Gateway events back through
the same webhook endpoint.
"""

from __future__ import annotations

import contextvars
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast
from urllib.parse import quote as urlquote

import httpx
from chat_adapter_shared import (
    NetworkError,
    ValidationError,
    extract_card,
    extract_files,
    to_buffer,
)
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .cards import card_to_discord_payload
from .errors import handle_discord_error
from .markdown import DiscordFormatConverter
from .thread_id import (
    DiscordThreadId,
    decode_thread_id,
    encode_thread_id,
    is_dm,
)
from .thread_id import (
    channel_id_from_thread_id as _channel_id_from_thread_id,
)

if TYPE_CHECKING:
    from chat import Logger, Message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_MAX_CONTENT_LENGTH = 2000
_HEX_64_PATTERN = "0123456789abcdef"

# Discord interaction types.
INTERACTION_TYPE_PING = 1
INTERACTION_TYPE_APPLICATION_COMMAND = 2
INTERACTION_TYPE_MESSAGE_COMPONENT = 3

# Discord interaction response types.
RESPONSE_TYPE_PONG = 1
RESPONSE_TYPE_CHANNEL_MESSAGE_WITH_SOURCE = 4
RESPONSE_TYPE_DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
RESPONSE_TYPE_DEFERRED_UPDATE_MESSAGE = 6
RESPONSE_TYPE_UPDATE_MESSAGE = 7

# Discord channel types.
CHANNEL_TYPE_GUILD_TEXT = 0
CHANNEL_TYPE_DM = 1
CHANNEL_TYPE_GROUP_DM = 3
CHANNEL_TYPE_GUILD_PUBLIC_THREAD = 11
CHANNEL_TYPE_GUILD_PRIVATE_THREAD = 12

_THREAD_PARENT_CACHE_TTL_SECONDS = 5 * 60

_EMOJI_NAME_MAP: dict[str, str] = {
    "\U0001f44d": "thumbs_up",
    "\U0001f44e": "thumbs_down",
    "\u2764\ufe0f": "heart",
    "\u2764": "heart",
    "\U0001f525": "fire",
    "\U0001f680": "rocket",
    "\U0001f64c": "raised_hands",
    "\u2705": "check",
    "\u274c": "x",
    "\U0001f44b": "wave",
    "\U0001f914": "thinking",
    "\U0001f60a": "smile",
    "\U0001f602": "laugh",
    "\U0001f389": "party",
    "\u2b50": "star",
    "\u2728": "sparkles",
    "\U0001f440": "eyes",
    "\U0001f4af": "100",
}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class DiscordAdapterConfig(TypedDict, total=False):
    """Config for :class:`DiscordAdapter` / :func:`create_discord_adapter`."""

    apiUrl: str
    applicationId: str
    botToken: str
    logger: Logger
    mentionRoleIds: list[str]
    publicKey: str
    userName: str


@dataclass(slots=True)
class DiscordSlashCommandContext:
    """Per-request slash command context used while resolving deferred responses."""

    channel_id: str
    interaction_token: str
    initial_response_sent: bool = False


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_discord_signature(
    public_key_hex: str,
    signature_hex: str | None,
    timestamp: str | None,
    body: bytes,
) -> bool:
    """Verify a Discord Ed25519 webhook signature.

    Mirrors ``discord-interactions``'s ``verifyKey`` — concatenates the
    ``timestamp`` header bytes with the raw body bytes and verifies against
    the hex-encoded signature using :class:`nacl.signing.VerifyKey`.

    Returns ``False`` on any error (missing headers, bad hex, bad signature).
    Never raises.
    """

    if not (signature_hex and timestamp):
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        signed = timestamp.encode("utf-8") + body
        verify_key.verify(signed, bytes.fromhex(signature_hex))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


# ---------------------------------------------------------------------------
# DiscordAdapter
# ---------------------------------------------------------------------------


class DiscordAdapter:
    """Discord platform adapter (HTTP Interactions path).

    Signature-verifies incoming webhooks and translates PING / MessageComponent /
    ApplicationCommand interactions as well as forwarded Gateway events. REST
    operations (post, edit, delete, reactions, typing, fetch_*) go through
    :mod:`httpx` against Discord's REST API.
    """

    name = "discord"
    lock_scope: Literal["thread", "channel"] | None = "thread"
    persist_message_history: bool = False

    def __init__(self, config: DiscordAdapterConfig | None = None) -> None:
        cfg: dict[str, Any] = dict(config or {})

        bot_token = cfg.get("botToken") or os.environ.get("DISCORD_BOT_TOKEN")
        if not bot_token:
            raise ValidationError(
                "discord",
                "botToken is required. Set DISCORD_BOT_TOKEN or provide it in config.",
            )
        public_key = cfg.get("publicKey") or os.environ.get("DISCORD_PUBLIC_KEY")
        if not public_key:
            raise ValidationError(
                "discord",
                "publicKey is required. Set DISCORD_PUBLIC_KEY or provide it in config.",
            )
        application_id = cfg.get("applicationId") or os.environ.get("DISCORD_APPLICATION_ID")
        if not application_id:
            raise ValidationError(
                "discord",
                "applicationId is required. Set DISCORD_APPLICATION_ID or provide it in config.",
            )

        self.bot_token: str = str(bot_token)
        self.public_key: str = str(public_key).strip().lower()
        self.application_id: str = str(application_id)
        self.api_base_url: str = (
            cfg.get("apiUrl") or os.environ.get("DISCORD_API_URL") or DISCORD_API_BASE
        )

        raw_role_ids = cfg.get("mentionRoleIds")
        if raw_role_ids is None:
            env_roles = os.environ.get("DISCORD_MENTION_ROLE_IDS", "")
            self.mention_role_ids: list[str] = (
                [r.strip() for r in env_roles.split(",") if r.strip()] if env_roles else []
            )
        else:
            self.mention_role_ids = list(raw_role_ids)

        self.bot_user_id: str = self.application_id
        self.user_name: str = cfg.get("userName") or "bot"

        logger = cfg.get("logger")
        if logger is None:
            from chat import ConsoleLogger

            logger = ConsoleLogger("info").child("discord")
        self.logger: Logger = logger

        if not _is_hex_64(self.public_key):
            self.logger.error(
                "Invalid Discord public key format",
                {"length": len(self.public_key)},
            )

        self.format_converter = DiscordFormatConverter()
        self._http_client: httpx.AsyncClient | None = None
        self._thread_parent_cache: dict[str, tuple[str, float]] = {}
        self._request_context: contextvars.ContextVar[DiscordSlashCommandContext | None] = (
            contextvars.ContextVar("discord_request_context", default=None)
        )
        self._chat: Any = None

    # ---------------------------------------------------------------- init

    async def initialize(self, chat: Any) -> None:
        self._chat = chat
        self.logger.info("Discord adapter initialized")

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def disconnect(self) -> None:
        """Release the HTTP client if one was built. Alias for :meth:`close`."""

        await self.close()

    # --------------------------------------------------------- thread id API

    def encode_thread_id(self, platform_data: DiscordThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> DiscordThreadId:
        return decode_thread_id(thread_id)

    def is_dm(self, thread_id: str) -> bool:
        return is_dm(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        return _channel_id_from_thread_id(thread_id)

    def get_channel_visibility(self, channel_id: str) -> str:
        """Discord channels are private by default (guild-scoped); DMs are private.

        Discord doesn't expose a "public" visibility concept the way Slack does
        (no ``is_private`` flag at the channel level for guild text channels),
        so we conservatively return ``"unknown"`` for non-DM channels. Mirrors
        the Google Chat adapter's treatment.
        """

        if self.is_dm(channel_id):
            return "private"
        return "unknown"

    # --------------------------------------------------------- subscriptions

    async def subscribe(self, thread_id: str) -> None:
        """No-op — Discord subscription is handled at the `Chat` state layer.

        Discord's Gateway / interactions surface doesn't expose a subscription
        primitive; mirrors the Google Chat adapter's no-op.
        """

        return None

    async def unsubscribe(self, thread_id: str) -> None:
        return None

    # ------------------------------------------------------------- modals

    async def open_modal(self, trigger_id: str, view: Any) -> Any:
        """Discord doesn't expose a Slack-style modal surface.

        Discord has modal responses (``APPLICATION_MODAL``) tied to interaction
        responses, not a standalone ``open_modal`` trigger. Raise the canonical
        ``chat.NotImplementedError`` — pinned in ``docs/parity.md``.
        """

        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "Discord does not expose a standalone open_modal surface; respond "
            "with a modal interaction response instead.",
            feature="modals",
        )

    # ----------------------------------------------------------- streaming

    async def stream(
        self,
        thread_id: str,
        chunks: Any,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Stream ``chunks`` to a Discord message, editing periodically.

        Mirrors the Slack/GChat streaming shape: post placeholder, edit every
        ``streamingUpdateIntervalMs``, flush on close.
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

    # -------------------------------------------------------- webhook entry

    async def handle_webhook(
        self,
        body: bytes | str,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Verify the incoming webhook and dispatch to the appropriate handler.

        Returns a ``(status, headers, body)`` tuple. Matches the shape used by
        :meth:`chat.Chat.handle_webhook`.

        Detects forwarded Gateway events by the ``x-discord-gateway-token``
        header (bot-token-authenticated); otherwise verifies the Ed25519
        signature via the ``x-signature-ed25519`` / ``x-signature-timestamp``
        headers.
        """

        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        body_str = body_bytes.decode("utf-8", errors="replace")
        normalized = _lowercase_headers(headers or {})

        gateway_token = normalized.get("x-discord-gateway-token")
        if gateway_token:
            if gateway_token != self.bot_token:
                self.logger.warn("Invalid gateway token")
                return 401, {}, "Invalid gateway token"
            self.logger.info("Discord forwarded Gateway event received")
            try:
                event = json.loads(body_str)
            except ValueError:
                return 400, {}, "Invalid JSON"
            await self._handle_forwarded_gateway_event(event)
            return 200, {"content-type": "application/json"}, json.dumps({"ok": True})

        signature = normalized.get("x-signature-ed25519")
        timestamp = normalized.get("x-signature-timestamp")
        if not verify_discord_signature(self.public_key, signature, timestamp, body_bytes):
            self.logger.warn("Discord signature verification failed")
            return 401, {}, "Invalid signature"

        try:
            interaction = json.loads(body_str)
        except ValueError:
            return 400, {}, "Invalid JSON"

        interaction_type = interaction.get("type")

        if interaction_type == INTERACTION_TYPE_PING:
            return (
                200,
                {"content-type": "application/json"},
                json.dumps({"type": RESPONSE_TYPE_PONG}),
            )

        if interaction_type == INTERACTION_TYPE_MESSAGE_COMPONENT:
            await self._handle_component_interaction(interaction)
            return (
                200,
                {"content-type": "application/json"},
                json.dumps({"type": RESPONSE_TYPE_DEFERRED_UPDATE_MESSAGE}),
            )

        if interaction_type == INTERACTION_TYPE_APPLICATION_COMMAND:
            await self._handle_application_command_interaction(interaction)
            return (
                200,
                {"content-type": "application/json"},
                json.dumps({"type": RESPONSE_TYPE_DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}),
            )

        return 400, {}, "Unknown interaction type"

    # ----------------------------------------- interaction & event handlers

    async def _handle_component_interaction(self, interaction: dict[str, Any]) -> None:
        if self._chat is None:
            self.logger.warn("Chat instance not initialized, ignoring interaction")
            return

        data = interaction.get("data") or {}
        custom_id = data.get("custom_id")
        if not custom_id:
            self.logger.warn("No custom_id in component interaction")
            return

        user = (interaction.get("member") or {}).get("user") or interaction.get("user")
        if not user:
            self.logger.warn("No user in component interaction")
            return

        interaction_channel_id = interaction.get("channel_id")
        guild_id = interaction.get("guild_id") or "@me"
        message_id = (interaction.get("message") or {}).get("id")
        if not (interaction_channel_id and message_id):
            self.logger.warn("Missing channel_id or message_id in interaction")
            return

        channel = interaction.get("channel") or {}
        is_thread = channel.get("type") in (
            CHANNEL_TYPE_GUILD_PUBLIC_THREAD,
            CHANNEL_TYPE_GUILD_PRIVATE_THREAD,
        )
        parent_channel_id = (
            channel.get("parent_id")
            if is_thread and channel.get("parent_id")
            else interaction_channel_id
        )

        if is_thread:
            thread_id = self.encode_thread_id(
                {
                    "guildId": guild_id,
                    "channelId": str(parent_channel_id),
                    "threadId": str(interaction_channel_id),
                }
            )
        else:
            thread_id = self.encode_thread_id(
                {"guildId": guild_id, "channelId": str(interaction_channel_id)}
            )

        from chat.types import Author

        author = Author(
            user_id=str(user.get("id") or ""),
            user_name=str(user.get("username") or ""),
            full_name=str(user.get("global_name") or user.get("username") or ""),
            is_bot=bool(user.get("bot", False)),
            is_me=user.get("id") == self.application_id,
        )

        action_event = {
            "actionId": custom_id,
            "value": custom_id,
            "user": author,
            "messageId": message_id,
            "threadId": thread_id,
            "adapter": self,
            "raw": interaction,
        }

        self.logger.debug(
            "Processing Discord button action",
            {"actionId": custom_id, "messageId": message_id, "threadId": thread_id},
        )

        process = getattr(self._chat, "process_action", None) or getattr(
            self._chat, "processAction", None
        )
        if process is not None:
            result = process(action_event)
            if hasattr(result, "__await__"):
                await result

    async def _handle_application_command_interaction(self, interaction: dict[str, Any]) -> None:
        if self._chat is None:
            self.logger.warn("Chat instance not initialized, ignoring interaction")
            return

        data = interaction.get("data") or {}
        command_name = data.get("name")
        if not command_name:
            self.logger.warn("No command name in application command interaction")
            return

        user = (interaction.get("member") or {}).get("user") or interaction.get("user")
        if not user:
            self.logger.warn("No user in application command interaction")
            return

        interaction_channel_id = interaction.get("channel_id")
        if not interaction_channel_id:
            self.logger.warn("Missing channel_id in application command interaction")
            return

        guild_id = interaction.get("guild_id") or "@me"
        channel = interaction.get("channel") or {}
        is_thread = channel.get("type") in (
            CHANNEL_TYPE_GUILD_PUBLIC_THREAD,
            CHANNEL_TYPE_GUILD_PRIVATE_THREAD,
        )
        parent_channel_id = (
            channel.get("parent_id")
            if is_thread and channel.get("parent_id")
            else interaction_channel_id
        )

        if is_thread:
            channel_id = self.encode_thread_id(
                {
                    "guildId": guild_id,
                    "channelId": str(parent_channel_id),
                    "threadId": str(interaction_channel_id),
                }
            )
        else:
            channel_id = self.encode_thread_id(
                {"guildId": guild_id, "channelId": str(interaction_channel_id)}
            )

        command, text = parse_slash_command(command_name, data.get("options"))

        from chat.types import Author

        author = Author(
            user_id=str(user.get("id") or ""),
            user_name=str(user.get("username") or ""),
            full_name=str(user.get("global_name") or user.get("username") or ""),
            is_bot=bool(user.get("bot", False)),
            is_me=user.get("id") == self.application_id,
        )

        slash_command_event = {
            "command": command,
            "text": text,
            "user": author,
            "adapter": self,
            "raw": interaction,
            "channelId": channel_id,
        }

        token = self._request_context.set(
            DiscordSlashCommandContext(
                channel_id=channel_id,
                interaction_token=interaction.get("token", ""),
                initial_response_sent=False,
            )
        )
        try:
            process = getattr(self._chat, "process_slash_command", None) or getattr(
                self._chat, "processSlashCommand", None
            )
            if process is not None:
                result = process(slash_command_event)
                if hasattr(result, "__await__"):
                    await result
        finally:
            self._request_context.reset(token)

    async def _handle_forwarded_gateway_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        self.logger.info(
            "Processing forwarded Gateway event",
            {"type": event_type, "timestamp": event.get("timestamp")},
        )

        data = event.get("data") or {}
        if event_type == "GATEWAY_MESSAGE_CREATE":
            await self._handle_forwarded_message(data)
        elif event_type == "GATEWAY_MESSAGE_REACTION_ADD":
            await self._handle_forwarded_reaction(data, True)
        elif event_type == "GATEWAY_MESSAGE_REACTION_REMOVE":
            await self._handle_forwarded_reaction(data, False)
        else:
            self.logger.debug("Forwarded Gateway event (no handler)", {"type": event_type})

    async def _handle_forwarded_message(self, data: dict[str, Any]) -> None:
        if self._chat is None:
            return

        guild_id = data.get("guild_id") or "@me"
        channel_id = data.get("channel_id", "")

        discord_thread_id: str | None = None
        parent_channel_id = channel_id

        thread = data.get("thread")
        if thread:
            discord_thread_id = thread.get("id")
            parent_channel_id = thread.get("parent_id", channel_id)
        elif data.get("channel_type") in (
            CHANNEL_TYPE_GUILD_PUBLIC_THREAD,
            CHANNEL_TYPE_GUILD_PRIVATE_THREAD,
        ):
            parent = await self._resolve_thread_parent(channel_id)
            if parent:
                discord_thread_id = channel_id
                parent_channel_id = parent

        mentions = data.get("mentions") or []
        is_user_mentioned = bool(data.get("is_mention")) or any(
            m.get("id") == self.application_id for m in mentions
        )
        mention_roles = data.get("mention_roles") or []
        is_role_mentioned = bool(self.mention_role_ids) and any(
            r in self.mention_role_ids for r in mention_roles
        )
        is_mentioned = is_user_mentioned or is_role_mentioned

        if not discord_thread_id and is_mentioned:
            try:
                new_thread = await self._create_discord_thread(channel_id, data.get("id", ""))
                discord_thread_id = new_thread["id"]
            except Exception as exc:
                self.logger.error(
                    "Failed to create Discord thread for mention",
                    {"error": str(exc), "messageId": data.get("id")},
                )

        thread_id = self.encode_thread_id(
            {
                "guildId": guild_id,
                "channelId": parent_channel_id,
                "threadId": discord_thread_id or "",
            }
            if discord_thread_id
            else {"guildId": guild_id, "channelId": parent_channel_id}
        )

        chat_message = self._build_gateway_message(data, thread_id, is_mentioned)
        handle = getattr(self._chat, "handle_incoming_message", None) or getattr(
            self._chat, "handleIncomingMessage", None
        )
        if handle is None:
            return
        try:
            result = handle(self, thread_id, chat_message)
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            self.logger.error(
                "Error handling forwarded message",
                {"error": str(exc), "messageId": data.get("id")},
            )

    async def _handle_forwarded_reaction(self, data: dict[str, Any], added: bool) -> None:
        if self._chat is None:
            return

        guild_id = data.get("guild_id") or "@me"
        channel_id = data.get("channel_id", "")

        discord_thread_id: str | None = None
        parent_channel_id = channel_id

        if data.get("channel_type") in (
            CHANNEL_TYPE_GUILD_PUBLIC_THREAD,
            CHANNEL_TYPE_GUILD_PRIVATE_THREAD,
        ):
            parent = await self._resolve_thread_parent(channel_id)
            if parent:
                discord_thread_id = channel_id
                parent_channel_id = parent

        thread_id = self.encode_thread_id(
            {
                "guildId": guild_id,
                "channelId": parent_channel_id,
                "threadId": discord_thread_id or "",
            }
            if discord_thread_id
            else {"guildId": guild_id, "channelId": parent_channel_id}
        )

        emoji_data = data.get("emoji") or {}
        emoji_name = emoji_data.get("name") or "unknown"
        normalized_emoji = _normalize_discord_emoji(emoji_name)

        user_info = data.get("user") or (data.get("member") or {}).get("user")
        if not user_info:
            self.logger.warn("Reaction event missing user info", {"data": data})
            return

        raw_emoji = f"<:{emoji_name}:{emoji_data['id']}>" if emoji_data.get("id") else emoji_name

        from chat.types import Author

        reaction_author = Author(
            user_id=str(user_info.get("id") or ""),
            user_name=str(user_info.get("username") or ""),
            full_name=str(user_info.get("username") or ""),
            is_bot=user_info.get("bot") is True,
            is_me=user_info.get("id") == self.application_id,
        )

        reaction_event = {
            "adapter": self,
            "threadId": thread_id,
            "messageId": data.get("message_id"),
            "emoji": normalized_emoji,
            "rawEmoji": raw_emoji,
            "added": added,
            "user": reaction_author,
            "raw": data,
        }

        process = getattr(self._chat, "process_reaction", None) or getattr(
            self._chat, "processReaction", None
        )
        if process is not None:
            result = process(reaction_event)
            if hasattr(result, "__await__"):
                await result

    # --------------------------------------------------- message REST calls

    async def post_message(self, thread_id: str, message: Any) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        channel_id = decoded["channelId"]
        discord_thread_id = decoded.get("threadId")
        if discord_thread_id:
            channel_id = discord_thread_id

        payload = self._build_message_payload(message, clear_content_when_card=False)
        files = extract_files(message)

        slash_context = self._request_context.get()
        if slash_context and slash_context.channel_id == thread_id:
            return await self._post_slash_response(slash_context, thread_id, payload, files)

        if files:
            return await self._post_message_with_files(channel_id, thread_id, payload, files)

        response = await self._discord_fetch(
            f"/channels/{channel_id}/messages",
            "POST",
            payload,
            "postMessage",
        )
        result = response.json()
        return {"id": result.get("id", ""), "threadId": thread_id, "raw": result}

    async def edit_message(self, thread_id: str, message_id: str, message: Any) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        target_channel_id = decoded.get("threadId") or decoded["channelId"]

        payload = self._build_message_payload(message, clear_content_when_card=True)

        response = await self._discord_fetch(
            f"/channels/{target_channel_id}/messages/{message_id}",
            "PATCH",
            payload,
            "editMessage",
        )
        result = response.json()
        return {"id": result.get("id", ""), "threadId": thread_id, "raw": result}

    async def delete_message(self, thread_id: str, message_id: str) -> None:
        decoded = self.decode_thread_id(thread_id)
        target_channel_id = decoded.get("threadId") or decoded["channelId"]
        await self._discord_fetch(
            f"/channels/{target_channel_id}/messages/{message_id}",
            "DELETE",
            None,
            "deleteMessage",
        )

    async def add_reaction(self, thread_id: str, message_id: str, emoji: Any) -> None:
        decoded = self.decode_thread_id(thread_id)
        target_channel_id = decoded.get("threadId") or decoded["channelId"]
        emoji_encoded = _encode_emoji(emoji)
        await self._discord_fetch(
            f"/channels/{target_channel_id}/messages/{message_id}/reactions/{emoji_encoded}/@me",
            "PUT",
            None,
            "addReaction",
        )

    async def remove_reaction(self, thread_id: str, message_id: str, emoji: Any) -> None:
        decoded = self.decode_thread_id(thread_id)
        target_channel_id = decoded.get("threadId") or decoded["channelId"]
        emoji_encoded = _encode_emoji(emoji)
        await self._discord_fetch(
            f"/channels/{target_channel_id}/messages/{message_id}/reactions/{emoji_encoded}/@me",
            "DELETE",
            None,
            "removeReaction",
        )

    async def start_typing(self, thread_id: str, status: str | None = None) -> None:
        decoded = self.decode_thread_id(thread_id)
        target_channel_id = decoded.get("threadId") or decoded["channelId"]
        await self._discord_fetch(
            f"/channels/{target_channel_id}/typing", "POST", None, "startTyping"
        )

    # ------------------------------------------------------ reader methods

    async def fetch_messages(self, thread_id: str, options: Any = None) -> dict[str, Any]:
        opts = options or {}
        if not isinstance(opts, dict):
            opts = {}
        decoded = self.decode_thread_id(thread_id)
        target_channel_id = decoded.get("threadId") or decoded["channelId"]
        limit = int(opts.get("limit") or 50)
        direction = opts.get("direction") or "backward"

        query = f"limit={limit}"
        cursor = opts.get("cursor")
        if cursor:
            key = "before" if direction == "backward" else "after"
            query += f"&{key}={urlquote(str(cursor))}"

        response = await self._discord_fetch(
            f"/channels/{target_channel_id}/messages?{query}",
            "GET",
            None,
            "fetchMessages",
        )
        raw_messages = response.json() or []

        sorted_messages = list(reversed(raw_messages))
        messages = [self._parse_discord_message(m, thread_id) for m in sorted_messages]

        next_cursor: str | None = None
        if len(raw_messages) == limit:
            if direction == "backward":
                next_cursor = raw_messages[-1].get("id") if raw_messages else None
            else:
                next_cursor = raw_messages[0].get("id") if raw_messages else None
        return {"messages": messages, "nextCursor": next_cursor}

    async def fetch_thread(self, thread_id: str) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        channel_id = decoded["channelId"]
        response = await self._discord_fetch(f"/channels/{channel_id}", "GET", None, "fetchThread")
        channel = response.json() or {}
        channel_type = channel.get("type")
        return {
            "id": thread_id,
            "channelId": channel_id,
            "channelName": channel.get("name"),
            "isDM": channel_type in (CHANNEL_TYPE_DM, CHANNEL_TYPE_GROUP_DM),
            "metadata": {
                "guildId": decoded.get("guildId"),
                "channelType": channel_type,
                "raw": channel,
            },
        }

    async def open_dm(self, user_id: str) -> str:
        response = await self._discord_fetch(
            "/users/@me/channels", "POST", {"recipient_id": user_id}, "openDM"
        )
        dm_channel = response.json() or {}
        return self.encode_thread_id({"guildId": "@me", "channelId": dm_channel.get("id", "")})

    async def fetch_channel_info(self, channel_id: str) -> dict[str, Any]:
        parts = channel_id.split(":")
        if len(parts) < 3 or not parts[2]:
            raise ValidationError("discord", f"Invalid Discord channel ID: {channel_id}")
        discord_channel_id = parts[2]
        response = await self._discord_fetch(
            f"/channels/{discord_channel_id}", "GET", None, "fetchChannelInfo"
        )
        channel = response.json() or {}
        channel_type = channel.get("type")
        return {
            "id": channel_id,
            "name": channel.get("name"),
            "isDM": channel_type in (CHANNEL_TYPE_DM, CHANNEL_TYPE_GROUP_DM),
            "memberCount": channel.get("member_count"),
            "metadata": {"channelType": channel_type, "raw": channel},
        }

    async def fetch_channel_messages(self, channel_id: str, options: Any = None) -> dict[str, Any]:
        opts = options or {}
        if not isinstance(opts, dict):
            opts = {}
        parts = channel_id.split(":")
        if len(parts) < 3 or not parts[2]:
            raise ValidationError("discord", f"Invalid Discord channel ID: {channel_id}")
        discord_channel_id = parts[2]
        limit = int(opts.get("limit") or 50)
        direction = opts.get("direction") or "backward"

        query = f"limit={limit}"
        cursor = opts.get("cursor")
        if cursor:
            key = "before" if direction == "backward" else "after"
            query += f"&{key}={urlquote(str(cursor))}"

        response = await self._discord_fetch(
            f"/channels/{discord_channel_id}/messages?{query}",
            "GET",
            None,
            "fetchChannelMessages",
        )
        raw_messages = response.json() or []
        sorted_messages = list(reversed(raw_messages))
        messages = [self._parse_discord_message(m, channel_id) for m in sorted_messages]

        next_cursor: str | None = None
        if len(raw_messages) == limit:
            if direction == "backward":
                next_cursor = raw_messages[-1].get("id") if raw_messages else None
            else:
                next_cursor = raw_messages[0].get("id") if raw_messages else None
        return {"messages": messages, "nextCursor": next_cursor}

    async def list_threads(self, channel_id: str, options: Any = None) -> dict[str, Any]:
        opts = options or {}
        if not isinstance(opts, dict):
            opts = {}
        parts = channel_id.split(":")
        if len(parts) < 4 or not (parts[1] and parts[2]):
            raise ValidationError("discord", f"Invalid Discord channel ID: {channel_id}")
        guild_id = parts[1]
        discord_channel_id = parts[2]

        active_resp = await self._discord_fetch(
            f"/guilds/{guild_id}/threads/active", "GET", None, "listThreads"
        )
        active_data = active_resp.json() or {}
        channel_threads = [
            t
            for t in (active_data.get("threads") or [])
            if t.get("parent_id") == discord_channel_id
        ]

        limit = int(opts.get("limit") or 50)
        archived_threads: list[dict[str, Any]] = []
        try:
            archived_resp = await self._discord_fetch(
                f"/channels/{discord_channel_id}/threads/archived/public?limit={limit}",
                "GET",
                None,
                "listThreadsArchived",
            )
            archived_threads = (archived_resp.json() or {}).get("threads") or []
        except NetworkError:
            self.logger.debug("Could not fetch archived threads (may lack permissions)")

        seen: set[str] = set()
        unique_threads: list[dict[str, Any]] = []
        for t in [*channel_threads, *archived_threads]:
            tid = t.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            unique_threads.append(t)

        limited_threads = unique_threads[:limit]

        threads: list[dict[str, Any]] = []
        for t in limited_threads:
            thread_encoded = self.encode_thread_id(
                {
                    "guildId": guild_id,
                    "channelId": discord_channel_id,
                    "threadId": t.get("id", ""),
                }
            )
            reply_count = t.get("total_message_sent") or t.get("message_count")
            archive_ts = (t.get("thread_metadata") or {}).get("archive_timestamp")
            last_reply_at = _parse_iso(archive_ts) if archive_ts else None

            try:
                msg_resp = await self._discord_fetch(
                    f"/channels/{t.get('id')}/messages?limit=1&after=0",
                    "GET",
                    None,
                    "listThreadsRoot",
                )
                msgs = msg_resp.json() or []
                if msgs:
                    threads.append(
                        {
                            "id": thread_encoded,
                            "rootMessage": self._parse_discord_message(msgs[0], thread_encoded),
                            "replyCount": reply_count,
                            "lastReplyAt": last_reply_at,
                        }
                    )
                    continue
            except NetworkError:
                pass

            threads.append(
                {
                    "id": thread_encoded,
                    "rootMessage": self._make_placeholder_message(t, thread_encoded),
                    "replyCount": reply_count,
                }
            )

        return {
            "threads": threads,
            "nextCursor": str(limit) if len(unique_threads) > limit else None,
        }

    async def post_channel_message(self, channel_id: str, message: Any) -> dict[str, Any]:
        parts = channel_id.split(":")
        if len(parts) < 3 or not parts[2]:
            raise ValidationError("discord", f"Invalid Discord channel ID: {channel_id}")
        discord_channel_id = parts[2]

        payload = self._build_message_payload(message, clear_content_when_card=False)
        files = extract_files(message)

        slash_context = self._request_context.get()
        if slash_context and slash_context.channel_id == channel_id:
            return await self._post_slash_response(slash_context, channel_id, payload, files)

        if files:
            return await self._post_message_with_files(
                discord_channel_id, channel_id, payload, files
            )

        response = await self._discord_fetch(
            f"/channels/{discord_channel_id}/messages",
            "POST",
            payload,
            "postChannelMessage",
        )
        result = response.json()
        return {"id": result.get("id", ""), "threadId": channel_id, "raw": result}

    # ----------------------------------------------------------- parsing

    def parse_message(self, raw: Any) -> Message[Any]:
        msg = raw if isinstance(raw, dict) else {}
        guild_id = msg.get("guild_id") or "@me"
        thread_id = self.encode_thread_id(
            {"guildId": guild_id, "channelId": msg.get("channel_id", "")}
        )
        return self._parse_discord_message(msg, thread_id)

    def render_formatted(self, content: Any) -> str:
        return self.format_converter.from_ast(content)

    # =====================================================================
    # Internal helpers
    # =====================================================================

    def _build_message_payload(
        self, message: Any, *, clear_content_when_card: bool
    ) -> dict[str, Any]:
        from chat import convert_emoji_placeholders

        payload: dict[str, Any] = {}
        embeds: list[dict[str, Any]] = []
        components: list[dict[str, Any]] = []

        card = extract_card(message)
        if card:
            card_payload = card_to_discord_payload(cast("dict[str, Any]", card))
            embeds.extend(card_payload.get("embeds") or [])
            components.extend(card_payload.get("components") or [])
            if clear_content_when_card:
                payload["content"] = ""
        else:
            rendered = self.format_converter.render_postable(message)
            payload["content"] = _truncate_content(convert_emoji_placeholders(rendered, "discord"))

        if embeds:
            payload["embeds"] = embeds
        if components:
            payload["components"] = components
        return payload

    async def _post_message_with_files(
        self,
        channel_id: str,
        thread_id: str,
        payload: dict[str, Any],
        files: list[Any],
    ) -> dict[str, Any]:
        form_files = await _prepare_multipart_files(files)
        client = await self._get_http_client()
        url = f"{self.api_base_url}/channels/{channel_id}/messages"
        response = await client.post(
            url,
            headers={"Authorization": f"Bot {self.bot_token}"},
            data={"payload_json": json.dumps(payload)},
            files=form_files,
        )
        if response.status_code >= 400:
            handle_discord_error(response, "postMessage")
        result = response.json()
        return {"id": result.get("id", ""), "threadId": thread_id, "raw": result}

    async def _post_slash_response(
        self,
        slash_context: DiscordSlashCommandContext,
        thread_id: str,
        payload: dict[str, Any],
        files: list[Any],
    ) -> dict[str, Any]:
        is_initial = not slash_context.initial_response_sent
        slash_context.initial_response_sent = True

        if is_initial:
            path = (
                f"/webhooks/{self.application_id}/{slash_context.interaction_token}"
                "/messages/@original"
            )
            method = "PATCH"
        else:
            path = f"/webhooks/{self.application_id}/{slash_context.interaction_token}?wait=true"
            method = "POST"

        client = await self._get_http_client()
        url = f"{self.api_base_url}{path}"

        if files:
            form_files = await _prepare_multipart_files(files)
            response = await client.request(
                method,
                url,
                data={"payload_json": json.dumps(payload)},
                files=form_files,
            )
        else:
            response = await client.request(
                method,
                url,
                headers={"Content-Type": "application/json"},
                content=json.dumps(payload).encode("utf-8"),
            )

        if response.status_code >= 400:
            handle_discord_error(response, "slashResponse")
        result = response.json()
        return {"id": result.get("id", ""), "threadId": thread_id, "raw": result}

    async def _create_discord_thread(self, channel_id: str, message_id: str) -> dict[str, str]:
        thread_name = f"Thread {datetime.now(UTC).isoformat()}"
        try:
            response = await self._discord_fetch(
                f"/channels/{channel_id}/messages/{message_id}/threads",
                "POST",
                {"name": thread_name, "auto_archive_duration": 1440},
                "createThread",
            )
            result = response.json() or {}
            return {"id": str(result.get("id", "")), "name": str(result.get("name", thread_name))}
        except NetworkError as err:
            # Discord error 160004: thread already exists for this message → reuse.
            if "160004" in str(err):
                self.logger.debug(
                    "Thread already exists for message, reusing existing thread",
                    {"channelId": channel_id, "messageId": message_id},
                )
                return {"id": message_id, "name": thread_name}
            raise

    async def _resolve_thread_parent(self, channel_id: str) -> str | None:
        cached = self._thread_parent_cache.get(channel_id)
        now = time.monotonic()
        if cached and cached[1] > now:
            return cached[0]
        try:
            response = await self._discord_fetch(
                f"/channels/{channel_id}", "GET", None, "resolveThreadParent"
            )
            channel = response.json() or {}
            parent = channel.get("parent_id")
            if parent:
                self._thread_parent_cache[channel_id] = (
                    parent,
                    now + _THREAD_PARENT_CACHE_TTL_SECONDS,
                )
                return str(parent)
        except NetworkError as err:
            self.logger.error(
                "Failed to fetch thread parent",
                {"error": str(err), "channelId": channel_id},
            )
        return None

    def _build_gateway_message(
        self, data: dict[str, Any], thread_id: str, is_mentioned: bool
    ) -> Message[Any]:
        from chat import Attachment, Author, Message, MessageMetadata

        author = data.get("author") or {}
        attachments_raw = data.get("attachments") or []
        attachments = [
            Attachment(
                type=_attachment_type(att.get("content_type")),
                url=att.get("url"),
                name=att.get("filename"),
                mime_type=att.get("content_type"),
                size=att.get("size"),
            )
            for att in attachments_raw
        ]

        timestamp = data.get("timestamp")
        date_sent = _parse_iso(timestamp) if isinstance(timestamp, str) else datetime.now(UTC)

        content = str(data.get("content") or "")
        return Message(
            id=str(data.get("id") or ""),
            thread_id=thread_id,
            text=content,
            formatted=self.format_converter.to_ast(content),
            author=Author(
                user_id=str(author.get("id") or ""),
                user_name=str(author.get("username") or ""),
                full_name=str(author.get("global_name") or author.get("username") or ""),
                is_bot=author.get("bot") is True,
                is_me=author.get("id") == self.application_id,
            ),
            metadata=MessageMetadata(date_sent=date_sent or datetime.now(UTC), edited=False),
            attachments=attachments,
            raw=data,
            is_mention=is_mentioned,
        )

    def _parse_discord_message(self, raw: dict[str, Any], thread_id: str) -> Message[Any]:
        from chat import Attachment, Author, Message, MessageMetadata

        # Thread-starter messages use the referenced message when available
        # (upstream ``MessageType.ThreadStarterMessage`` = 21 in discord-api-types).
        if raw.get("type") == 21 and raw.get("referenced_message"):
            msg = raw["referenced_message"]
        else:
            msg = raw

        author = msg.get("author") or {}
        is_bot = author.get("bot", False)

        timestamp = msg.get("timestamp")
        date_sent = _parse_iso(timestamp) if isinstance(timestamp, str) else datetime.now(UTC)
        edited_ts = msg.get("edited_timestamp")
        edited_at = _parse_iso(edited_ts) if isinstance(edited_ts, str) else None

        attachments = [
            Attachment(
                type=_attachment_type(att.get("content_type")),
                url=att.get("url"),
                name=att.get("filename"),
                mime_type=att.get("content_type"),
                size=att.get("size"),
                width=att.get("width"),
                height=att.get("height"),
            )
            for att in (msg.get("attachments") or [])
        ]

        content = str(msg.get("content") or "")
        return Message(
            id=str(msg.get("id") or ""),
            thread_id=thread_id,
            text=self.format_converter.extract_plain_text(content),
            formatted=self.format_converter.to_ast(content),
            raw=raw,
            author=Author(
                user_id=str(author.get("id") or ""),
                user_name=str(author.get("username") or ""),
                full_name=str(author.get("global_name") or author.get("username") or ""),
                is_bot=bool(is_bot),
                is_me=author.get("id") == self.bot_user_id,
            ),
            metadata=MessageMetadata(
                date_sent=date_sent or datetime.now(UTC),
                edited=edited_ts is not None,
                edited_at=edited_at,
            ),
            attachments=attachments,
        )

    def _make_placeholder_message(self, thread: dict[str, Any], thread_id: str) -> Message[Any]:
        from chat import Author, Message, MessageMetadata

        return Message(
            id=str(thread.get("id") or ""),
            thread_id=thread_id,
            text=str(thread.get("name") or ""),
            formatted=self.format_converter.to_ast(str(thread.get("name") or "")),
            raw=thread,
            author=Author(
                user_id="unknown",
                user_name="unknown",
                full_name="unknown",
                is_bot=False,
                is_me=False,
            ),
            metadata=MessageMetadata(date_sent=datetime.now(UTC), edited=False),
            attachments=[],
        )

    async def _discord_fetch(
        self,
        path: str,
        method: str,
        body: Any,
        operation: str,
    ) -> httpx.Response:
        client = await self._get_http_client()
        url = f"{self.api_base_url}{path}"
        headers = {"Authorization": f"Bot {self.bot_token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(body).encode("utf-8")
        else:
            content = None
        try:
            response = await client.request(method, url, headers=headers, content=content)
        except httpx.HTTPError as err:
            raise NetworkError(
                "discord",
                f"Discord API error during {operation}: {err}",
            ) from err

        if response.status_code >= 400:
            handle_discord_error(response, operation)
        return response

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def parse_slash_command(name: str, options: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    """Parse a Discord slash command into ``(command, text)``.

    Subcommand / subcommand-group names are appended to the command path,
    leaf option values are flattened into ``text``. Mirrors upstream
    ``parseSlashCommand``.
    """

    command_parts = [name if name.startswith("/") else f"/{name}"]
    value_parts: list[str] = []

    def _collect(items: list[dict[str, Any]]) -> None:
        for option in items:
            if option.get("value") is not None:
                value_parts.append(str(option["value"]))
                continue
            sub_options = option.get("options") or []
            if sub_options:
                command_parts.append(str(option.get("name", "")))
                _collect(sub_options)

    if options:
        _collect(options)
    return " ".join(command_parts), " ".join(value_parts).strip()


def _is_hex_64(value: str) -> bool:
    if len(value) != 64:
        return False
    return all(ch in _HEX_64_PATTERN for ch in value)


def _lowercase_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _truncate_content(content: str) -> str:
    if len(content) <= DISCORD_MAX_CONTENT_LENGTH:
        return content
    return content[: DISCORD_MAX_CONTENT_LENGTH - 3] + "..."


def _encode_emoji(emoji: Any) -> str:
    from chat import default_emoji_resolver

    emoji_str = (
        default_emoji_resolver.to_discord(emoji)
        if hasattr(default_emoji_resolver, "to_discord")
        else str(emoji)
    )
    return urlquote(emoji_str)


def _normalize_discord_emoji(emoji_name: str) -> Any:
    from chat import get_emoji

    normalized_name = _EMOJI_NAME_MAP.get(emoji_name, emoji_name)
    return get_emoji(normalized_name)


def _attachment_type(mime_type: str | None) -> Literal["image", "video", "audio", "file"]:
    if not mime_type:
        return "file"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "file"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _prepare_multipart_files(files: list[Any]) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Convert a list of file dicts into httpx's ``files=`` tuple format."""

    out: list[tuple[str, tuple[str, bytes, str]]] = []
    for i, file in enumerate(files):
        data = file.get("data") if isinstance(file, dict) else getattr(file, "data", None)
        buf = await to_buffer(data, {"platform": "discord", "throw_on_unsupported": False})
        if not buf:
            continue
        filename = (
            file.get("filename") if isinstance(file, dict) else getattr(file, "filename", None)
        ) or f"file-{i}"
        mime_type = (
            file.get("mimeType") if isinstance(file, dict) else getattr(file, "mime_type", None)
        ) or "application/octet-stream"
        out.append((f"files[{i}]", (filename, buf, mime_type)))
    return out


def create_discord_adapter(config: DiscordAdapterConfig | None = None) -> DiscordAdapter:
    """Factory for :class:`DiscordAdapter`. Mirrors upstream ``createDiscordAdapter``."""

    return DiscordAdapter(config)


__all__ = [
    "DISCORD_API_BASE",
    "DISCORD_MAX_CONTENT_LENGTH",
    "DiscordAdapter",
    "DiscordAdapterConfig",
    "DiscordSlashCommandContext",
    "create_discord_adapter",
    "parse_slash_command",
    "verify_discord_signature",
]
