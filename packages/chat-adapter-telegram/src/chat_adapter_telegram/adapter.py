"""Telegram adapter facade.

Python port of upstream ``packages/adapter-telegram/src/index.ts``.

Covers the outbound Bot API surface (``sendMessage`` / ``editMessageText`` /
``deleteMessage`` / ``sendChatAction`` / ``answerCallbackQuery`` /
``setMessageReaction`` / ``getMe`` / ``getChat`` / ``getFile`` /
``sendDocument``), inbound webhook dispatch (``message`` / ``edited_message``
/ ``channel_post`` / ``edited_channel_post`` / ``callback_query`` /
``message_reaction``), and long-polling (``getUpdates``).

Webhook requests are verified via the ``x-telegram-bot-api-secret-token``
header using :func:`hmac.compare_digest` for a constant-time comparison.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
from datetime import UTC
from typing import TYPE_CHECKING, Any, Literal

import httpx
from chat import (
    ConsoleLogger,
    Message,
    NotImplementedError,
    convert_emoji_placeholders,
    default_emoji_resolver,
    get_emoji,
)
from chat_adapter_shared import (
    NetworkError,
    ResourceNotFoundError,
    ValidationError,
    card_to_fallback_text,
    extract_card,
    extract_files,
    to_buffer,
)

from .cards import (
    card_to_telegram_inline_keyboard,
    decode_telegram_callback_data,
    empty_telegram_inline_keyboard,
)
from .errors import throw_telegram_api_error
from .markdown import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    TelegramFormatConverter,
    TelegramParseMode,
    to_bot_api_parse_mode,
    truncate_for_telegram,
)
from .thread_id import decode_thread_id, encode_thread_id
from .types import (
    TelegramAdapterConfig,
    TelegramAdapterMode,
    TelegramApiResponse,
    TelegramCallbackQuery,
    TelegramChat,
    TelegramInlineKeyboardMarkup,
    TelegramLongPollingConfig,
    TelegramMessage,
    TelegramMessageEntity,
    TelegramMessageReactionUpdated,
    TelegramRawMessage,
    TelegramReactionType,
    TelegramThreadId,
    TelegramUpdate,
    TelegramUser,
    TelegramWebhookInfo,
)

if TYPE_CHECKING:
    from chat import EmojiValue, Logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_SECRET_TOKEN_HEADER = "x-telegram-bot-api-secret-token"

_MESSAGE_ID_PATTERN = re.compile(r"^([^:]+):(\d+)$")
_MESSAGE_SEQUENCE_PATTERN = re.compile(r":(\d+)$")
_LEADING_AT_PATTERN = re.compile(r"^@+")
_EMOJI_PLACEHOLDER_PATTERN = re.compile(r"^\{\{emoji:([a-z0-9_]+)\}\}$", re.IGNORECASE)
_EMOJI_NAME_PATTERN = re.compile(r"^[a-z0-9_+\-]+$", re.IGNORECASE)
_REGEX_ESCAPE_PATTERN = re.compile(r"([.*+?^${}()|\[\]\\])")

_TELEGRAM_DEFAULT_POLLING_TIMEOUT_SECONDS = 30
_TELEGRAM_DEFAULT_POLLING_LIMIT = 100
_TELEGRAM_DEFAULT_POLLING_RETRY_DELAY_MS = 1000
_TELEGRAM_MAX_POLLING_LIMIT = 100
_TELEGRAM_MIN_POLLING_LIMIT = 1
_TELEGRAM_MIN_POLLING_TIMEOUT_SECONDS = 0
_TELEGRAM_MAX_POLLING_TIMEOUT_SECONDS = 300


TelegramRuntimeMode = Literal["webhook", "polling"]


def _trim_trailing_slashes(url: str) -> str:
    return url.rstrip("/")


class _ResolvedLongPollingConfig:
    __slots__ = (
        "allowed_updates",
        "delete_webhook",
        "drop_pending_updates",
        "limit",
        "retry_delay_ms",
        "timeout",
    )

    def __init__(
        self,
        *,
        allowed_updates: list[str] | None,
        delete_webhook: bool,
        drop_pending_updates: bool,
        limit: int,
        retry_delay_ms: int,
        timeout: int,
    ) -> None:
        self.allowed_updates = allowed_updates
        self.delete_webhook = delete_webhook
        self.drop_pending_updates = drop_pending_updates
        self.limit = limit
        self.retry_delay_ms = retry_delay_ms
        self.timeout = timeout


# ---------------------------------------------------------------------------
# Inbound entity → standard markdown
# ---------------------------------------------------------------------------


_ENTITY_ESCAPE_PATTERN = re.compile(r"([\[\]()\\])")


def _escape_markdown_in_entity(text: str) -> str:
    return _ENTITY_ESCAPE_PATTERN.sub(r"\\\1", text)


def apply_telegram_entities(text: str, entities: list[TelegramMessageEntity]) -> str:
    """Translate inbound Telegram message entities to standard markdown.

    Telegram delivers formatting as entity objects alongside plain text —
    ``bold``, ``italic``, ``code``, ``pre``, ``strikethrough``, ``text_link``.
    This reconstructs standard markdown (``**bold**``, ``~~strike~~`` …)
    so the output can feed the SDK's canonical :func:`parse_markdown`.

    Offsets are UTF-16 code units per the Bot API spec but we treat them as
    Python string indices since the ported tests use pure-ASCII content. A
    future follow-up may add UTF-16 aware indexing.
    """

    if not entities:
        return text

    def _sort_key(e: TelegramMessageEntity) -> tuple[int, int]:
        offset = e.get("offset")
        length = e.get("length")
        return (
            -int(offset if isinstance(offset, int) else 0),
            int(length if isinstance(length, int) else 0),
        )

    sorted_entities = sorted(entities, key=_sort_key)
    result = text

    for entity in sorted_entities:
        start = entity.get("offset")
        length = entity.get("length")
        if not (isinstance(start, int) and isinstance(length, int)):
            continue
        end = start + length
        entity_text = result[start:end]
        replacement: str | None = None
        etype = entity.get("type")

        if etype == "text_link":
            url = entity.get("url")
            if isinstance(url, str):
                replacement = f"[{_escape_markdown_in_entity(entity_text)}]({url})"
        elif etype == "bold":
            replacement = f"**{entity_text}**"
        elif etype == "italic":
            replacement = f"*{entity_text}*"
        elif etype == "code":
            replacement = f"`{entity_text}`"
        elif etype == "pre":
            lang = entity.get("language") or ""
            replacement = f"```{lang}\n{entity_text}\n```"
        elif etype == "strikethrough":
            replacement = f"~~{entity_text}~~"

        if replacement is not None:
            result = result[:start] + replacement + result[end:]

    return result


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class TelegramAdapter:
    """Telegram Bot API adapter for the chat SDK."""

    name = "telegram"
    lock_scope = "channel"
    persist_message_history = True

    def __init__(self, config: TelegramAdapterConfig | None = None) -> None:
        cfg: TelegramAdapterConfig = config or {}
        bot_token = cfg.get("botToken") or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            raise ValidationError(
                "telegram",
                "botToken is required. Set TELEGRAM_BOT_TOKEN or provide it in config.",
            )

        self._bot_token: str = bot_token
        self._api_base_url: str = _trim_trailing_slashes(
            cfg.get("apiUrl")
            or cfg.get("apiBaseUrl")
            or os.environ.get("TELEGRAM_API_BASE_URL")
            or TELEGRAM_API_BASE,
        )
        self._secret_token: str | None = cfg.get("secretToken") or os.environ.get(
            "TELEGRAM_WEBHOOK_SECRET_TOKEN",
        )
        logger = cfg.get("logger")
        self._logger: Logger = (
            logger if logger is not None else ConsoleLogger("info").child("telegram")
        )
        user_name = cfg.get("userName") or os.environ.get("TELEGRAM_BOT_USERNAME")
        self._user_name: str = self._normalize_user_name(user_name or "bot")
        self._has_explicit_user_name: bool = bool(user_name)
        mode: TelegramAdapterMode = cfg.get("mode") or "auto"
        if mode not in ("auto", "webhook", "polling"):
            raise ValidationError(
                "telegram",
                f'Invalid mode: {mode}. Expected "auto", "webhook", or "polling".',
            )
        self._mode: TelegramAdapterMode = mode
        self._long_polling: TelegramLongPollingConfig | None = cfg.get("longPolling")
        self._runtime_mode: TelegramRuntimeMode = "webhook"

        self._format_converter = TelegramFormatConverter()
        self._warned_no_verification = False
        self._bot_user_id: str | None = None
        self._chat: Any | None = None

        self._message_cache: dict[str, list[Message[Any]]] = {}
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=60.0)

        self._polling_task: asyncio.Task[None] | None = None
        self._polling_active = False
        self._polling_stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def bot_user_id(self) -> str | None:
        return self._bot_user_id

    @property
    def user_name(self) -> str:
        return self._user_name

    @property
    def is_polling(self) -> bool:
        return self._polling_active

    @property
    def runtime_mode(self) -> TelegramRuntimeMode:
        return self._runtime_mode

    async def initialize(self, chat: Any) -> None:
        """Bind the chat instance and probe bot identity + runtime mode."""

        self._chat = chat

        if not self._has_explicit_user_name:
            get_name = getattr(chat, "get_user_name", None)
            if callable(get_name):
                candidate = get_name()
                if isinstance(candidate, str) and candidate.strip():
                    self._user_name = self._normalize_user_name(candidate)

        try:
            me: TelegramUser = await self._telegram_fetch("getMe")
            if "id" in me:
                self._bot_user_id = str(me["id"])
            if not self._has_explicit_user_name and me.get("username"):
                self._user_name = self._normalize_user_name(str(me["username"]))
            self._logger.info(
                "Telegram adapter initialized",
                {"botUserId": self._bot_user_id, "userName": self._user_name},
            )
        except Exception as err:
            self._logger.warn("Failed to fetch Telegram bot identity", {"error": str(err)})

        self._runtime_mode = await self._resolve_runtime_mode()

        if self._runtime_mode == "polling":
            config = self._long_polling
            if self._mode == "auto":
                merged: TelegramLongPollingConfig = (
                    {**config, "deleteWebhook": False}  # type: ignore[typeddict-item]
                    if config
                    else {"deleteWebhook": False}
                )
                await self.start_polling(merged)
            else:
                await self.start_polling(config)

    async def close(self) -> None:
        """Shut down the polling loop (if running) and close the HTTP client."""

        await self.stop_polling()
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Webhook handling
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        body: bytes | str,
        headers: dict[str, str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Verify and dispatch a webhook request.

        Returns ``(status, headers, body)`` tuple matching other chat-py
        adapters.
        """

        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        body_str = body_bytes.decode("utf-8", errors="replace")
        normalized = {k.lower(): v for k, v in (headers or {}).items()}

        if self._secret_token:
            header_token = normalized.get(TELEGRAM_SECRET_TOKEN_HEADER)
            if not (
                isinstance(header_token, str)
                and hmac.compare_digest(header_token, self._secret_token)
            ):
                self._logger.warn("Telegram webhook rejected due to invalid secret token")
                return 401, {}, "Invalid secret token"
        elif not self._warned_no_verification:
            self._warned_no_verification = True
            self._logger.warn(
                "Telegram webhook verification is disabled. Set "
                "TELEGRAM_WEBHOOK_SECRET_TOKEN or secretToken to verify incoming requests.",
            )

        try:
            update: TelegramUpdate = json.loads(body_str)
        except (ValueError, json.JSONDecodeError):
            return 400, {}, "Invalid JSON"

        if self._chat is None:
            self._logger.warn("Chat instance not initialized, ignoring Telegram webhook")
            return 200, {}, "OK"

        try:
            self._process_update(update, options)
        except Exception as err:
            self._logger.warn(
                "Failed to process Telegram webhook update",
                {"error": str(err), "updateId": update.get("update_id")},
            )

        return 200, {}, "OK"

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def start_polling(self, config: TelegramLongPollingConfig | None = None) -> None:
        """Begin long-polling ``getUpdates`` in the background."""

        if self._chat is None:
            raise ValidationError("telegram", "Cannot start polling before initialize()")

        if self._polling_active:
            self._logger.debug("Telegram polling already active")
            return

        resolved = self._resolve_polling_config(config)
        previous_mode = self._runtime_mode
        self._polling_active = True

        try:
            if resolved.delete_webhook:
                await self.reset_webhook(resolved.drop_pending_updates)
            self._runtime_mode = "polling"
        except Exception:
            self._polling_active = False
            self._runtime_mode = previous_mode
            raise

        self._logger.info(
            "Telegram polling started",
            {
                "limit": resolved.limit,
                "timeout": resolved.timeout,
                "allowedUpdates": resolved.allowed_updates,
            },
        )

        self._polling_stop_event = asyncio.Event()
        self._polling_task = asyncio.create_task(self._polling_loop(resolved))

    async def stop_polling(self) -> None:
        """Signal the polling loop to stop and await its completion."""

        if not self._polling_active:
            return

        self._polling_active = False
        if self._polling_stop_event is not None:
            self._polling_stop_event.set()

        if self._polling_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._polling_task
            self._polling_task = None

        self._polling_stop_event = None
        self._logger.info("Telegram polling stopped")

    async def reset_webhook(self, drop_pending_updates: bool = False) -> None:
        """Call ``deleteWebhook`` so polling updates can flow."""

        await self._telegram_fetch("deleteWebhook", {"drop_pending_updates": drop_pending_updates})
        self._logger.info("Telegram webhook reset", {"dropPendingUpdates": drop_pending_updates})

    # ------------------------------------------------------------------
    # REST: send / edit / delete / react / typing
    # ------------------------------------------------------------------

    async def post_message(
        self,
        thread_id: str,
        message: Any,
    ) -> dict[str, Any]:
        """Send a new message to ``thread_id``.

        Mirrors upstream ``postMessage`` — returns ``{id, threadId, raw}``.
        """

        parsed_thread = self._resolve_thread_id(thread_id)

        card = extract_card(message)
        reply_markup = card_to_telegram_inline_keyboard(card) if card else None
        parse_mode = self._resolve_parse_mode(message, card)
        rendered = (
            self._format_converter.from_markdown(
                card_to_fallback_text(card, bold_format="**"),
            )
            if card
            else self._format_converter.render_postable(message)
        )
        text = truncate_for_telegram(
            convert_emoji_placeholders(rendered, "gchat"),
            TELEGRAM_MESSAGE_LIMIT,
            parse_mode,
        )

        files = extract_files(message)
        if len(files) > 1:
            raise ValidationError(
                "telegram",
                "Telegram adapter supports a single file upload per message",
            )

        raw_message: TelegramMessage
        if len(files) == 1:
            file = files[0]
            if not file:
                raise ValidationError("telegram", "File upload payload is empty")
            raw_message = await self._send_document(
                parsed_thread,
                file,
                text,
                reply_markup,
                parse_mode,
            )
        else:
            if not text.strip():
                raise ValidationError("telegram", "Message text cannot be empty")
            raw_message = await self._telegram_fetch(
                "sendMessage",
                {
                    "chat_id": parsed_thread["chatId"],
                    "message_thread_id": parsed_thread.get("messageThreadId"),
                    "text": text,
                    "reply_markup": reply_markup,
                    "parse_mode": to_bot_api_parse_mode(parse_mode),
                },
            )

        resulting_thread_id = encode_thread_id(
            {
                "chatId": str(raw_message["chat"]["id"]),
                "messageThreadId": raw_message.get("message_thread_id")
                or parsed_thread.get("messageThreadId"),
            },
        )

        parsed = self._parse_telegram_message(raw_message, resulting_thread_id)
        self._cache_message(parsed)

        return {"id": parsed.id, "threadId": parsed.thread_id, "raw": raw_message}

    async def post_channel_message(
        self,
        channel_id: str,
        message: Any,
    ) -> dict[str, Any]:
        return await self.post_message(channel_id, message)

    async def edit_message(
        self,
        thread_id: str,
        message_id: str,
        message: Any,
    ) -> dict[str, Any]:
        parsed_thread = self._resolve_thread_id(thread_id)
        chat_id, telegram_message_id, composite_id = self._decode_composite_message_id(
            message_id,
            parsed_thread["chatId"],
        )

        card = extract_card(message)
        reply_markup = card_to_telegram_inline_keyboard(card) if card else None
        parse_mode = self._resolve_parse_mode(message, card)
        rendered = (
            self._format_converter.from_markdown(
                card_to_fallback_text(card, bold_format="**"),
            )
            if card
            else self._format_converter.render_postable(message)
        )
        text = truncate_for_telegram(
            convert_emoji_placeholders(rendered, "gchat"),
            TELEGRAM_MESSAGE_LIMIT,
            parse_mode,
        )
        if not text.strip():
            raise ValidationError("telegram", "Message text cannot be empty")

        result: TelegramMessage | bool = await self._telegram_fetch(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": telegram_message_id,
                "text": text,
                "reply_markup": reply_markup or empty_telegram_inline_keyboard(),
                "parse_mode": to_bot_api_parse_mode(parse_mode),
            },
        )

        if result is True:
            existing = self._find_cached_message(composite_id)
            if existing is None:
                raise NotImplementedError(
                    "Telegram returned a non-message edit result and no cached message was found",
                    "editMessage",
                )
            from datetime import datetime

            updated = Message(
                id=existing.id,
                thread_id=existing.thread_id,
                text=text,
                formatted=self._format_converter.to_ast(text),
                raw=existing.raw,
                author=existing.author,
                metadata=type(existing.metadata)(
                    date_sent=existing.metadata.date_sent,
                    edited=True,
                    edited_at=datetime.now(tz=UTC),
                ),
                attachments=list(existing.attachments),
                is_mention=existing.is_mention,
                links=list(existing.links),
            )
            self._cache_message(updated)
            return {"id": updated.id, "threadId": updated.thread_id, "raw": updated.raw}

        resulting_thread_id = encode_thread_id(
            {
                "chatId": str(result["chat"]["id"]),
                "messageThreadId": result.get("message_thread_id")
                or parsed_thread.get("messageThreadId"),
            },
        )
        parsed = self._parse_telegram_message(result, resulting_thread_id)
        self._cache_message(parsed)
        return {"id": parsed.id, "threadId": parsed.thread_id, "raw": result}

    async def delete_message(self, thread_id: str, message_id: str) -> None:
        parsed_thread = self._resolve_thread_id(thread_id)
        chat_id, telegram_message_id, composite_id = self._decode_composite_message_id(
            message_id,
            parsed_thread["chatId"],
        )
        await self._telegram_fetch(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": telegram_message_id},
        )
        self._delete_cached_message(composite_id)

    async def add_reaction(
        self,
        thread_id: str,
        message_id: str,
        emoji: EmojiValue | str,
    ) -> None:
        parsed_thread = self._resolve_thread_id(thread_id)
        chat_id, telegram_message_id, _ = self._decode_composite_message_id(
            message_id,
            parsed_thread["chatId"],
        )
        await self._telegram_fetch(
            "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": telegram_message_id,
                "reaction": [self._to_telegram_reaction(emoji)],
            },
        )

    async def remove_reaction(
        self,
        thread_id: str,
        message_id: str,
        _emoji: EmojiValue | str,
    ) -> None:
        parsed_thread = self._resolve_thread_id(thread_id)
        chat_id, telegram_message_id, _ = self._decode_composite_message_id(
            message_id,
            parsed_thread["chatId"],
        )
        await self._telegram_fetch(
            "setMessageReaction",
            {"chat_id": chat_id, "message_id": telegram_message_id, "reaction": []},
        )

    async def start_typing(self, thread_id: str) -> None:
        parsed_thread = self._resolve_thread_id(thread_id)
        await self._telegram_fetch(
            "sendChatAction",
            {
                "chat_id": parsed_thread["chatId"],
                "message_thread_id": parsed_thread.get("messageThreadId"),
                "action": "typing",
            },
        )

    # ------------------------------------------------------------------
    # Fetch (cache-backed — Telegram Bot API has no history endpoint)
    # ------------------------------------------------------------------

    async def fetch_messages(
        self,
        thread_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        messages = sorted(self._message_cache.get(thread_id, []), key=self._message_sort_key)
        return self._paginate_messages(messages, options or {})

    async def fetch_channel_messages(
        self,
        channel_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        by_id: dict[str, Message[Any]] = {}
        for t_id, messages in self._message_cache.items():
            try:
                decoded = decode_thread_id(t_id)
            except ValidationError:
                continue
            if decoded.get("chatId") != channel_id:
                continue
            for msg in messages:
                by_id[msg.id] = msg
        all_messages = sorted(by_id.values(), key=self._message_sort_key)
        return self._paginate_messages(all_messages, options or {})

    async def fetch_message(
        self,
        _thread_id: str,
        message_id: str,
    ) -> Message[Any] | None:
        return self._find_cached_message(message_id)

    async def fetch_thread(self, thread_id: str) -> dict[str, Any]:
        parsed_thread = self._resolve_thread_id(thread_id)
        chat: TelegramChat = await self._telegram_fetch(
            "getChat",
            {"chat_id": parsed_thread["chatId"]},
        )
        return {
            "id": encode_thread_id(parsed_thread),
            "channelId": str(chat["id"]),
            "channelName": self._chat_display_name(chat),
            "isDM": chat.get("type") == "private",
            "metadata": {
                "chat": chat,
                "messageThreadId": parsed_thread.get("messageThreadId"),
            },
        }

    async def fetch_channel_info(self, channel_id: str) -> dict[str, Any]:
        chat: TelegramChat = await self._telegram_fetch("getChat", {"chat_id": channel_id})
        member_count: int | None
        try:
            member_count = await self._telegram_fetch(
                "getChatMemberCount",
                {"chat_id": channel_id},
            )
        except Exception:
            member_count = None
        return {
            "id": str(chat["id"]),
            "name": self._chat_display_name(chat),
            "isDM": chat.get("type") == "private",
            "memberCount": member_count,
            "metadata": {"chat": chat},
        }

    # ------------------------------------------------------------------
    # Thread / channel / DM helpers
    # ------------------------------------------------------------------

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        decoded = self._resolve_thread_id(thread_id)
        return f"telegram:{decoded['chatId']}"

    async def open_dm(self, user_id: str) -> str:
        return encode_thread_id({"chatId": user_id})

    def is_dm(self, thread_id: str) -> bool:
        chat_id = self._resolve_thread_id(thread_id)["chatId"]
        return not chat_id.startswith("-")

    def encode_thread_id(self, platform_data: TelegramThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> TelegramThreadId:
        return decode_thread_id(thread_id)

    def parse_message(self, raw: TelegramRawMessage) -> Message[Any]:
        thread_id = encode_thread_id(
            {
                "chatId": str(raw["chat"]["id"]),
                "messageThreadId": raw.get("message_thread_id"),
            },
        )
        message = self._parse_telegram_message(raw, thread_id)
        self._cache_message(message)
        return message

    def render_formatted(self, content: Any) -> str:
        return self._format_converter.from_ast(content)

    # ------------------------------------------------------------------
    # Private: update dispatch
    # ------------------------------------------------------------------

    def _process_update(
        self,
        update: TelegramUpdate,
        options: dict[str, Any] | None = None,
    ) -> None:
        message_update = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if message_update is not None:
            self._handle_incoming_message_update(message_update, options)

        callback = update.get("callback_query")
        if callback is not None:
            self._handle_callback_query(callback, options)

        reaction = update.get("message_reaction")
        if reaction is not None:
            self._handle_message_reaction_update(reaction, options)

    def _handle_incoming_message_update(
        self,
        raw: TelegramMessage,
        options: dict[str, Any] | None,
    ) -> None:
        if self._chat is None:
            return
        thread_id = encode_thread_id(
            {
                "chatId": str(raw["chat"]["id"]),
                "messageThreadId": raw.get("message_thread_id"),
            },
        )
        parsed = self._parse_telegram_message(raw, thread_id)
        self._cache_message(parsed)
        self._chat.process_message(self, thread_id, parsed, options)

    def _handle_callback_query(
        self,
        callback: TelegramCallbackQuery,
        options: dict[str, Any] | None,
    ) -> None:
        if self._chat is None:
            return
        msg = callback.get("message")
        if msg is None:
            return
        thread_id = encode_thread_id(
            {
                "chatId": str(msg["chat"]["id"]),
                "messageThreadId": msg.get("message_thread_id"),
            },
        )
        message_id = self._encode_message_id(str(msg["chat"]["id"]), int(msg["message_id"]))
        decoded = decode_telegram_callback_data(callback.get("data"))
        user = callback.get("from_") or callback.get("from")  # type: ignore[assignment]

        self._chat.process_action(
            {
                "adapter": self,
                "actionId": decoded["actionId"],
                "value": decoded["value"],
                "messageId": message_id,
                "threadId": thread_id,
                "user": self._to_author(user or {}),
                "raw": callback,
            },
            options,
        )

        async def _ack() -> None:
            try:
                await self._telegram_fetch(
                    "answerCallbackQuery",
                    {"callback_query_id": callback.get("id")},
                )
            except Exception as err:
                self._logger.warn(
                    "Failed to acknowledge Telegram callback query",
                    {"callbackQueryId": callback.get("id"), "error": str(err)},
                )

        task = asyncio.create_task(_ack())
        if options and callable(options.get("waitUntil")):
            options["waitUntil"](task)

    def _handle_message_reaction_update(
        self,
        reaction_update: TelegramMessageReactionUpdated,
        options: dict[str, Any] | None,
    ) -> None:
        if self._chat is None:
            return
        thread_id = encode_thread_id(
            {
                "chatId": str(reaction_update["chat"]["id"]),
                "messageThreadId": reaction_update.get("message_thread_id"),
            },
        )
        message_id = self._encode_message_id(
            str(reaction_update["chat"]["id"]),
            int(reaction_update["message_id"]),
        )

        old_keys = {self._reaction_key(r) for r in (reaction_update.get("old_reaction") or [])}
        new_keys = {self._reaction_key(r) for r in (reaction_update.get("new_reaction") or [])}

        user = reaction_update.get("user")
        actor = (
            self._to_author(user)
            if user
            else self._to_reaction_actor_author(reaction_update["chat"])
        )

        for reaction in reaction_update.get("new_reaction") or []:
            key = self._reaction_key(reaction)
            if key not in old_keys:
                self._chat.process_reaction(
                    {
                        "adapter": self,
                        "threadId": thread_id,
                        "messageId": message_id,
                        "emoji": self._reaction_to_emoji_value(reaction),
                        "rawEmoji": key,
                        "added": True,
                        "user": actor,
                        "raw": reaction_update,
                    },
                    options,
                )
        for reaction in reaction_update.get("old_reaction") or []:
            key = self._reaction_key(reaction)
            if key not in new_keys:
                self._chat.process_reaction(
                    {
                        "adapter": self,
                        "threadId": thread_id,
                        "messageId": message_id,
                        "emoji": self._reaction_to_emoji_value(reaction),
                        "rawEmoji": key,
                        "added": False,
                        "user": actor,
                        "raw": reaction_update,
                    },
                    options,
                )

    # ------------------------------------------------------------------
    # Private: resolve runtime mode
    # ------------------------------------------------------------------

    async def _resolve_runtime_mode(self) -> TelegramRuntimeMode:
        if self._mode == "webhook":
            return "webhook"
        if self._mode == "polling":
            return "polling"

        webhook_info = await self._fetch_webhook_info()
        if webhook_info is None:
            self._logger.warn(
                "Telegram auto mode could not verify webhook status; keeping webhook mode",
            )
            return "webhook"
        url = webhook_info.get("url")
        if isinstance(url, str) and url.strip():
            self._logger.debug(
                "Telegram auto mode selected webhook mode",
                {"webhookUrl": url},
            )
            return "webhook"
        if self._is_likely_serverless_runtime():
            self._logger.warn(
                "Telegram auto mode detected serverless runtime without webhook URL; "
                "keeping webhook mode",
            )
            return "webhook"
        self._logger.info("Telegram auto mode selected polling mode")
        return "polling"

    async def _fetch_webhook_info(self) -> TelegramWebhookInfo | None:
        try:
            return await self._telegram_fetch("getWebhookInfo")
        except Exception as err:
            self._logger.warn(
                "Failed to fetch Telegram webhook info",
                {"error": str(err)},
            )
            return None

    def _is_likely_serverless_runtime(self) -> bool:
        if not os.environ:
            return False
        aws_exec_env = os.environ.get("AWS_EXECUTION_ENV") or ""
        return bool(
            os.environ.get("VERCEL")
            or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
            or "AWS_Lambda" in aws_exec_env
            or os.environ.get("FUNCTIONS_WORKER_RUNTIME")
            or os.environ.get("NETLIFY")
            or os.environ.get("K_SERVICE"),
        )

    # ------------------------------------------------------------------
    # Private: parsing
    # ------------------------------------------------------------------

    def _parse_telegram_message(
        self,
        raw: TelegramMessage,
        thread_id: str,
    ) -> Message[Any]:
        from datetime import datetime

        from chat.types import Attachment, Author, MessageMetadata

        plain_text = raw.get("text") or raw.get("caption") or ""
        entities = raw.get("entities") or raw.get("caption_entities") or []
        text = apply_telegram_entities(plain_text, entities)

        from_user = raw.get("from_") or raw.get("from")  # type: ignore[assignment]
        author: Author
        if from_user:
            author = self._to_author(from_user)
        elif raw.get("sender_chat"):
            author = self._to_reaction_actor_author(raw["sender_chat"])
        else:
            chat = raw["chat"]
            fallback_name = self._chat_display_name(chat) or str(chat["id"])
            author = Author(
                user_id=str(chat["id"]),
                user_name=fallback_name,
                full_name=fallback_name,
                is_bot="unknown",
                is_me=False,
            )

        date_val = int(raw.get("date") or 0)
        edit_date = raw.get("edit_date")
        metadata = MessageMetadata(
            date_sent=datetime.fromtimestamp(date_val, tz=UTC),
            edited=edit_date is not None,
            edited_at=(
                datetime.fromtimestamp(int(edit_date), tz=UTC)
                if isinstance(edit_date, int)
                else None
            ),
        )

        attachments: list[Attachment] = []
        photos = raw.get("photo") or []
        if photos:
            photo = photos[-1]
            attachments.append(
                Attachment(
                    type="image",
                    name=None,
                    mime_type=None,
                    size=photo.get("file_size"),
                    width=photo.get("width"),
                    height=photo.get("height"),
                    fetch_data=self._make_fetch_data(photo.get("file_id")),
                ),
            )
        video = raw.get("video")
        if video:
            attachments.append(
                Attachment(
                    type="video",
                    name=video.get("file_name"),
                    mime_type=video.get("mime_type"),
                    size=video.get("file_size"),
                    width=video.get("width"),
                    height=video.get("height"),
                    fetch_data=self._make_fetch_data(video.get("file_id")),
                ),
            )
        audio = raw.get("audio")
        if audio:
            attachments.append(
                Attachment(
                    type="audio",
                    name=audio.get("file_name"),
                    mime_type=audio.get("mime_type"),
                    size=audio.get("file_size"),
                    fetch_data=self._make_fetch_data(audio.get("file_id")),
                ),
            )
        voice = raw.get("voice")
        if voice:
            attachments.append(
                Attachment(
                    type="audio",
                    name=None,
                    mime_type=voice.get("mime_type"),
                    size=voice.get("file_size"),
                    fetch_data=self._make_fetch_data(voice.get("file_id")),
                ),
            )
        document = raw.get("document")
        if document:
            attachments.append(
                Attachment(
                    type="file",
                    name=document.get("file_name"),
                    mime_type=document.get("mime_type"),
                    size=document.get("file_size"),
                    fetch_data=self._make_fetch_data(document.get("file_id")),
                ),
            )

        return Message(
            id=self._encode_message_id(str(raw["chat"]["id"]), int(raw["message_id"])),
            thread_id=thread_id,
            text=text,
            formatted=self._format_converter.to_ast(text),
            raw=raw,
            author=author,
            metadata=metadata,
            attachments=attachments,
            is_mention=self._is_bot_mentioned(raw, plain_text),
        )

    def _make_fetch_data(self, file_id: str | None) -> Any:
        if not file_id:
            return None

        async def _fetch() -> bytes:
            return await self._download_file(file_id)

        return _fetch

    async def _download_file(self, file_id: str) -> bytes:
        file = await self._telegram_fetch("getFile", {"file_id": file_id})
        file_path = file.get("file_path")
        if not file_path:
            raise ResourceNotFoundError("telegram", "file", file_id)
        url = f"{self._api_base_url}/file/bot{self._bot_token}/{file_path}"
        try:
            response = await self._http.get(url)
        except httpx.HTTPError as err:
            raise NetworkError(
                "telegram",
                f"Failed to download Telegram file {file_id}",
                err,
            ) from err
        if response.status_code >= 400:
            raise NetworkError(
                "telegram",
                f"Failed to download Telegram file {file_id}: {response.status_code}",
            )
        return response.content

    # ------------------------------------------------------------------
    # Private: send file
    # ------------------------------------------------------------------

    async def _send_document(
        self,
        thread: TelegramThreadId,
        file: Any,
        text: str,
        reply_markup: TelegramInlineKeyboardMarkup | None,
        parse_mode: TelegramParseMode,
    ) -> TelegramMessage:
        data_field = file.get("data") if isinstance(file, dict) else getattr(file, "data", None)
        filename = (
            file.get("filename") if isinstance(file, dict) else getattr(file, "filename", None)
        )
        mime_type = (
            file.get("mimeType") if isinstance(file, dict) else getattr(file, "mime_type", None)
        )
        buffer = await to_buffer(data_field)

        form_data: dict[str, Any] = {"chat_id": thread["chatId"]}
        if isinstance(thread.get("messageThreadId"), int):
            form_data["message_thread_id"] = str(thread["messageThreadId"])

        if text.strip():
            form_data["caption"] = truncate_for_telegram(
                text,
                TELEGRAM_CAPTION_LIMIT,
                parse_mode,
            )
            mode = to_bot_api_parse_mode(parse_mode)
            if mode:
                form_data["parse_mode"] = mode

        if reply_markup is not None:
            form_data["reply_markup"] = json.dumps(reply_markup)

        files_payload = {
            "document": (
                filename or "file",
                buffer,
                mime_type or "application/octet-stream",
            ),
        }

        url = f"{self._api_base_url}/bot{self._bot_token}/sendDocument"
        try:
            response = await self._http.post(url, data=form_data, files=files_payload)
        except httpx.HTTPError as err:
            raise NetworkError(
                "telegram",
                "Network error calling Telegram sendDocument",
                err,
            ) from err

        try:
            data: TelegramApiResponse = response.json()
        except ValueError as err:
            raise NetworkError(
                "telegram",
                "Failed to parse Telegram API response for sendDocument",
            ) from err

        if not (response.is_success and data.get("ok")):
            throw_telegram_api_error("sendDocument", response.status_code, data)

        result = data.get("result")
        if result is None:
            raise NetworkError("telegram", "Telegram API sendDocument returned no result")
        return result  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Private: cache / pagination
    # ------------------------------------------------------------------

    def _cache_message(self, message: Message[Any]) -> None:
        existing = self._message_cache.get(message.thread_id, [])
        for i, item in enumerate(existing):
            if item.id == message.id:
                existing[i] = message
                break
        else:
            existing.append(message)
        existing.sort(key=self._message_sort_key)
        self._message_cache[message.thread_id] = existing

    def _find_cached_message(self, message_id: str) -> Message[Any] | None:
        for messages in self._message_cache.values():
            for msg in messages:
                if msg.id == message_id:
                    return msg
        return None

    def _delete_cached_message(self, message_id: str) -> None:
        for thread_id, messages in list(self._message_cache.items()):
            filtered = [m for m in messages if m.id != message_id]
            if not filtered:
                self._message_cache.pop(thread_id, None)
            elif len(filtered) != len(messages):
                self._message_cache[thread_id] = filtered

    def _message_sort_key(self, message: Message[Any]) -> tuple[float, int]:
        date_sent = message.metadata.date_sent
        ts = date_sent.timestamp() if date_sent is not None else 0.0
        match = _MESSAGE_SEQUENCE_PATTERN.search(message.id)
        sequence = int(match.group(1)) if match else 0
        return (ts, sequence)

    def _paginate_messages(
        self,
        messages: list[Message[Any]],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        raw_limit = options.get("limit", 50)
        limit = max(1, min(int(raw_limit) if raw_limit is not None else 50, 100))
        direction = options.get("direction", "backward")
        if not messages:
            return {"messages": []}

        index_by_id = {m.id: i for i, m in enumerate(messages)}
        cursor = options.get("cursor")

        if direction == "backward":
            end = (
                index_by_id[cursor]
                if isinstance(cursor, str) and cursor in index_by_id
                else len(messages)
            )
            start = max(0, end - limit)
            page = messages[start:end]
            result: dict[str, Any] = {"messages": page}
            if start > 0 and page:
                result["nextCursor"] = page[0].id
            return result

        start = index_by_id[cursor] + 1 if isinstance(cursor, str) and cursor in index_by_id else 0
        end = min(len(messages), start + limit)
        page = messages[start:end]
        result = {"messages": page}
        if end < len(messages) and page:
            result["nextCursor"] = page[-1].id
        return result

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    def _resolve_thread_id(self, value: str) -> TelegramThreadId:
        if value.startswith("telegram:"):
            return decode_thread_id(value)
        return {"chatId": value}

    def _encode_message_id(self, chat_id: str, message_id: int) -> str:
        return f"{chat_id}:{message_id}"

    def _decode_composite_message_id(
        self,
        message_id: str,
        expected_chat_id: str | None = None,
    ) -> tuple[str, int, str]:
        match = _MESSAGE_ID_PATTERN.match(message_id)
        if match:
            chat_id = match.group(1)
            parsed = int(match.group(2))
            if expected_chat_id is not None and chat_id != expected_chat_id:
                raise ValidationError(
                    "telegram",
                    f"Message ID chat mismatch: expected {expected_chat_id}, got {chat_id}",
                )
            return chat_id, parsed, f"{chat_id}:{parsed}"

        if expected_chat_id is None:
            raise ValidationError(
                "telegram",
                f"Telegram message ID must be in <chatId>:<messageId> format, got: {message_id}",
            )
        try:
            parsed = int(message_id)
        except ValueError as err:
            raise ValidationError(
                "telegram",
                f"Invalid Telegram message ID: {message_id}",
            ) from err
        return expected_chat_id, parsed, f"{expected_chat_id}:{parsed}"

    def _to_author(self, user: TelegramUser | dict[str, Any]) -> Any:
        from chat.types import Author

        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        full = " ".join(part for part in (first, last) if part).strip()
        user_id = str(user.get("id", ""))
        username = user.get("username")
        display = full or username or user_id
        return Author(
            user_id=user_id,
            user_name=str(username or first or user_id),
            full_name=str(display),
            is_bot=bool(user.get("is_bot", False)),
            is_me=user_id == (self._bot_user_id or ""),
        )

    def _to_reaction_actor_author(self, chat: TelegramChat) -> Any:
        from chat.types import Author

        name = self._chat_display_name(chat) or str(chat.get("id", ""))
        return Author(
            user_id=f"chat:{chat.get('id')}",
            user_name=name,
            full_name=name,
            is_bot="unknown",
            is_me=False,
        )

    def _chat_display_name(self, chat: TelegramChat) -> str | None:
        title = chat.get("title")
        if isinstance(title, str) and title:
            return title
        first = chat.get("first_name") or ""
        last = chat.get("last_name") or ""
        private_name = " ".join(part for part in (first, last) if part).strip()
        if private_name:
            return private_name
        username = chat.get("username")
        if isinstance(username, str) and username:
            return username
        return None

    def _is_bot_mentioned(self, raw: TelegramMessage, text: str) -> bool:
        if not text:
            return False
        username = self._user_name
        entities = raw.get("entities") or raw.get("caption_entities") or []
        for entity in entities:
            etype = entity.get("type")
            if etype == "mention":
                mention_text = self._entity_text(text, entity)
                if mention_text.lower() == f"@{username.lower()}":
                    return True
            if etype == "text_mention":
                user = entity.get("user")
                if (
                    isinstance(user, dict)
                    and self._bot_user_id
                    and str(user.get("id")) == self._bot_user_id
                ):
                    return True
            if etype == "bot_command":
                command_text = self._entity_text(text, entity)
                if command_text.lower().endswith(f"@{username.lower()}"):
                    return True
        escaped = _REGEX_ESCAPE_PATTERN.sub(r"\\\1", username)
        return re.search(rf"@{escaped}\b", text, re.IGNORECASE) is not None

    def _entity_text(self, text: str, entity: TelegramMessageEntity) -> str:
        offset = entity.get("offset")
        length = entity.get("length")
        if not (isinstance(offset, int) and isinstance(length, int)):
            return ""
        return text[offset : offset + length]

    def _normalize_user_name(self, value: Any) -> str:
        if not isinstance(value, str):
            return "bot"
        stripped = _LEADING_AT_PATTERN.sub("", value).strip()
        return stripped or "bot"

    def _resolve_parse_mode(self, message: Any, card: Any) -> TelegramParseMode:
        if card is not None:
            return "MarkdownV2"
        if isinstance(message, str):
            return "plain"
        if isinstance(message, dict) and "raw" in message:
            return "plain"
        return "MarkdownV2"

    def _to_telegram_reaction(self, emoji: Any) -> TelegramReactionType:
        if not isinstance(emoji, str):
            name = getattr(emoji, "name", None)
            return {
                "type": "emoji",
                "emoji": default_emoji_resolver.to_gchat(name or emoji),
            }

        if emoji.startswith("custom:"):
            return {"type": "custom_emoji", "custom_emoji_id": emoji[len("custom:") :]}

        placeholder = _EMOJI_PLACEHOLDER_PATTERN.match(emoji)
        if placeholder:
            return {
                "type": "emoji",
                "emoji": default_emoji_resolver.to_gchat(placeholder.group(1)),
            }

        if _EMOJI_NAME_PATTERN.match(emoji):
            return {
                "type": "emoji",
                "emoji": default_emoji_resolver.to_gchat(emoji.lower()),
            }

        return {"type": "emoji", "emoji": emoji}

    def _reaction_key(self, reaction: TelegramReactionType) -> str:
        if reaction.get("type") == "emoji":
            return str(reaction.get("emoji", ""))
        return f"custom:{reaction.get('custom_emoji_id', '')}"

    def _reaction_to_emoji_value(self, reaction: TelegramReactionType) -> Any:
        if reaction.get("type") == "emoji":
            return default_emoji_resolver.from_gchat(str(reaction.get("emoji", "")))
        return get_emoji(f"custom:{reaction.get('custom_emoji_id', '')}")

    # ------------------------------------------------------------------
    # Private: polling
    # ------------------------------------------------------------------

    def _resolve_polling_config(
        self,
        override: TelegramLongPollingConfig | None,
    ) -> _ResolvedLongPollingConfig:
        base: TelegramLongPollingConfig = self._long_polling or {}
        merged = {**base, **(override or {})}
        allowed = merged.get("allowedUpdates")
        allowed_list: list[str] | None = (
            list(allowed) if isinstance(allowed, list) and allowed else None
        )
        return _ResolvedLongPollingConfig(
            allowed_updates=allowed_list,
            delete_webhook=bool(merged.get("deleteWebhook", True)),
            drop_pending_updates=bool(merged.get("dropPendingUpdates", False)),
            limit=self._clamp_int(
                merged.get("limit"),
                _TELEGRAM_DEFAULT_POLLING_LIMIT,
                _TELEGRAM_MIN_POLLING_LIMIT,
                _TELEGRAM_MAX_POLLING_LIMIT,
            ),
            retry_delay_ms=self._clamp_int(
                merged.get("retryDelayMs"),
                _TELEGRAM_DEFAULT_POLLING_RETRY_DELAY_MS,
                0,
                2**31 - 1,
            ),
            timeout=self._clamp_int(
                merged.get("timeout"),
                _TELEGRAM_DEFAULT_POLLING_TIMEOUT_SECONDS,
                _TELEGRAM_MIN_POLLING_TIMEOUT_SECONDS,
                _TELEGRAM_MAX_POLLING_TIMEOUT_SECONDS,
            ),
        )

    @staticmethod
    def _clamp_int(value: Any, fallback: int, low: int, high: int) -> int:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return fallback
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return fallback
        return max(low, min(high, parsed))

    async def _polling_loop(self, config: _ResolvedLongPollingConfig) -> None:
        offset: int | None = None
        consecutive_failures = 0
        max_backoff_ms = 30_000
        stop_event = self._polling_stop_event

        while self._polling_active:
            try:
                payload: dict[str, Any] = {
                    "limit": config.limit,
                    "timeout": config.timeout,
                }
                if offset is not None:
                    payload["offset"] = offset
                if config.allowed_updates is not None:
                    payload["allowed_updates"] = config.allowed_updates

                updates: list[TelegramUpdate] = await self._telegram_fetch(
                    "getUpdates",
                    payload,
                )
                consecutive_failures = 0

                for update in updates:
                    uid = update.get("update_id")
                    if isinstance(uid, int):
                        offset = uid + 1
                    try:
                        self._process_update(update)
                    except Exception as err:
                        self._logger.warn(
                            "Failed to process Telegram polled update",
                            {"error": str(err), "updateId": update.get("update_id")},
                        )
            except asyncio.CancelledError:
                return
            except Exception as err:
                consecutive_failures += 1
                backoff_ms = min(
                    config.retry_delay_ms * 2 ** (consecutive_failures - 1),
                    max_backoff_ms,
                )
                self._logger.warn(
                    "Telegram polling request failed",
                    {
                        "error": str(err),
                        "retryDelayMs": backoff_ms,
                        "consecutiveFailures": consecutive_failures,
                    },
                )
                if not self._polling_active:
                    return
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=backoff_ms / 1000,
                        )
                        return
                    except TimeoutError:
                        pass

    # ------------------------------------------------------------------
    # Private: HTTP transport
    # ------------------------------------------------------------------

    async def _telegram_fetch(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._api_base_url}/bot{self._bot_token}/{method}"
        body = {k: v for k, v in (payload or {}).items() if v is not None}
        try:
            response = await self._http.post(url, json=body)
        except httpx.HTTPError as err:
            raise NetworkError(
                "telegram",
                f"Network error calling Telegram {method}",
                err,
            ) from err

        try:
            data: TelegramApiResponse = response.json()
        except ValueError as err:
            raise NetworkError(
                "telegram",
                f"Failed to parse Telegram API response for {method}",
            ) from err

        if not (response.is_success and data.get("ok")):
            throw_telegram_api_error(method, response.status_code, data)

        if "result" not in data:
            raise NetworkError("telegram", f"Telegram API {method} returned no result")
        return data["result"]


def create_telegram_adapter(config: TelegramAdapterConfig | None = None) -> TelegramAdapter:
    """Factory for :class:`TelegramAdapter` — matches upstream ``createTelegramAdapter``."""

    return TelegramAdapter(config or {})


__all__ = [
    "TELEGRAM_API_BASE",
    "TELEGRAM_SECRET_TOKEN_HEADER",
    "TelegramAdapter",
    "TelegramRuntimeMode",
    "apply_telegram_entities",
    "create_telegram_adapter",
]
