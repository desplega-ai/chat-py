"""Microsoft Teams adapter facade, config, and JWT verification.

Python port of upstream ``packages/adapter-teams/src/index.ts``.

Scope of this port:

- Public config types (:class:`TeamsAdapterConfig`, :class:`TeamsAuthFederated`)
- Env-var config resolution (``TEAMS_APP_ID``, ``TEAMS_APP_PASSWORD``,
  ``TEAMS_APP_TENANT_ID``, ``TEAMS_API_URL``)
- Bot Framework JWT verification (:func:`verify_bearer_token`) via
  :mod:`jwt.PyJWKClient` against ``https://login.botframework.com/v1/.well-known/keys``
- Thread ID / markdown / card passthroughs
- :class:`TeamsAdapter` class with a ``handle_webhook`` entrypoint,
  ``post_message`` / ``edit_message`` / ``delete_message`` / ``start_typing``
  / ``stream`` over Bot Framework REST using :mod:`httpx`, and
  :class:`NotImplementedError` stubs for reactions + Graph reader methods.
- :func:`create_teams_adapter` factory

Certificate auth raises at construction time (matches upstream's
``deprecated`` note — the Teams SDK does not yet support it).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypedDict, cast

import httpx
import jwt
import msal  # type: ignore[import-untyped]
from chat_adapter_shared import (
    AuthenticationError,
    NetworkError,
    ValidationError,
    buffer_to_data_uri,
    extract_card,
    extract_files,
    to_buffer,
)

from .cards import card_to_adaptive_card
from .errors import handle_teams_error
from .markdown import TeamsFormatConverter
from .thread_id import (
    TeamsThreadId,
    decode_thread_id,
    encode_thread_id,
    is_dm,
)

if TYPE_CHECKING:
    from chat import Logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Bot Framework JWKS endpoint for token signature verification.
BOT_FRAMEWORK_JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"

#: Canonical Bot Framework issuer (shared by all multi-tenant bots).
BOT_FRAMEWORK_ISSUER = "https://api.botframework.com"

#: Default Bot Framework REST endpoint for outbound activities.
DEFAULT_TEAMS_API_URL = "https://smba.trafficmanager.net/amer/"

#: Federated credential audience — matches upstream ``clientAudience`` default.
DEFAULT_FEDERATED_AUDIENCE = "api://AzureADTokenExchange"

_MESSAGEID_STRIP_PATTERN = ";messageid="


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TeamsAuthCertificate:
    """PEM + thumbprint certificate auth.

    Deprecated — not yet supported by the Teams SDK or this port. Supplying
    this to :class:`TeamsAdapter` raises :class:`ValidationError`.
    """

    certificate_private_key: str
    certificate_thumbprint: str | None = None
    x5c: str | None = None


@dataclass(slots=True)
class TeamsAuthFederated:
    """Workload-identity / managed-identity federated auth."""

    client_id: str
    client_audience: str = DEFAULT_FEDERATED_AUDIENCE


class TeamsAdapterConfig(TypedDict, total=False):
    """Config for :class:`TeamsAdapter` / :func:`create_teams_adapter`."""

    apiUrl: str
    appId: str
    appPassword: str
    appTenantId: str
    appType: Literal["MultiTenant", "SingleTenant"]
    certificate: TeamsAuthCertificate
    dialogOpenTimeoutMs: int
    federated: TeamsAuthFederated
    logger: Logger
    userName: str


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------


def _bot_framework_jwks_client() -> jwt.PyJWKClient:
    """Return a cached JWKS client for the Bot Framework signing keys."""

    return jwt.PyJWKClient(BOT_FRAMEWORK_JWKS_URL, cache_keys=True, lifespan=86400)


def verify_bearer_token(
    authorization_header: str | None,
    expected_audience: str,
    *,
    allowed_issuers: tuple[str, ...] = (BOT_FRAMEWORK_ISSUER,),
    jwks_client: jwt.PyJWKClient | None = None,
    leeway_seconds: int = 300,
) -> bool:
    """Verify a Bot Framework-signed Bearer JWT.

    Returns ``True`` when the header is well-formed and the token validates
    against the JWKS, audience, and issuer allowlist; ``False`` otherwise.
    Never raises — mirrors upstream behavior.

    ``jwks_client`` is injectable so tests can substitute a fake.
    """

    if not authorization_header or not authorization_header.startswith("Bearer "):
        return False

    token = authorization_header[len("Bearer ") :]
    if not token:
        return False

    client = jwks_client or _bot_framework_jwks_client()
    try:
        signing_key = client.get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=expected_audience,
            leeway=leeway_seconds,
            options={"require": ["exp", "aud", "iss"]},
        )
    except Exception:
        return False

    issuer = payload.get("iss")
    if not isinstance(issuer, str):
        return False
    return issuer in allowed_issuers


# ---------------------------------------------------------------------------
# TeamsAdapter
# ---------------------------------------------------------------------------


class TeamsAdapter:
    """Microsoft Teams platform adapter.

    Wraps Bot Framework REST calls for outbound activities and uses
    :class:`TeamsFormatConverter` / :func:`card_to_adaptive_card` to translate
    the platform-agnostic message shape. The live-dispatch webhook handling
    that upstream bridges via ``@microsoft/teams.apps`` is not ported — this
    facade only exposes ``handle_webhook`` as a thin JWT-verifying shim so
    higher-level event routing can be layered on top when the async ``Adapter``
    protocol lands in chat-core part B.
    """

    name = "teams"
    lock_scope: Literal["thread", "channel"] | None = "thread"
    persist_message_history: bool = False

    def __init__(self, config: TeamsAdapterConfig | None = None) -> None:
        cfg: TeamsAdapterConfig = dict(config or {})  # type: ignore[assignment]

        if cfg.get("certificate") is not None:
            raise ValidationError(
                "teams",
                "Certificate auth is not supported by the Teams SDK; use "
                "appPassword or federated credentials instead.",
            )

        self.user_name: str = cfg.get("userName") or "bot"

        self.app_id: str | None = cfg.get("appId") or os.environ.get("TEAMS_APP_ID")
        self.app_password: str | None = cfg.get("appPassword") or os.environ.get(
            "TEAMS_APP_PASSWORD"
        )
        self.app_tenant_id: str | None = cfg.get("appTenantId") or os.environ.get(
            "TEAMS_APP_TENANT_ID"
        )
        self.app_type: Literal["MultiTenant", "SingleTenant"] = cfg.get("appType") or "MultiTenant"
        self.api_url: str = (
            cfg.get("apiUrl") or os.environ.get("TEAMS_API_URL") or DEFAULT_TEAMS_API_URL
        )
        self.federated: TeamsAuthFederated | None = cfg.get("federated")
        self.dialog_open_timeout_ms: int = cfg.get("dialogOpenTimeoutMs") or 5000

        logger = cfg.get("logger")
        if logger is None:
            from chat import ConsoleLogger

            logger = ConsoleLogger("info").child("teams")
        self.logger: Logger = logger

        self.format_converter = TeamsFormatConverter()

        self._http_client: httpx.AsyncClient | None = None
        self._msal_app: msal.ConfidentialClientApplication | msal.ManagedIdentityClient | None = (
            None
        )
        self._token_lock = asyncio.Lock()

    # -------------------------------------------------------- thread id API

    def encode_thread_id(self, platform_data: TeamsThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> TeamsThreadId:
        return decode_thread_id(thread_id)

    def is_dm(self, thread_id: str) -> bool:
        return is_dm(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        """Strip any ``;messageid=N`` suffix from the encoded thread ID."""

        decoded = decode_thread_id(thread_id)
        base = decoded["conversationId"].split(_MESSAGEID_STRIP_PATTERN, 1)[0]
        return encode_thread_id({"conversationId": base, "serviceUrl": decoded["serviceUrl"]})

    # ------------------------------------------------------------- parsing

    def parse_message(self, raw: Any) -> Any:
        """Translate a Bot Framework ``Activity`` dict into :class:`chat.Message`."""

        from datetime import UTC, datetime

        from chat import Author, Message, MessageMetadata

        activity = raw if isinstance(raw, dict) else {}
        conversation = activity.get("conversation") or {}
        from_user = activity.get("from") or {}
        service_url = str(activity.get("serviceUrl") or "")
        conversation_id = str(conversation.get("id") or "")
        thread_id_str = encode_thread_id(
            {"conversationId": conversation_id, "serviceUrl": service_url}
        )

        text = str(activity.get("text") or "").strip()
        timestamp = activity.get("timestamp")
        if isinstance(timestamp, str):
            try:
                date_sent = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                date_sent = datetime.now(UTC)
        else:
            date_sent = datetime.now(UTC)

        is_me = self._is_message_from_self(from_user)

        return Message(
            id=str(activity.get("id") or ""),
            thread_id=thread_id_str,
            text=self.format_converter.extract_plain_text(text),
            formatted=self.format_converter.to_ast(text),
            raw=activity,
            author=Author(
                user_id=str(from_user.get("id") or "unknown"),
                user_name=str(from_user.get("name") or "unknown"),
                full_name=str(from_user.get("name") or "unknown"),
                is_bot=False,
                is_me=is_me,
            ),
            metadata=MessageMetadata(date_sent=date_sent, edited=False),
        )

    def _is_message_from_self(self, from_user: dict[str, Any]) -> bool:
        from_id = from_user.get("id")
        if not (isinstance(from_id, str) and self.app_id):
            return False
        if from_id == self.app_id:
            return True
        return from_id.endswith(f":{self.app_id}")

    # -------------------------------------------------------- webhook entry

    async def handle_webhook(
        self,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Verify the incoming JWT and acknowledge.

        Returns a ``(status, headers, body)`` tuple matching the shape used by
        :meth:`chat.Chat.handle_webhook`. Event routing (onMessage, onAction,
        onReaction) is not wired up in this port — higher layers can inspect
        ``body`` directly via :meth:`parse_message` / :meth:`decode_thread_id`.
        """

        auth_header = None
        if headers:
            for key, value in headers.items():
                if key.lower() == "authorization":
                    auth_header = value
                    break

        if self.app_id and auth_header is not None:
            ok = verify_bearer_token(auth_header, self.app_id)
            if not ok:
                return 401, {}, "unauthorized"

        # Best-effort dispatch — parse ``message`` activities and fire the
        # ``on_new_message`` / ``on_new_mention`` pipeline via
        # :meth:`Chat.dispatch`. Other activity types (``invoke``,
        # ``conversationUpdate``, ``messageReaction``) are acknowledged but
        # not routed here; higher layers can inspect ``body`` directly.
        activity = body if isinstance(body, dict) else {}
        if activity.get("type") == "message":
            chat = getattr(self, "_chat", None)
            if chat is not None:
                try:
                    message = self.parse_message(activity)
                    chat.process_message(self, message.thread_id, message)
                except Exception as err:
                    self.logger.warn(
                        "teams: dispatch failed",
                        {"activity_id": activity.get("id"), "error": repr(err)},
                    )

        return 200, {"content-type": "application/json"}, "{}"

    # ------------------------------------------------------------ posting

    async def post_message(self, thread_id: str, message: Any) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        conversation_id = decoded["conversationId"]

        activity = await self._message_to_activity(message)
        return await self._send_activity(conversation_id, activity, thread_id, "postMessage")

    async def edit_message(self, thread_id: str, message_id: str, message: Any) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        conversation_id = decoded["conversationId"]
        service_url = decoded["serviceUrl"]

        activity = await self._message_to_activity(message)
        url = (
            f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{message_id}"
        )
        await self._bot_rest_call("PUT", url, activity, "editMessage")
        return {"id": message_id, "threadId": thread_id, "raw": activity}

    async def delete_message(self, thread_id: str, message_id: str) -> None:
        decoded = self.decode_thread_id(thread_id)
        url = (
            f"{decoded['serviceUrl'].rstrip('/')}"
            f"/v3/conversations/{decoded['conversationId']}/activities/{message_id}"
        )
        await self._bot_rest_call("DELETE", url, None, "deleteMessage")

    async def start_typing(self, thread_id: str, status: str | None = None) -> None:
        decoded = self.decode_thread_id(thread_id)
        activity = {"type": "typing"}
        await self._send_activity(decoded["conversationId"], activity, thread_id, "startTyping")

    async def stream(
        self,
        thread_id: str,
        text_stream: Any,
        options: Any | None = None,
    ) -> dict[str, Any]:
        """Stream responses via post+edit. Accepts an async iterable of chunks."""

        decoded = self.decode_thread_id(thread_id)
        conversation_id = decoded["conversationId"]
        service_url = decoded["serviceUrl"]

        accumulated = ""
        message_id: str | None = None

        async for chunk in text_stream:
            text = ""
            if isinstance(chunk, str):
                text = chunk
            elif isinstance(chunk, dict) and chunk.get("type") == "markdown_text":
                text = str(chunk.get("text") or "")
            if not text:
                continue

            accumulated += text
            activity = {"type": "message", "text": accumulated, "textFormat": "markdown"}

            if message_id:
                url = (
                    f"{service_url.rstrip('/')}"
                    f"/v3/conversations/{conversation_id}/activities/{message_id}"
                )
                await self._bot_rest_call("PUT", url, activity, "stream")
            else:
                sent = await self._send_activity(conversation_id, activity, thread_id, "stream")
                message_id = str(sent.get("id") or "")

        return {
            "id": message_id or "",
            "threadId": thread_id,
            "raw": {"text": accumulated},
        }

    async def open_dm(self, user_id: str) -> str:
        """Create a 1:1 conversation and return its encoded thread ID.

        The upstream implementation pulls ``serviceUrl`` / ``tenantId`` from
        the chat state cache (populated from inbound activities). This port
        requires both to be supplied via adapter config (``appTenantId`` +
        ``apiUrl``) since chat-core state wiring is not yet available.
        """

        if not self.app_tenant_id:
            raise ValidationError(
                "teams",
                "Cannot open DM: appTenantId is required until the chat state cache is wired up.",
            )

        body = {
            "isGroup": False,
            "bot": {"id": self.app_id or "", "name": self.user_name},
            "members": [{"id": user_id, "name": "", "role": "user"}],
            "tenantId": self.app_tenant_id,
            "channelData": {"tenant": {"id": self.app_tenant_id}},
        }
        url = f"{self.api_url.rstrip('/')}/v3/conversations"
        result = await self._bot_rest_call("POST", url, body, "openDM")
        conversation_id = (result or {}).get("id")
        if not conversation_id:
            raise NetworkError(
                "teams",
                "Failed to create 1:1 conversation - no ID returned",
            )
        return self.encode_thread_id(
            {"conversationId": str(conversation_id), "serviceUrl": self.api_url}
        )

    # -------------------------------------------------- reactions / graph

    async def add_reaction(self, thread_id: str, message_id: str, emoji: Any) -> NoReturn:
        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "addReaction is not yet supported by the Teams SDK",
            "addReaction",
        )

    async def remove_reaction(self, thread_id: str, message_id: str, emoji: Any) -> NoReturn:
        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "removeReaction is not yet supported by the Teams SDK",
            "removeReaction",
        )

    async def fetch_messages(self, thread_id: str, options: Any = None) -> NoReturn:
        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "fetchMessages requires the Graph API reader (not yet ported)",
            "fetchMessages",
        )

    async def fetch_thread(self, thread_id: str) -> NoReturn:
        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "fetchThread requires the Graph API reader (not yet ported)",
            "fetchThread",
        )

    async def fetch_channel_messages(self, channel_id: str, options: Any = None) -> NoReturn:
        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "fetchChannelMessages requires the Graph API reader (not yet ported)",
            "fetchChannelMessages",
        )

    async def list_threads(self, channel_id: str, options: Any = None) -> NoReturn:
        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "listThreads requires the Graph API reader (not yet ported)",
            "listThreads",
        )

    async def fetch_channel_info(self, channel_id: str) -> NoReturn:
        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "fetchChannelInfo requires the Graph API reader (not yet ported)",
            "fetchChannelInfo",
        )

    # --------------------------------------------------------- formatting

    def render_formatted(self, content: Any) -> str:
        return self.format_converter.from_ast(content)

    # --------------------------------------------------------------- close

    async def close(self) -> None:
        """Tear down the internal :class:`httpx.AsyncClient`, if any."""

        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # -------------------------------------------------------- Adapter Protocol

    async def initialize(self, chat: Any) -> None:
        """Store the :class:`Chat` reference for later dispatch.

        Called by :meth:`Chat._do_initialize` once per adapter.
        """

        self._chat = chat

    async def disconnect(self) -> None:
        """Tear down any background resources (delegates to :meth:`close`)."""

        await self.close()

    async def subscribe(self, thread_id: str) -> None:
        """Subscribe the bot to a thread — no-op on Teams (subscription is
        implicit in Bot Framework once the bot is added to a conversation).
        """

        return None

    async def unsubscribe(self, thread_id: str) -> None:
        """Unsubscribe — no-op on Teams (same reasoning as :meth:`subscribe`)."""

        return None

    async def post_channel_message(self, channel_id: str, message: Any) -> dict[str, Any]:
        """Post to a channel root (no existing thread). Teams treats this as a
        new conversation in the channel, which requires the Graph API reader
        path — not yet ported.
        """

        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "postChannelMessage is not yet supported by the Teams SDK",
            "postChannelMessage",
        )

    async def open_modal(self, trigger_id: str, view: Any) -> Any:
        """Open a Teams task module. Not yet ported — upstream uses the
        ``taskModule/continue`` invoke response flow, which is not wired up
        through this webhook facade.
        """

        from chat.errors import NotImplementedError as ChatNotImplementedError

        raise ChatNotImplementedError(
            "openModal is not yet supported by the Teams SDK",
            "openModal",
        )

    def get_channel_visibility(
        self, thread_id: str
    ) -> Literal["external", "private", "workspace", "unknown"]:
        """Best-effort visibility lookup from thread ID shape.

        Teams doesn't expose visibility via the webhook activity; upstream
        resolves it via Graph API which is not yet ported. Returns
        ``"private"`` for DMs and ``"unknown"`` otherwise.
        """

        try:
            if self.is_dm(thread_id):
                return "private"
        except Exception:
            pass
        return "unknown"

    # ======================================================================
    # Internal helpers
    # ======================================================================

    async def _message_to_activity(self, message: Any) -> dict[str, Any]:
        """Translate a postable message into a Bot Framework ``Activity`` dict."""

        files = extract_files(message)
        file_attachments: list[dict[str, Any]] = []
        for file in files:
            buffer = await to_buffer(
                file.get("data") if isinstance(file, dict) else file.data,
                {"platform": "teams", "throw_on_unsupported": False},
            )
            if not buffer:
                continue
            mime_type = (
                file.get("mimeType") if isinstance(file, dict) else file.mime_type
            ) or "application/octet-stream"
            filename = (file.get("filename") if isinstance(file, dict) else file.filename) or "file"
            data_uri = buffer_to_data_uri(buffer, mime_type)
            file_attachments.append(
                {"contentType": mime_type, "contentUrl": data_uri, "name": filename}
            )

        card = extract_card(message)
        if card:
            adaptive_card = card_to_adaptive_card(cast("dict[str, Any]", card))
            attachments = [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card,
                },
                *file_attachments,
            ]
            return {"type": "message", "attachments": attachments}

        text = self.format_converter.render_postable(message)
        from chat import convert_emoji_placeholders

        text = convert_emoji_placeholders(text, "teams")
        activity: dict[str, Any] = {
            "type": "message",
            "text": text,
            "textFormat": "markdown",
        }
        if file_attachments:
            activity["attachments"] = file_attachments
        return activity

    async def _send_activity(
        self,
        conversation_id: str,
        activity: dict[str, Any],
        thread_id: str,
        operation: str,
    ) -> dict[str, Any]:
        url = f"{self.api_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
        result = await self._bot_rest_call("POST", url, activity, operation)
        return {
            "id": str((result or {}).get("id") or ""),
            "threadId": thread_id,
            "raw": activity,
        }

    async def _bot_rest_call(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        operation: str,
    ) -> dict[str, Any] | None:
        token = await self._get_bot_token()
        client = await self._get_http_client()
        try:
            response = await client.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body if body is not None else None,
            )
        except httpx.HTTPError as err:
            handle_teams_error({"message": str(err)}, operation)

        if response.status_code >= 400:
            payload: dict[str, Any] = {"statusCode": response.status_code}
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                inner = parsed.get("error") if isinstance(parsed.get("error"), dict) else None
                if inner and "message" in inner:
                    payload["message"] = inner["message"]
                elif "message" in parsed:
                    payload["message"] = parsed["message"]
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                payload["retryAfter"] = int(retry_after)
            handle_teams_error(payload, operation)

        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def _get_bot_token(self) -> str:
        """Fetch a Bot Framework OAuth token via msal."""

        async with self._token_lock:
            if self.federated is not None:
                return await asyncio.to_thread(self._acquire_federated_token)
            return await asyncio.to_thread(self._acquire_confidential_token)

    def _acquire_confidential_token(self) -> str:
        if not (self.app_id and self.app_password):
            raise AuthenticationError(
                "teams",
                "appId and appPassword are required for Bot Framework auth.",
            )

        authority = (
            f"https://login.microsoftonline.com/{self.app_tenant_id}"
            if self.app_tenant_id and self.app_type == "SingleTenant"
            else "https://login.microsoftonline.com/botframework.com"
        )
        if self._msal_app is None or not isinstance(
            self._msal_app, msal.ConfidentialClientApplication
        ):
            self._msal_app = msal.ConfidentialClientApplication(
                self.app_id,
                authority=authority,
                client_credential=self.app_password,
            )

        result = self._msal_app.acquire_token_for_client(
            scopes=["https://api.botframework.com/.default"],
        )
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            error = (
                result.get("error_description") if isinstance(result, dict) else None
            ) or "unknown msal error"
            raise AuthenticationError(
                "teams",
                f"Failed to acquire Bot Framework token: {error}",
            )
        return str(token)

    def _acquire_federated_token(self) -> str:
        if self.federated is None:
            raise AuthenticationError("teams", "Federated auth not configured.")
        if self._msal_app is None or not isinstance(self._msal_app, msal.ManagedIdentityClient):
            managed = msal.UserAssignedManagedIdentity(client_id=self.federated.client_id)
            self._msal_app = msal.ManagedIdentityClient(
                managed,
                http_client=None,
            )
        result = self._msal_app.acquire_token_for_client(
            resource="https://api.botframework.com",
        )
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            raise AuthenticationError(
                "teams",
                "Failed to acquire Bot Framework token via federated auth.",
            )
        return str(token)


def create_teams_adapter(config: TeamsAdapterConfig | None = None) -> TeamsAdapter:
    """Factory for :class:`TeamsAdapter`. Mirrors upstream ``createTeamsAdapter``."""

    return TeamsAdapter(config)


__all__ = [
    "BOT_FRAMEWORK_ISSUER",
    "BOT_FRAMEWORK_JWKS_URL",
    "DEFAULT_TEAMS_API_URL",
    "TeamsAdapter",
    "TeamsAdapterConfig",
    "TeamsAuthCertificate",
    "TeamsAuthFederated",
    "create_teams_adapter",
    "verify_bearer_token",
]
