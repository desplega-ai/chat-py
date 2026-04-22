"""WhatsApp adapter facade.

Python port of upstream ``packages/adapter-whatsapp/src/index.ts``.

Covers the WhatsApp Cloud API surface: outbound text + interactive
messages, reactions, ``markAsRead``, media downloads, and inbound webhook
dispatch (``messages`` field). Webhook GET requests are answered via the
Meta ``hub.verify_token`` challenge; POST requests are verified using
HMAC-SHA256 against the App Secret with a constant-time comparison.

WhatsApp does **not** support editing or deleting messages — those entry
points raise :class:`chat.NotImplementedError`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import httpx
from chat import (
    ConsoleLogger,
    Message,
    NotImplementedError,
    convert_emoji_placeholders,
    default_emoji_resolver,
    get_emoji,
)
from chat.types import Attachment, Author, MessageMetadata
from chat_adapter_shared import (
    NetworkError,
    ValidationError,
    extract_card,
)

from .cards import card_to_whatsapp, decode_whatsapp_callback_data
from .errors import throw_whatsapp_api_error
from .markdown import WhatsAppFormatConverter
from .thread_id import decode_thread_id, encode_thread_id
from .types import (
    WhatsAppAdapterConfig,
    WhatsAppContact,
    WhatsAppInboundMessage,
    WhatsAppInteractiveMessage,
    WhatsAppMediaResponse,
    WhatsAppRawMessage,
    WhatsAppSendResponse,
    WhatsAppThreadId,
    WhatsAppWebhookPayload,
)

if TYPE_CHECKING:
    from chat import EmojiValue, Logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_API_VERSION = "v21.0"
DEFAULT_API_BASE_URL = "https://graph.facebook.com"

# Maximum body length of a single WhatsApp Cloud API text message.
WHATSAPP_MESSAGE_LIMIT = 4096


def split_message(text: str) -> list[str]:
    """Split text into chunks that fit WhatsApp's 4096-character limit.

    Tries to break on paragraph (``\\n\\n``) then line (``\\n``) boundaries
    that fall in the second half of the chunk; otherwise falls back to a
    hard break at the limit.
    """

    if len(text) <= WHATSAPP_MESSAGE_LIMIT:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > WHATSAPP_MESSAGE_LIMIT:
        slice_ = remaining[:WHATSAPP_MESSAGE_LIMIT]

        break_index = slice_.rfind("\n\n")
        if break_index == -1 or break_index < WHATSAPP_MESSAGE_LIMIT // 2:
            break_index = slice_.rfind("\n")
        if break_index == -1 or break_index < WHATSAPP_MESSAGE_LIMIT // 2:
            break_index = WHATSAPP_MESSAGE_LIMIT

        chunks.append(remaining[:break_index].rstrip())
        remaining = remaining[break_index:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class WhatsAppAdapter:
    """WhatsApp Cloud API adapter for the chat SDK.

    All conversations are 1:1 DMs between the business phone number and
    end users.
    """

    name = "whatsapp"
    lock_scope = "channel"
    persist_message_history = True

    def __init__(self, config: WhatsAppAdapterConfig) -> None:
        cfg: dict[str, Any] = dict(config or {})

        access_token = cfg.get("accessToken")
        if not access_token:
            raise ValidationError(
                "whatsapp",
                "accessToken is required.",
            )
        app_secret = cfg.get("appSecret")
        if not app_secret:
            raise ValidationError("whatsapp", "appSecret is required.")
        phone_number_id = cfg.get("phoneNumberId")
        if not phone_number_id:
            raise ValidationError("whatsapp", "phoneNumberId is required.")
        verify_token = cfg.get("verifyToken")
        if not verify_token:
            raise ValidationError("whatsapp", "verifyToken is required.")

        self._access_token: str = str(access_token)
        self._app_secret: str = str(app_secret)
        self._phone_number_id: str = str(phone_number_id)
        self._verify_token: str = str(verify_token)

        api_version = cfg.get("apiVersion") or DEFAULT_API_VERSION
        base_url = cfg.get("apiUrl") or DEFAULT_API_BASE_URL
        self._graph_api_url: str = f"{str(base_url).rstrip('/')}/{api_version}"

        logger = cfg.get("logger")
        self._logger: Logger = (
            logger if logger is not None else ConsoleLogger("info").child("whatsapp")
        )
        self._user_name: str = str(cfg.get("userName") or "whatsapp-bot")

        self._format_converter = WhatsAppFormatConverter()
        self._chat: Any | None = None
        self._bot_user_id: str | None = None
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=60.0)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def bot_user_id(self) -> str | None:
        return self._bot_user_id

    @property
    def user_name(self) -> str:
        return self._user_name

    async def initialize(self, chat: Any) -> None:
        """Bind the chat instance and seed the bot's user ID."""

        self._chat = chat
        self._bot_user_id = self._phone_number_id
        self._logger.info(
            "WhatsApp adapter initialized",
            {"phoneNumberId": self._phone_number_id},
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""

        await self._http.aclose()

    async def disconnect(self) -> None:
        """Release the HTTP client. Alias for :meth:`close`."""

        await self.close()

    # ------------------------------------------------------------------
    # Subscriptions / modals
    #
    # WhatsApp Cloud API has no native per-thread subscription surface
    # (subscription is tracked in chat state) and no modal surface. The
    # methods below exist only to satisfy :class:`chat.types.Adapter`.
    # ------------------------------------------------------------------

    async def subscribe(self, _thread_id: str) -> None:
        """No-op — WhatsApp subscription is tracked at the ``Chat`` state layer."""

        return None

    async def unsubscribe(self, _thread_id: str) -> None:
        """No-op — mirrors :meth:`subscribe`."""

        return None

    async def open_modal(self, _trigger_id: str, _view: Any) -> Any:
        """WhatsApp has no modal surface; use interactive messages instead."""

        raise NotImplementedError(
            "WhatsApp has no modal surface; use interactive messages (buttons / list) instead.",
            "open_modal",
        )

    # ------------------------------------------------------------------
    # Channel-level messaging
    #
    # WhatsApp Cloud API is 1:1 DM-only; there is no channel surface. These
    # methods satisfy :class:`chat.types.Adapter` but raise
    # :class:`chat.NotImplementedError`, pinned by
    # ``tests/test_unsupported_features.py`` and documented in
    # ``docs/parity.md``.
    # ------------------------------------------------------------------

    async def post_channel_message(self, _channel_id: str, _message: Any) -> Any:
        raise NotImplementedError(
            "WhatsApp has no channel-level post surface; WhatsApp Cloud API is DM-only.",
            "post_channel_message",
        )

    async def fetch_channel_info(self, _channel_id: str) -> Any:
        raise NotImplementedError(
            "WhatsApp has no channel-info surface; WhatsApp Cloud API is DM-only.",
            "fetch_channel_info",
        )

    async def fetch_channel_messages(
        self,
        _channel_id: str,
        _options: Any | None = None,
    ) -> Any:
        raise NotImplementedError(
            "WhatsApp has no channel-message stream; WhatsApp Cloud API is DM-only.",
            "fetch_channel_messages",
        )

    async def list_threads(
        self,
        _channel_id: str,
        _options: Any | None = None,
    ) -> Any:
        raise NotImplementedError(
            "WhatsApp has no channel / thread-listing surface; WhatsApp Cloud API is DM-only.",
            "list_threads",
        )

    # ------------------------------------------------------------------
    # Webhook handling
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        body: bytes | str,
        headers: dict[str, str] | None = None,
        options: dict[str, Any] | None = None,
        method: str = "POST",
        url: str | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Verify and dispatch a webhook request.

        For ``GET`` requests this answers the Meta verification challenge
        (``hub.mode=subscribe`` + matching ``hub.verify_token``). For
        ``POST`` requests it verifies the ``X-Hub-Signature-256`` HMAC
        before dispatching ``messages`` field changes to the chat instance.

        Returns ``(status, headers, body)`` matching other chat-py adapters.
        """

        if method.upper() == "GET":
            return self._handle_verification_challenge(url or "")

        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        body_str = body_bytes.decode("utf-8", errors="replace")
        normalized = {k.lower(): v for k, v in (headers or {}).items()}

        signature = normalized.get("x-hub-signature-256")
        if not self._verify_signature(body_bytes, signature):
            self._logger.warn("WhatsApp webhook rejected due to invalid signature")
            return 401, {}, "Invalid signature"

        try:
            payload: WhatsAppWebhookPayload = json.loads(body_str)
        except (ValueError, json.JSONDecodeError):
            self._logger.error(
                "WhatsApp webhook invalid JSON",
                {"contentType": normalized.get("content-type")},
            )
            return 400, {}, "Invalid JSON"

        if self._chat is None:
            self._logger.warn("Chat instance not initialized, ignoring WhatsApp webhook")
            return 200, {}, "ok"

        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                if change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                messages = value.get("messages") or []
                contacts = value.get("contacts") or []
                metadata = value.get("metadata") or {}
                for message in messages:
                    try:
                        self._handle_inbound_message(
                            message,
                            contacts[0] if contacts else None,
                            str(metadata.get("phone_number_id") or self._phone_number_id),
                            options,
                        )
                    except Exception as err:
                        self._logger.error(
                            "Failed to handle inbound message",
                            {"messageId": message.get("id"), "error": str(err)},
                        )

        return 200, {}, "ok"

    # ------------------------------------------------------------------
    # Webhook helpers
    # ------------------------------------------------------------------

    def _handle_verification_challenge(self, url: str) -> tuple[int, dict[str, str], str]:
        params = parse_qs(urlsplit(url).query)
        mode = (params.get("hub.mode") or [""])[0]
        token = (params.get("hub.verify_token") or [""])[0]
        challenge = (params.get("hub.challenge") or [""])[0]

        if mode == "subscribe" and token == self._verify_token:
            self._logger.info("WhatsApp webhook verification succeeded")
            return 200, {}, challenge

        self._logger.warn(
            "WhatsApp webhook verification failed",
            {"mode": mode, "tokenMatch": token == self._verify_token},
        )
        return 403, {}, "Forbidden"

    def _verify_signature(self, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        try:
            digest = hmac.new(self._app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        except (TypeError, ValueError):
            return False
        expected = f"sha256={digest}"
        try:
            return hmac.compare_digest(signature, expected)
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Inbound message dispatch
    # ------------------------------------------------------------------

    def _handle_inbound_message(
        self,
        inbound: WhatsAppInboundMessage,
        contact: WhatsAppContact | None,
        phone_number_id: str,
        options: dict[str, Any] | None,
    ) -> None:
        if self._chat is None:
            self._logger.warn("Chat instance not initialized, ignoring message")
            return

        msg_type = inbound.get("type")

        if msg_type == "reaction" and inbound.get("reaction"):
            self._handle_reaction(inbound, contact, phone_number_id, options)
            return

        if msg_type == "interactive" and inbound.get("interactive"):
            self._handle_interactive_reply(inbound, contact, phone_number_id, options)
            return

        if msg_type == "button" and inbound.get("button"):
            self._handle_button_response(inbound, contact, phone_number_id, options)
            return

        text = self._extract_text_content(inbound)
        if text is None:
            self._logger.debug(
                "Unsupported message type, ignoring",
                {"type": msg_type, "messageId": inbound.get("id")},
            )
            return

        sender = inbound.get("from_") or inbound.get("from") or ""  # type: ignore[call-overload]
        thread_id = encode_thread_id(
            {"phoneNumberId": phone_number_id, "userWaId": str(sender)},
        )
        message = self._build_message(inbound, contact, thread_id, text, phone_number_id)
        self._chat.process_message(self, thread_id, message, options)

    def _handle_reaction(
        self,
        inbound: WhatsAppInboundMessage,
        contact: WhatsAppContact | None,
        phone_number_id: str,
        options: dict[str, Any] | None,
    ) -> None:
        reaction = inbound.get("reaction")
        if self._chat is None or not reaction:
            return

        sender = inbound.get("from_") or inbound.get("from") or ""  # type: ignore[call-overload]
        thread_id = encode_thread_id(
            {"phoneNumberId": phone_number_id, "userWaId": str(sender)},
        )

        raw_emoji = str(reaction.get("emoji", ""))
        added = raw_emoji != ""
        emoji_value = get_emoji(raw_emoji) if added else get_emoji("")

        user = self._contact_to_author(str(sender), contact)

        self._chat.process_reaction(
            {
                "adapter": self,
                "emoji": emoji_value,
                "rawEmoji": raw_emoji,
                "added": added,
                "user": user,
                "messageId": reaction.get("message_id"),
                "threadId": thread_id,
                "raw": inbound,
            },
            options,
        )

    def _handle_interactive_reply(
        self,
        inbound: WhatsAppInboundMessage,
        contact: WhatsAppContact | None,
        phone_number_id: str,
        options: dict[str, Any] | None,
    ) -> None:
        interactive = inbound.get("interactive")
        if self._chat is None or not interactive:
            return

        sender = inbound.get("from_") or inbound.get("from") or ""  # type: ignore[call-overload]
        thread_id = encode_thread_id(
            {"phoneNumberId": phone_number_id, "userWaId": str(sender)},
        )

        itype = interactive.get("type")
        raw_id: str | None = None
        fallback_value: str | None = None

        if itype == "button_reply" and interactive.get("button_reply"):
            br = interactive["button_reply"]
            raw_id = str(br.get("id", ""))
            fallback_value = str(br.get("title", ""))
        elif itype == "list_reply" and interactive.get("list_reply"):
            lr = interactive["list_reply"]
            raw_id = str(lr.get("id", ""))
            fallback_value = str(lr.get("title", ""))
        else:
            return

        decoded = decode_whatsapp_callback_data(raw_id)

        self._chat.process_action(
            {
                "adapter": self,
                "actionId": decoded["actionId"],
                "value": decoded["value"] if decoded["value"] is not None else fallback_value,
                "user": self._contact_to_author(str(sender), contact),
                "messageId": inbound.get("id"),
                "threadId": thread_id,
                "raw": inbound,
            },
            options,
        )

    def _handle_button_response(
        self,
        inbound: WhatsAppInboundMessage,
        contact: WhatsAppContact | None,
        phone_number_id: str,
        options: dict[str, Any] | None,
    ) -> None:
        button = inbound.get("button")
        if self._chat is None or not button:
            return

        sender = inbound.get("from_") or inbound.get("from") or ""  # type: ignore[call-overload]
        thread_id = encode_thread_id(
            {"phoneNumberId": phone_number_id, "userWaId": str(sender)},
        )

        self._chat.process_action(
            {
                "adapter": self,
                "actionId": button.get("payload"),
                "value": button.get("text"),
                "user": self._contact_to_author(str(sender), contact),
                "messageId": inbound.get("id"),
                "threadId": thread_id,
                "raw": inbound,
            },
            options,
        )

    def _extract_text_content(self, message: WhatsAppInboundMessage) -> str | None:
        mtype = message.get("type")
        if mtype == "text":
            text = message.get("text") or {}
            return str(text.get("body")) if text.get("body") is not None else None
        if mtype == "image":
            image = message.get("image") or {}
            caption = image.get("caption")
            return str(caption) if caption is not None else "[Image]"
        if mtype == "document":
            doc = message.get("document") or {}
            caption = doc.get("caption")
            if caption is not None:
                return str(caption)
            filename = doc.get("filename") or "file"
            return f"[Document: {filename}]"
        if mtype == "audio":
            return "[Audio message]"
        if mtype == "voice":
            return "[Voice message]"
        if mtype == "video":
            return "[Video]"
        if mtype == "sticker":
            return "[Sticker]"
        if mtype == "location":
            loc = message.get("location") or {}
            if loc:
                lat = loc.get("latitude")
                lng = loc.get("longitude")
                name = loc.get("name")
                address = loc.get("address")
                head = f"[Location: {name}" if name else f"[Location: {lat}, {lng}"
                parts = [head]
                if address:
                    parts.append(str(address))
                return f"{' - '.join(parts)}]"
            return "[Location]"
        return None

    # ------------------------------------------------------------------
    # Build Message
    # ------------------------------------------------------------------

    def _contact_to_author(self, sender: str, contact: WhatsAppContact | None) -> Author:
        name = sender
        if contact:
            profile = contact.get("profile") or {}
            profile_name = profile.get("name")
            if profile_name:
                name = str(profile_name)
        return Author(
            user_id=sender,
            user_name=name,
            full_name=name,
            is_bot=False,
            is_me=sender == (self._bot_user_id or ""),
        )

    def _build_message(
        self,
        inbound: WhatsAppInboundMessage,
        contact: WhatsAppContact | None,
        thread_id: str,
        text: str,
        phone_number_id: str,
    ) -> Message[Any]:
        sender = str(inbound.get("from_") or inbound.get("from") or "")  # type: ignore[call-overload]
        author = self._contact_to_author(sender, contact)
        formatted = self._format_converter.to_ast(text)
        attachments = self._build_attachments(inbound)

        timestamp = inbound.get("timestamp") or "0"
        try:
            ts_seconds = int(timestamp)
        except (TypeError, ValueError):
            ts_seconds = 0
        date_sent = datetime.fromtimestamp(ts_seconds, tz=UTC)

        raw: WhatsAppRawMessage = {
            "message": inbound,
            "phoneNumberId": phone_number_id or self._phone_number_id,
        }
        if contact is not None:
            raw["contact"] = contact

        return Message(
            id=str(inbound.get("id", "")),
            thread_id=thread_id,
            text=text,
            formatted=formatted,
            raw=raw,
            author=author,
            metadata=MessageMetadata(date_sent=date_sent, edited=False),
            attachments=attachments,
        )

    def _build_attachments(self, inbound: WhatsAppInboundMessage) -> list[Attachment]:
        attachments: list[Attachment] = []

        image = inbound.get("image")
        if image:
            attachments.append(
                self._build_media_attachment(
                    str(image.get("id", "")),
                    "image",
                    str(image.get("mime_type") or ""),
                ),
            )

        document = inbound.get("document")
        if document:
            attachments.append(
                self._build_media_attachment(
                    str(document.get("id", "")),
                    "file",
                    str(document.get("mime_type") or ""),
                    name=document.get("filename"),
                ),
            )

        audio = inbound.get("audio")
        if audio:
            attachments.append(
                self._build_media_attachment(
                    str(audio.get("id", "")),
                    "audio",
                    str(audio.get("mime_type") or ""),
                ),
            )

        video = inbound.get("video")
        if video:
            attachments.append(
                self._build_media_attachment(
                    str(video.get("id", "")),
                    "video",
                    str(video.get("mime_type") or ""),
                ),
            )

        voice = inbound.get("voice")
        if voice:
            attachments.append(
                self._build_media_attachment(
                    str(voice.get("id", "")),
                    "audio",
                    str(voice.get("mime_type") or ""),
                    name="voice",
                ),
            )

        sticker = inbound.get("sticker")
        if sticker:
            attachments.append(
                self._build_media_attachment(
                    str(sticker.get("id", "")),
                    "image",
                    str(sticker.get("mime_type") or ""),
                    name="sticker",
                ),
            )

        location = inbound.get("location")
        if location:
            lat = location.get("latitude")
            lng = location.get("longitude")
            try:
                lat_f = float(lat) if lat is not None else float("nan")
                lng_f = float(lng) if lng is not None else float("nan")
            except (TypeError, ValueError):
                lat_f = lng_f = float("nan")
            if lat_f == lat_f and lng_f == lng_f:  # not NaN
                map_url = f"https://www.google.com/maps?q={lat_f},{lng_f}"
                attachments.append(
                    Attachment(
                        type="file",
                        name=str(location.get("name") or "Location"),
                        mime_type="application/geo+json",
                        url=map_url,
                    ),
                )

        return attachments

    def _build_media_attachment(
        self,
        media_id: str,
        type_: str,
        mime_type: str,
        name: str | None = None,
    ) -> Attachment:
        async def _fetch() -> bytes:
            return await self.download_media(media_id)

        return Attachment(
            type=type_,  # type: ignore[arg-type]
            name=name,
            mime_type=mime_type or None,
            fetch_metadata={"mediaId": media_id},
            fetch_data=_fetch,
        )

    def rehydrate_attachment(self, attachment: Attachment) -> Attachment:
        meta = attachment.fetch_metadata or {}
        media_id = meta.get("mediaId") if isinstance(meta, dict) else None
        if not media_id:
            return attachment

        async def _fetch() -> bytes:
            return await self.download_media(str(media_id))

        return Attachment(
            type=attachment.type,
            name=attachment.name,
            mime_type=attachment.mime_type,
            size=attachment.size,
            width=attachment.width,
            height=attachment.height,
            url=attachment.url,
            fetch_metadata=attachment.fetch_metadata,
            fetch_data=_fetch,
        )

    # ------------------------------------------------------------------
    # Media download
    # ------------------------------------------------------------------

    async def download_media(self, media_id: str) -> bytes:
        """Download media from WhatsApp (two-step lookup → fetch)."""

        meta_url = f"{self._graph_api_url}/{media_id}"
        try:
            meta_response = await self._http.get(
                meta_url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        except httpx.HTTPError as err:
            raise NetworkError(
                "whatsapp",
                f"Network error fetching WhatsApp media URL for {media_id}",
                err,
            ) from err

        if meta_response.status_code >= 400:
            try:
                error_data = meta_response.json()
            except ValueError:
                error_data = {}
            self._logger.error(
                "Failed to get WhatsApp media URL",
                {"status": meta_response.status_code, "mediaId": media_id},
            )
            throw_whatsapp_api_error("downloadMedia", meta_response.status_code, error_data)

        try:
            media_info: WhatsAppMediaResponse = meta_response.json()
        except ValueError as err:
            raise NetworkError(
                "whatsapp",
                f"Failed to parse WhatsApp media metadata for {media_id}",
            ) from err

        media_url = media_info.get("url")
        if not media_url:
            raise NetworkError(
                "whatsapp",
                f"WhatsApp media metadata for {media_id} has no URL",
            )

        try:
            data_response = await self._http.get(
                str(media_url),
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        except httpx.HTTPError as err:
            raise NetworkError(
                "whatsapp",
                f"Network error downloading WhatsApp media {media_id}",
                err,
            ) from err

        if data_response.status_code >= 400:
            self._logger.error(
                "Failed to download WhatsApp media",
                {"status": data_response.status_code, "mediaId": media_id},
            )
            raise NetworkError(
                "whatsapp",
                f"Failed to download WhatsApp media {media_id}: {data_response.status_code}",
            )
        return data_response.content

    # ------------------------------------------------------------------
    # REST: send / react / mark-as-read
    # ------------------------------------------------------------------

    async def post_message(
        self,
        thread_id: str,
        message: Any,
    ) -> dict[str, Any]:
        """Send a message to a WhatsApp user.

        Cards with reply buttons render as interactive button messages;
        everything else is sent as plain text (split into 4096-char chunks
        when needed).
        """

        decoded = decode_thread_id(thread_id)
        user_wa_id = decoded["userWaId"]

        card = extract_card(message)
        if card:
            result = card_to_whatsapp(card)
            if result["type"] == "interactive":
                interactive_json = json.dumps(result["interactive"])
                interactive: WhatsAppInteractiveMessage = json.loads(
                    convert_emoji_placeholders(interactive_json, "whatsapp"),
                )
                return await self._send_interactive_message(thread_id, user_wa_id, interactive)
            return await self._send_text_message(
                thread_id,
                user_wa_id,
                convert_emoji_placeholders(result["text"], "whatsapp"),
            )

        body = convert_emoji_placeholders(
            self._format_converter.render_postable(message),
            "whatsapp",
        )
        return await self._send_text_message(thread_id, user_wa_id, body)

    async def edit_message(
        self,
        _thread_id: str,
        _message_id: str,
        _message: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "WhatsApp does not support editing messages. Use post_message to send a new message instead.",
            "editMessage",
        )

    async def delete_message(self, _thread_id: str, _message_id: str) -> None:
        raise NotImplementedError(
            "WhatsApp does not support deleting messages.",
            "deleteMessage",
        )

    async def add_reaction(
        self,
        thread_id: str,
        message_id: str,
        emoji: EmojiValue | str,
    ) -> None:
        decoded = decode_thread_id(thread_id)
        emoji_str = self._resolve_emoji(emoji)
        await self._graph_api_request(
            f"/{self._phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": decoded["userWaId"],
                "type": "reaction",
                "reaction": {
                    "message_id": message_id,
                    "emoji": emoji_str,
                },
            },
            method_name="addReaction",
        )

    async def remove_reaction(
        self,
        thread_id: str,
        message_id: str,
        _emoji: EmojiValue | str,
    ) -> None:
        decoded = decode_thread_id(thread_id)
        await self._graph_api_request(
            f"/{self._phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": decoded["userWaId"],
                "type": "reaction",
                "reaction": {
                    "message_id": message_id,
                    "emoji": "",
                },
            },
            method_name="removeReaction",
        )

    async def start_typing(self, _thread_id: str, _status: str | None = None) -> None:
        # WhatsApp Cloud API does not support typing indicators.
        return None

    async def mark_as_read(self, message_id: str) -> None:
        await self._graph_api_request(
            f"/{self._phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            },
            method_name="markAsRead",
        )

    # ------------------------------------------------------------------
    # Fetch / thread helpers
    # ------------------------------------------------------------------

    async def fetch_messages(
        self,
        _thread_id: str,
        _options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """No-op — WhatsApp Cloud API has no message history endpoint."""

        self._logger.debug(
            "fetchMessages not supported on WhatsApp - message history is not available via Cloud API",
        )
        return {"messages": []}

    async def fetch_thread(self, thread_id: str) -> dict[str, Any]:
        decoded = decode_thread_id(thread_id)
        return {
            "id": thread_id,
            "channelId": f"whatsapp:{decoded['phoneNumberId']}",
            "channelName": f"WhatsApp: {decoded['userWaId']}",
            "isDM": True,
            "metadata": decoded,
        }

    def encode_thread_id(self, platform_data: WhatsAppThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> WhatsAppThreadId:
        return decode_thread_id(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        decode_thread_id(thread_id)
        return thread_id

    def is_dm(self, _thread_id: str) -> bool:
        return True

    def get_channel_visibility(self, _channel_id: str) -> str:
        """WhatsApp DMs are always private 1:1 conversations."""

        return "private"

    async def open_dm(self, user_id: str) -> str:
        return encode_thread_id(
            {"phoneNumberId": self._phone_number_id, "userWaId": user_id},
        )

    def parse_message(self, raw: WhatsAppRawMessage) -> Message[Any]:
        message = raw.get("message") or {}
        text = self._extract_text_content(message) or ""
        sender = str(message.get("from_") or message.get("from") or "")  # type: ignore[call-overload]
        thread_id = encode_thread_id(
            {
                "phoneNumberId": str(raw.get("phoneNumberId") or self._phone_number_id),
                "userWaId": sender,
            },
        )
        return self._build_message(
            message,
            raw.get("contact"),
            thread_id,
            text,
            str(raw.get("phoneNumberId") or self._phone_number_id),
        )

    def render_formatted(self, content: Any) -> str:
        return self._format_converter.from_ast(content)

    def split_message(self, text: str) -> list[str]:
        return split_message(text)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(
        self,
        thread_id: str,
        text_stream: Any,
        _options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Buffer the entire stream and post as one markdown message.

        WhatsApp can't edit messages, so incremental updates aren't possible.
        """

        accumulated = ""
        async for chunk in text_stream:
            if isinstance(chunk, str):
                accumulated += chunk
            elif isinstance(chunk, dict) and chunk.get("type") == "markdown_text":
                accumulated += str(chunk.get("text") or "")
        return await self.post_message(thread_id, {"markdown": accumulated})

    # ------------------------------------------------------------------
    # Private: send helpers
    # ------------------------------------------------------------------

    async def _send_text_message(
        self,
        thread_id: str,
        to: str,
        text: str,
    ) -> dict[str, Any]:
        chunks = split_message(text)
        result: dict[str, Any] | None = None
        for chunk in chunks:
            result = await self._send_single_text_message(thread_id, to, chunk)
        if result is None:
            raise NetworkError(
                "whatsapp",
                "WhatsApp post_message produced no chunks",
            )
        return result

    async def _send_single_text_message(
        self,
        thread_id: str,
        to: str,
        text: str,
    ) -> dict[str, Any]:
        response: WhatsAppSendResponse = await self._graph_api_request(
            f"/{self._phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
            method_name="postMessage",
        )

        messages = response.get("messages") or []
        if not messages or not messages[0].get("id"):
            raise NetworkError(
                "whatsapp",
                "WhatsApp API did not return a message ID for text message",
            )
        message_id = str(messages[0]["id"])

        return {
            "id": message_id,
            "threadId": thread_id,
            "raw": {
                "message": {
                    "id": message_id,
                    "from": self._phone_number_id,
                    "timestamp": str(int(datetime.now(tz=UTC).timestamp())),
                    "type": "text",
                    "text": {"body": text},
                },
                "phoneNumberId": self._phone_number_id,
            },
        }

    async def _send_interactive_message(
        self,
        thread_id: str,
        to: str,
        interactive: WhatsAppInteractiveMessage,
    ) -> dict[str, Any]:
        response: WhatsAppSendResponse = await self._graph_api_request(
            f"/{self._phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": interactive,
            },
            method_name="postMessage",
        )

        messages = response.get("messages") or []
        if not messages or not messages[0].get("id"):
            raise NetworkError(
                "whatsapp",
                "WhatsApp API did not return a message ID for interactive message",
            )
        message_id = str(messages[0]["id"])

        return {
            "id": message_id,
            "threadId": thread_id,
            "raw": {
                "message": {
                    "id": message_id,
                    "from": self._phone_number_id,
                    "timestamp": str(int(datetime.now(tz=UTC).timestamp())),
                    "type": "interactive",
                },
                "phoneNumberId": self._phone_number_id,
            },
        }

    # ------------------------------------------------------------------
    # Private: HTTP transport
    # ------------------------------------------------------------------

    async def _graph_api_request(
        self,
        path: str,
        body: Any,
        method_name: str = "graphApiRequest",
    ) -> Any:
        url = f"{self._graph_api_url}{path}"
        try:
            response = await self._http.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError as err:
            raise NetworkError(
                "whatsapp",
                f"Network error calling WhatsApp {method_name}",
                err,
            ) from err

        try:
            data = response.json()
        except ValueError as err:
            if response.is_success:
                raise NetworkError(
                    "whatsapp",
                    f"Failed to parse WhatsApp API response for {method_name}",
                ) from err
            data = {}

        if not response.is_success:
            self._logger.error(
                "WhatsApp API error",
                {"status": response.status_code, "path": path},
            )
            throw_whatsapp_api_error(method_name, response.status_code, data)

        return data

    def _resolve_emoji(self, emoji: EmojiValue | str) -> str:
        if isinstance(emoji, str):
            return default_emoji_resolver.to_gchat(emoji)
        name = getattr(emoji, "name", None)
        return default_emoji_resolver.to_gchat(name or "")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_whatsapp_adapter(
    config: WhatsAppAdapterConfig | None = None,
) -> WhatsAppAdapter:
    """Factory for :class:`WhatsAppAdapter`.

    Reads missing values from the standard environment variables:
    ``WHATSAPP_ACCESS_TOKEN``, ``WHATSAPP_APP_SECRET``,
    ``WHATSAPP_PHONE_NUMBER_ID``, ``WHATSAPP_VERIFY_TOKEN``,
    ``WHATSAPP_BOT_USERNAME``, and ``WHATSAPP_API_URL``.
    """

    cfg: dict[str, Any] = dict(config or {})

    access_token = cfg.get("accessToken") or os.environ.get("WHATSAPP_ACCESS_TOKEN")
    if not access_token:
        raise ValidationError(
            "whatsapp",
            "accessToken is required. Set WHATSAPP_ACCESS_TOKEN or provide it in config.",
        )

    app_secret = cfg.get("appSecret") or os.environ.get("WHATSAPP_APP_SECRET")
    if not app_secret:
        raise ValidationError(
            "whatsapp",
            "appSecret is required. Set WHATSAPP_APP_SECRET or provide it in config.",
        )

    phone_number_id = cfg.get("phoneNumberId") or os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    if not phone_number_id:
        raise ValidationError(
            "whatsapp",
            "phoneNumberId is required. Set WHATSAPP_PHONE_NUMBER_ID or provide it in config.",
        )

    verify_token = cfg.get("verifyToken") or os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if not verify_token:
        raise ValidationError(
            "whatsapp",
            "verifyToken is required. Set WHATSAPP_VERIFY_TOKEN or provide it in config.",
        )

    user_name = cfg.get("userName") or os.environ.get("WHATSAPP_BOT_USERNAME") or "whatsapp-bot"

    resolved: dict[str, Any] = {
        "accessToken": access_token,
        "appSecret": app_secret,
        "phoneNumberId": phone_number_id,
        "verifyToken": verify_token,
        "userName": user_name,
        "logger": cfg.get("logger"),
    }
    api_version = cfg.get("apiVersion")
    if api_version:
        resolved["apiVersion"] = api_version
    api_url = cfg.get("apiUrl") or os.environ.get("WHATSAPP_API_URL")
    if api_url:
        resolved["apiUrl"] = api_url

    return WhatsAppAdapter(resolved)  # type: ignore[arg-type]


__all__ = [
    "DEFAULT_API_BASE_URL",
    "DEFAULT_API_VERSION",
    "WHATSAPP_MESSAGE_LIMIT",
    "WhatsAppAdapter",
    "create_whatsapp_adapter",
    "split_message",
]
