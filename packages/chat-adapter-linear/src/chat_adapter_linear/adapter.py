"""Linear adapter for chat-py.

Python port of upstream ``packages/adapter-linear/src/index.ts``.

Supports four authentication modes:

* **API key** — personal access token from Linear Settings > Security & Access.
* **OAuth access token** — pre-obtained via the OAuth flow.
* **Client credentials** — single-tenant app-actor auth with adapter-managed
  token refresh.
* **Multi-tenant OAuth app** — ``clientId`` + ``clientSecret``; per-organization
  installations are resolved from chat state at request time.

All API calls go through :mod:`httpx` using the Linear GraphQL endpoint
(``https://api.linear.app/graphql``). Webhook signatures are verified with
HMAC-SHA256 against the raw body.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict, cast
from urllib.parse import urlencode

import httpx
from chat_adapter_shared import (
    AdapterError,
    AuthenticationError,
    NetworkError,
    ValidationError,
)

from .errors import handle_linear_error, handle_linear_graphql_body
from .markdown import LinearFormatConverter
from .thread_id import (
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
)
from .types import (
    LinearActorData,
    LinearAdapterConfig,
    LinearAdapterMode,
    LinearCommentData,
    LinearInstallation,
    LinearOAuthCallbackOptions,
    LinearRawMessage,
    LinearThreadId,
)
from .utils import (
    assert_agent_session_thread,
    calculate_expiry,
    get_user_name_from_profile_url,
    installation_from_dict,
    render_message_to_linear_markdown,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Iterator

    from chat import Logger, Message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_OAUTH_TOKEN_URL = "https://api.linear.app/oauth/token"

_INSTALLATION_KEY_PREFIX = "linear:installation"
_INSTALLATION_REFRESH_BUFFER_MS = 5 * 60 * 1000


class _LinearRequestContext(TypedDict):
    """Per-request auth/client bundle used in multi-tenant mode."""

    access_token: str
    installation: LinearInstallation


_request_context: contextvars.ContextVar[_LinearRequestContext | None] = contextvars.ContextVar(
    "linear_request_context", default=None
)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_linear_signature(secret: str, signature: str | None, body: bytes | str) -> bool:
    """Verify a Linear webhook ``Linear-Signature`` HMAC-SHA256 header.

    Returns ``False`` on any mismatch or error — never raises.
    """

    if not signature:
        return False
    body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        return False
    try:
        return hmac.compare_digest(signature, digest)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# GraphQL fragments
# ---------------------------------------------------------------------------

_VIEWER_QUERY = """
query LinearAdapterViewer {
  viewer {
    id
    displayName
    organization { id }
  }
}
"""

_ISSUE_QUERY = """
query LinearAdapterIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    url
  }
}
"""

_ISSUE_COMMENTS_QUERY = """
query LinearAdapterIssueComments($issueId: String!, $first: Int!) {
  comments(
    filter: { issue: { id: { eq: $issueId } }, parent: { null: true } }
    first: $first
  ) {
    nodes {
      id
      body
      createdAt
      updatedAt
      url
      parentId
      user { id displayName name email avatarUrl }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_COMMENT_REPLIES_QUERY = """
query LinearAdapterCommentReplies($parentId: ID!, $first: Int!) {
  comments(
    filter: { parent: { id: { eq: $parentId } } }
    first: $first
  ) {
    nodes {
      id
      body
      createdAt
      updatedAt
      url
      parentId
      user { id displayName name email avatarUrl }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_COMMENT_QUERY = """
query LinearAdapterComment($id: String!) {
  comment(id: $id) {
    id
    body
    createdAt
    updatedAt
    url
    parentId
    user { id displayName name email avatarUrl }
  }
}
"""

_CREATE_COMMENT_MUTATION = """
mutation LinearAdapterCreateComment($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment {
      id
      body
      createdAt
      updatedAt
      url
      parentId
    }
  }
}
"""

_UPDATE_COMMENT_MUTATION = """
mutation LinearAdapterUpdateComment($id: String!, $input: CommentUpdateInput!) {
  commentUpdate(id: $id, input: $input) {
    success
    comment {
      id
      body
      createdAt
      updatedAt
      url
      parentId
    }
  }
}
"""

_DELETE_COMMENT_MUTATION = """
mutation LinearAdapterDeleteComment($id: String!) {
  commentDelete(id: $id) { success }
}
"""

_CREATE_REACTION_MUTATION = """
mutation LinearAdapterCreateReaction($input: ReactionCreateInput!) {
  reactionCreate(input: $input) { success }
}
"""

_CREATE_AGENT_ACTIVITY_MUTATION = """
mutation LinearAdapterCreateAgentActivity($input: AgentActivityCreateInput!) {
  agentActivityCreate(input: $input) {
    success
    agentActivity {
      id
      sourceCommentId
      agentSessionId
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Emoji mapping
# ---------------------------------------------------------------------------

_EMOJI_MAP: dict[str, str] = {
    "thumbs_up": "\U0001f44d",
    "thumbs_down": "\U0001f44e",
    "heart": "\u2764\ufe0f",
    "fire": "\U0001f525",
    "rocket": "\U0001f680",
    "eyes": "\U0001f440",
    "check": "\u2705",
    "warning": "\u26a0\ufe0f",
    "sparkles": "\u2728",
    "wave": "\U0001f44b",
    "raised_hands": "\U0001f64c",
    "laugh": "\U0001f604",
    "hooray": "\U0001f389",
    "confused": "\U0001f615",
}


def _resolve_emoji(emoji: Any) -> str:
    name = (
        emoji
        if isinstance(emoji, str)
        else (emoji.get("name") if isinstance(emoji, dict) else getattr(emoji, "name", None))
    )
    if not isinstance(name, str):
        return ""
    return _EMOJI_MAP.get(name, name)


# ---------------------------------------------------------------------------
# LinearAdapter
# ---------------------------------------------------------------------------


class LinearAdapter:
    """Linear platform adapter.

    Implements the :class:`chat.Adapter` protocol over Linear's GraphQL API.
    Thread IDs follow ``linear:{issueId}[:c:{commentId}][:s:{agentSessionId}]``.
    """

    name = "linear"

    def __init__(self, config: LinearAdapterConfig | None = None) -> None:
        cfg: dict[str, Any] = dict(config or {})

        webhook_secret = cfg.get("webhookSecret") or os.environ.get("LINEAR_WEBHOOK_SECRET")
        if not webhook_secret:
            raise ValidationError(
                "linear",
                "webhookSecret is required. Set LINEAR_WEBHOOK_SECRET or provide it in config.",
            )
        self.webhook_secret: str = str(webhook_secret)

        logger = cfg.get("logger")
        if logger is None:
            from chat import ConsoleLogger

            logger = ConsoleLogger("info").child("linear")
        self.logger: Logger = logger

        self.mode: LinearAdapterMode = cast("LinearAdapterMode", cfg.get("mode") or "comments")
        self.user_name: str = (
            cfg.get("userName") or os.environ.get("LINEAR_BOT_USERNAME") or "linear-bot"
        )
        self.api_url: str = cfg.get("apiUrl") or os.environ.get("LINEAR_API_URL") or LINEAR_API_URL

        # Auth state
        self._default_access_token: str | None = None
        self._oauth_client_id: str | None = None
        self._oauth_client_secret: str | None = None
        self._client_credentials: dict[str, Any] | None = None
        self._access_token_expiry_ms: int | None = None

        self._default_bot_user_id: str | None = None
        self._default_organization_id: str | None = None

        api_key = cfg.get("apiKey")
        access_token = cfg.get("accessToken")
        client_credentials = cfg.get("clientCredentials")
        client_id = cfg.get("clientId")
        client_secret = cfg.get("clientSecret")

        if api_key:
            self._default_access_token = str(api_key)
        elif access_token:
            self._default_access_token = str(access_token)
        elif client_credentials:
            self._client_credentials = self._normalize_client_credentials(
                client_credentials, "config"
            )
        elif client_id or client_secret:
            if not (client_id and client_secret):
                raise ValidationError(
                    "linear",
                    "clientId and clientSecret are required together for multi-tenant OAuth.",
                )
            self._oauth_client_id = str(client_id)
            self._oauth_client_secret = str(client_secret)
        else:
            env_api_key = os.environ.get("LINEAR_API_KEY")
            env_access_token = os.environ.get("LINEAR_ACCESS_TOKEN")
            env_cc_id = os.environ.get("LINEAR_CLIENT_CREDENTIALS_CLIENT_ID")
            env_cc_secret = os.environ.get("LINEAR_CLIENT_CREDENTIALS_CLIENT_SECRET")
            env_client_id = os.environ.get("LINEAR_CLIENT_ID")
            env_client_secret = os.environ.get("LINEAR_CLIENT_SECRET")
            if env_api_key:
                self._default_access_token = env_api_key
            elif env_access_token:
                self._default_access_token = env_access_token
            elif env_cc_id and env_cc_secret:
                self._client_credentials = self._normalize_client_credentials(
                    {
                        "clientId": env_cc_id,
                        "clientSecret": env_cc_secret,
                        "scopes": _parse_env_scopes(
                            os.environ.get("LINEAR_CLIENT_CREDENTIALS_SCOPES")
                        ),
                    },
                    "env",
                )
            elif env_client_id and env_client_secret:
                self._oauth_client_id = env_client_id
                self._oauth_client_secret = env_client_secret
            else:
                raise ValidationError(
                    "linear",
                    "Authentication is required. Set LINEAR_API_KEY, LINEAR_ACCESS_TOKEN, "
                    "LINEAR_CLIENT_CREDENTIALS_CLIENT_ID/LINEAR_CLIENT_CREDENTIALS_CLIENT_SECRET, "
                    "or LINEAR_CLIENT_ID/LINEAR_CLIENT_SECRET, or provide auth in config.",
                )

        self.format_converter = LinearFormatConverter()
        self._http_client: httpx.AsyncClient | None = None
        self._chat: Any = None

    # ------------------------------------------------------------------
    # Config normalization
    # ------------------------------------------------------------------

    def _normalize_client_credentials(
        self,
        creds: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        client_id = creds.get("clientId")
        client_secret = creds.get("clientSecret")
        if not (client_id and client_secret):
            raise ValidationError(
                "linear",
                (
                    "clientCredentials.clientId and clientCredentials.clientSecret "
                    f"are required in {source}."
                ),
            )
        scopes = creds.get("scopes")
        if not scopes:
            scopes = ["read", "write", "comments:create", "issues:create"]
            if self.mode == "agent-sessions":
                scopes.append("app:mentionable")
        return {
            "clientId": str(client_id),
            "clientSecret": str(client_secret),
            "scopes": [str(s) for s in scopes],
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, chat: Any) -> None:
        self._chat = chat

        if self._client_credentials:
            await self._refresh_client_credentials_token()

        if self._default_access_token:
            try:
                identity = await self._fetch_viewer_identity(self._default_access_token)
                self._default_bot_user_id = identity["botUserId"]
                self._default_organization_id = identity["organizationId"]
                self.logger.info(
                    "Linear auth completed",
                    {
                        "botUserId": self._default_bot_user_id,
                        "displayName": identity.get("displayName"),
                        "organizationId": self._default_organization_id,
                    },
                )
            except Exception as exc:
                self.logger.warn("Could not fetch Linear bot user ID", {"error": str(exc)})
        elif self.is_multi_tenant:
            self.logger.info("Linear adapter initialized in multi-tenant mode")

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Thread ID helpers
    # ------------------------------------------------------------------

    def encode_thread_id(self, platform_data: LinearThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> LinearThreadId:
        return decode_thread_id(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        return channel_id_from_thread_id(thread_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def bot_user_id(self) -> str:
        ctx = _request_context.get()
        installation = ctx["installation"] if ctx else None
        value = (
            installation.get("botUserId") if installation is not None else self._default_bot_user_id
        )
        if not value:
            raise AdapterError(
                "No bot user ID available in context. "
                "Ensure the adapter has been initialized and authenticated properly.",
                "linear",
            )
        return value

    @property
    def organization_id(self) -> str:
        ctx = _request_context.get()
        value = (
            ctx["installation"].get("organizationId")
            if ctx is not None
            else self._default_organization_id
        )
        if not value:
            raise AuthenticationError(
                "linear",
                "No Linear organization ID available. "
                "Ensure the adapter has been initialized or use with_installation().",
            )
        return value

    @property
    def is_multi_tenant(self) -> bool:
        return bool(self._oauth_client_id and self._oauth_client_secret)

    # ------------------------------------------------------------------
    # Installation management
    # ------------------------------------------------------------------

    def _installation_key(self, organization_id: str) -> str:
        return f"{_INSTALLATION_KEY_PREFIX}:{organization_id}"

    def _state(self) -> Any:
        if self._chat is None:
            return None
        state = getattr(self._chat, "get_state", None) or getattr(self._chat, "getState", None)
        if state is None:
            return None
        return state() if callable(state) else state

    async def set_installation(
        self, organization_id: str, installation: LinearInstallation
    ) -> None:
        state = self._state()
        if state is None:
            raise ValidationError(
                "linear",
                "Adapter not initialized. Ensure chat.initialize() has been called first.",
            )
        await state.set(self._installation_key(organization_id), dict(installation))
        self.logger.info("Linear installation saved", {"organizationId": organization_id})

    async def get_installation(self, organization_id: str) -> LinearInstallation | None:
        state = self._state()
        if state is None:
            raise ValidationError(
                "linear",
                "Adapter not initialized. Ensure chat.initialize() has been called first.",
            )
        ctx = _request_context.get()
        if ctx is not None and ctx["installation"].get("organizationId") == organization_id:
            return ctx["installation"]

        value = await state.get(self._installation_key(organization_id))
        coerced = installation_from_dict(value)
        if coerced is None:
            return None
        return cast("LinearInstallation", coerced)

    async def delete_installation(self, organization_id: str) -> None:
        state = self._state()
        if state is None:
            raise ValidationError(
                "linear",
                "Adapter not initialized. Ensure chat.initialize() has been called first.",
            )
        await state.delete(self._installation_key(organization_id))
        self.logger.info("Linear installation deleted", {"organizationId": organization_id})

    # ------------------------------------------------------------------
    # OAuth flows
    # ------------------------------------------------------------------

    async def handle_oauth_callback(
        self,
        query: dict[str, str] | str,
        options: LinearOAuthCallbackOptions,
    ) -> dict[str, Any]:
        """Process a Linear OAuth callback.

        ``query`` may be a dict of already-parsed ``?code=...&state=...`` params
        or a raw query string.
        """

        if not (self._oauth_client_id and self._oauth_client_secret):
            raise ValidationError(
                "linear",
                "clientId and clientSecret are required for OAuth. "
                "Pass them in create_linear_adapter().",
            )

        redirect_uri = options.get("redirectUri")
        if not redirect_uri:
            raise ValidationError(
                "linear",
                "redirectUri is required for handle_oauth_callback().",
            )

        params = _parse_query(query)
        error = params.get("error")
        if error:
            description = params.get("error_description")
            detail = f"{error} - {description}" if description else error
            raise AuthenticationError("linear", f"Linear OAuth failed: {detail}")

        code = params.get("code")
        if not code:
            raise ValidationError(
                "linear",
                "Missing 'code' query parameter in OAuth callback request.",
            )

        token = await self._fetch_oauth_token(
            {
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._oauth_client_id,
                "client_secret": self._oauth_client_secret,
                "grant_type": "authorization_code",
            },
            "Failed to exchange Linear OAuth code",
        )

        identity = await self._fetch_viewer_identity(token["access_token"])
        installation: LinearInstallation = {
            "accessToken": token["access_token"],
            "botUserId": identity["botUserId"],
            "expiresAt": calculate_expiry(token.get("expires_in")),
            "organizationId": identity["organizationId"],
        }
        refresh_token = token.get("refresh_token")
        if refresh_token:
            installation["refreshToken"] = refresh_token

        await self.set_installation(identity["organizationId"], installation)
        return {
            "organizationId": identity["organizationId"],
            "installation": installation,
        }

    async def refresh_installation(self, installation: LinearInstallation) -> LinearInstallation:
        """Refresh an installation's access token if close to expiry."""

        refresh_token = installation.get("refreshToken")
        if not (refresh_token and self._oauth_client_id and self._oauth_client_secret):
            return installation

        expires_at = installation.get("expiresAt")
        now_ms = int(time.time() * 1000)
        if expires_at is not None and expires_at > now_ms + _INSTALLATION_REFRESH_BUFFER_MS:
            return installation

        token = await self._fetch_oauth_token(
            {
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "client_id": self._oauth_client_id,
                "client_secret": self._oauth_client_secret,
            },
            "Failed to refresh Linear OAuth token",
        )

        refreshed: LinearInstallation = dict(installation)  # type: ignore[assignment]
        refreshed["accessToken"] = token["access_token"]
        refreshed["expiresAt"] = calculate_expiry(token.get("expires_in"))
        if "refresh_token" in token:
            refreshed["refreshToken"] = token["refresh_token"]

        await self.set_installation(installation["organizationId"], refreshed)
        return refreshed

    @contextmanager
    def _with_context(self, installation: LinearInstallation) -> Iterator[None]:
        access_token = installation.get("accessToken")
        if not access_token:
            raise AuthenticationError(
                "linear",
                "Installation is missing an access token",
            )
        token = _request_context.set({"access_token": access_token, "installation": installation})
        try:
            yield
        finally:
            _request_context.reset(token)

    async def with_installation(
        self,
        installation_or_org_id: str | LinearInstallation,
        fn: Any,
    ) -> Any:
        """Run ``fn()`` with ``installation`` in the request context."""

        if not self.is_multi_tenant:
            result = fn()
            return await result if hasattr(result, "__await__") else result

        if isinstance(installation_or_org_id, str):
            installation = await self._require_installation(installation_or_org_id)
        else:
            installation = await self.refresh_installation(installation_or_org_id)

        with self._with_context(installation):
            result = fn()
            return await result if hasattr(result, "__await__") else result

    async def _require_installation(self, organization_id: str) -> LinearInstallation:
        installation = await self.get_installation(organization_id)
        if installation is None:
            raise AuthenticationError(
                "linear",
                f"No installation found for organization {organization_id}",
            )
        return await self.refresh_installation(installation)

    # ------------------------------------------------------------------
    # Webhook handling
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        body: bytes | str,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Verify and dispatch an incoming Linear webhook."""

        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        body_str = body_bytes.decode("utf-8", errors="replace")
        normalized = {k.lower(): v for k, v in (headers or {}).items()}

        signature = normalized.get("linear-signature")
        if not verify_linear_signature(self.webhook_secret, signature, body_bytes):
            return 401, {}, "Invalid signature"

        try:
            payload = json.loads(body_str)
        except ValueError:
            self.logger.error("Linear webhook invalid JSON")
            return 400, {}, "Invalid JSON"

        event_type = payload.get("type")
        organization_id = payload.get("organizationId")

        async def _dispatch() -> None:
            if event_type == "OAuthApp" and payload.get("action") == "revoked":
                try:
                    await self.delete_installation(organization_id or "")
                except Exception as exc:
                    self.logger.error(
                        "Failed to delete Linear installation on revoke",
                        {"organizationId": organization_id, "error": str(exc)},
                    )
                return

            if event_type == "Comment":
                if self.mode != "comments" or payload.get("action") != "create":
                    return
                await self._handle_comment_created(payload)
                return

            if event_type == "AgentSessionEvent":
                if self.mode != "agent-sessions":
                    self.logger.warn(
                        "Received AgentSessionEvent webhook but adapter is not in "
                        "agent-sessions mode, ignoring"
                    )
                    return
                await self._handle_agent_session_event(payload)
                return

            if event_type == "Reaction":
                data = payload.get("data") or {}
                self.logger.debug(
                    "Received reaction webhook",
                    {
                        "reactionId": data.get("id"),
                        "emoji": data.get("emoji"),
                        "commentId": data.get("commentId"),
                        "action": payload.get("action"),
                    },
                )

        if self.is_multi_tenant and organization_id:
            installation = await self.get_installation(organization_id)
            if installation is None:
                self.logger.warn(
                    "No Linear installation found for organization",
                    {"organizationId": organization_id},
                )
                return 200, {}, "ok"
            with self._with_context(installation):
                await _dispatch()
        else:
            await _dispatch()

        return 200, {}, "ok"

    async def _handle_comment_created(self, payload: dict[str, Any]) -> None:
        if self._chat is None:
            self.logger.warn("Chat instance not initialized, ignoring comment")
            return

        data = payload.get("data") or {}
        issue_id = data.get("issueId")
        user = data.get("user")
        if not (issue_id and isinstance(user, dict)):
            self.logger.debug(
                "Ignoring non-issue comment",
                {"commentId": data.get("id")},
            )
            return

        root_comment_id = str(data.get("parentId") or data.get("id") or "")
        thread_id = self.encode_thread_id({"issueId": issue_id, "commentId": root_comment_id})

        comment: LinearCommentData = {
            "body": str(data.get("body") or ""),
            "createdAt": str(data.get("createdAt") or ""),
            "id": str(data.get("id") or ""),
            "issueId": issue_id,
            "parentId": data.get("parentId"),
            "updatedAt": str(data.get("updatedAt") or ""),
            "url": payload.get("url"),
            "user": {
                "type": "user",
                "id": str(user.get("id") or ""),
                "displayName": get_user_name_from_profile_url(user.get("url")),
                "fullName": str(user.get("name") or ""),
                "email": user.get("email"),
                "avatarUrl": user.get("avatarUrl"),
            },
        }

        raw: LinearRawMessage = cast(
            "LinearRawMessage",
            {
                "kind": "comment",
                "comment": comment,
                "organizationId": payload.get("organizationId") or "",
            },
        )
        message = self.parse_message(raw)
        await self._dispatch_message(thread_id, message)

    async def _handle_agent_session_event(self, payload: dict[str, Any]) -> None:
        if self._chat is None:
            self.logger.warn("Chat instance not initialized, ignoring agent session event")
            return

        session = payload.get("agentSession") or {}
        issue_id = session.get("issueId") or (session.get("issue") or {}).get("id")
        if not issue_id:
            return

        action = payload.get("action")
        organization_id = payload.get("organizationId") or ""

        if action == "prompted":
            activity = payload.get("agentActivity") or {}
            source_comment_id = activity.get("sourceCommentId")
            if not source_comment_id:
                return
            user = activity.get("user") or {}
            content = activity.get("content") or {}
            comment: LinearCommentData = {
                "id": str(source_comment_id),
                "body": str(content.get("body") or ""),
                "issueId": issue_id,
                "user": {
                    "type": "user",
                    "id": str(user.get("id") or ""),
                    "displayName": get_user_name_from_profile_url(user.get("url")),
                    "fullName": str(user.get("name") or ""),
                    "email": user.get("email"),
                    "avatarUrl": user.get("avatarUrl"),
                },
                "parentId": (session.get("comment") or {}).get("id"),
                "createdAt": str(activity.get("createdAt") or ""),
                "updatedAt": str(activity.get("createdAt") or ""),
                "url": session.get("url"),
            }
            raw: LinearRawMessage = cast(
                "LinearRawMessage",
                {
                    "kind": "agent_session_comment",
                    "organizationId": organization_id,
                    "comment": comment,
                    "agentSessionId": str(session.get("id") or ""),
                    "agentSessionPromptContext": payload.get("promptContext") or "",
                },
            )
            message = self.parse_message(raw)
            await self._dispatch_message(message.thread_id, message)
            return

        if action == "created":
            root_comment = session.get("comment") or {}
            if session.get("appUserId") and session.get("appUserId") != self._default_bot_user_id:
                return
            if not root_comment:
                return
            creator = session.get("creator") or {}
            comment = {
                "id": str(root_comment.get("id") or ""),
                "body": str(root_comment.get("body") or ""),
                "issueId": issue_id,
                "user": (
                    {
                        "type": "user",
                        "id": str(creator.get("id") or ""),
                        "displayName": get_user_name_from_profile_url(creator.get("url")),
                        "fullName": str(creator.get("name") or ""),
                        "email": creator.get("email"),
                        "avatarUrl": creator.get("avatarUrl"),
                    }
                    if creator
                    else {
                        "type": "bot",
                        "id": self._default_bot_user_id or "",
                        "displayName": self.user_name,
                        "fullName": self.user_name,
                        "email": None,
                        "avatarUrl": None,
                    }
                ),
                "parentId": None,
                "createdAt": str(payload.get("createdAt") or ""),
                "updatedAt": str(payload.get("createdAt") or ""),
                "url": session.get("url"),
            }
            raw2: LinearRawMessage = cast(
                "LinearRawMessage",
                {
                    "kind": "agent_session_comment",
                    "organizationId": organization_id,
                    "comment": comment,
                    "agentSessionId": str(session.get("id") or ""),
                    "agentSessionPromptContext": payload.get("promptContext") or "",
                },
            )
            message = self.parse_message(raw2)
            await self._dispatch_message(message.thread_id, message)

    async def _dispatch_message(self, thread_id: str, message: Message[Any]) -> None:
        process = (
            getattr(self._chat, "process_message", None)
            or getattr(self._chat, "processMessage", None)
            or getattr(self._chat, "handle_incoming_message", None)
            or getattr(self._chat, "handleIncomingMessage", None)
        )
        if process is None:
            return
        result = process(self, thread_id, message)
        if hasattr(result, "__await__"):
            await result

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    def parse_message(self, raw: LinearRawMessage) -> Message[Any]:
        from chat import Author, Message, MessageMetadata

        raw_dict = cast("dict[str, Any]", raw)
        comment = cast("LinearCommentData", raw_dict.get("comment") or {})
        user: LinearActorData = comment.get("user") or {}
        body = str(comment.get("body") or "")
        issue_id = str(comment.get("issueId") or "")

        is_agent_session = raw_dict.get("kind") == "agent_session_comment"
        agent_session_id = raw_dict.get("agentSessionId") if is_agent_session else None

        thread_id_parts: LinearThreadId = {
            "issueId": issue_id,
            "commentId": str(comment.get("id") or ""),
        }
        if agent_session_id:
            thread_id_parts["agentSessionId"] = str(agent_session_id)

        thread_id = self.encode_thread_id(thread_id_parts)

        created_at = str(comment.get("createdAt") or "")
        updated_at = str(comment.get("updatedAt") or "")
        edited = bool(created_at and updated_at and created_at != updated_at)

        author = Author(
            user_id=str(user.get("id") or ""),
            user_name=str(user.get("displayName") or ""),
            full_name=str(user.get("fullName") or user.get("displayName") or ""),
            is_bot=user.get("type") == "bot",
            is_me=str(user.get("id") or "") == (self._default_bot_user_id or ""),
        )

        return Message(
            id=str(comment.get("id") or ""),
            is_mention=is_agent_session,
            thread_id=thread_id,
            text=body,
            formatted=self.format_converter.to_ast(body),
            author=author,
            metadata=MessageMetadata(
                date_sent=_parse_iso(created_at) or datetime.now(UTC),
                edited=edited,
                edited_at=_parse_iso(updated_at) if edited else None,
            ),
            attachments=[],
            raw=raw,
        )

    def render_formatted(self, content: Any) -> str:
        return self.format_converter.from_ast(content)

    # ------------------------------------------------------------------
    # REST: post / edit / delete
    # ------------------------------------------------------------------

    async def post_message(self, thread_id: str, message: Any) -> dict[str, Any]:
        await self._ensure_valid_token()
        decoded = self.decode_thread_id(thread_id)
        body = render_message_to_linear_markdown(message, self.format_converter)

        agent_session_id = decoded.get("agentSessionId")
        if agent_session_id:
            assert_agent_session_thread(decoded)
            activity = await self._create_agent_activity(
                agent_session_id=agent_session_id,
                content={"type": "response", "body": body},
            )
            return {
                "id": str(activity.get("id") or ""),
                "threadId": thread_id,
                "raw": {
                    "kind": "agent_session_comment",
                    "comment": {
                        "id": str(activity.get("sourceCommentId") or activity.get("id") or ""),
                        "body": body,
                        "issueId": decoded["issueId"],
                        "parentId": decoded.get("commentId"),
                        "user": self._bot_actor(),
                        "createdAt": _now_iso(),
                        "updatedAt": _now_iso(),
                        "url": None,
                    },
                    "agentSessionId": agent_session_id,
                    "organizationId": self._resolved_organization_id(),
                },
            }

        variables: dict[str, Any] = {
            "input": {
                "issueId": decoded["issueId"],
                "body": body,
            }
        }
        comment_id = decoded.get("commentId")
        if comment_id:
            variables["input"]["parentId"] = comment_id

        data = await self._graphql(_CREATE_COMMENT_MUTATION, variables, "commentCreate")
        payload = (data or {}).get("commentCreate") or {}
        comment = payload.get("comment")
        if not (payload.get("success") and comment):
            raise AdapterError("Failed to create comment on Linear issue", "linear")

        return {
            "id": str(comment.get("id") or ""),
            "threadId": thread_id,
            "raw": {
                "kind": "comment",
                "comment": {
                    "id": str(comment.get("id") or ""),
                    "body": str(comment.get("body") or body),
                    "issueId": decoded["issueId"],
                    "parentId": comment_id,
                    "user": self._bot_actor(),
                    "createdAt": str(comment.get("createdAt") or _now_iso()),
                    "updatedAt": str(comment.get("updatedAt") or _now_iso()),
                    "url": comment.get("url"),
                },
                "organizationId": self._resolved_organization_id(),
            },
        }

    async def edit_message(self, thread_id: str, message_id: str, message: Any) -> dict[str, Any]:
        await self._ensure_valid_token()
        decoded = self.decode_thread_id(thread_id)
        if decoded.get("agentSessionId"):
            raise AdapterError(
                "Linear agent session activities are append-only and cannot be edited",
                "linear",
            )

        body = render_message_to_linear_markdown(message, self.format_converter)
        data = await self._graphql(
            _UPDATE_COMMENT_MUTATION,
            {"id": message_id, "input": {"body": body}},
            "commentUpdate",
        )
        payload = (data or {}).get("commentUpdate") or {}
        comment = payload.get("comment")
        if not (payload.get("success") and comment):
            raise AdapterError("Failed to update comment on Linear", "linear")

        return {
            "id": str(comment.get("id") or ""),
            "threadId": thread_id,
            "raw": {
                "kind": "comment",
                "comment": {
                    "id": str(comment.get("id") or ""),
                    "body": str(comment.get("body") or body),
                    "issueId": decoded["issueId"],
                    "parentId": comment.get("parentId"),
                    "user": self._bot_actor(),
                    "createdAt": str(comment.get("createdAt") or _now_iso()),
                    "updatedAt": str(comment.get("updatedAt") or _now_iso()),
                    "url": comment.get("url"),
                },
                "organizationId": self._resolved_organization_id(),
            },
        }

    async def delete_message(self, thread_id: str, message_id: str) -> None:
        decoded = self.decode_thread_id(thread_id)
        if decoded.get("agentSessionId"):
            raise AdapterError(
                "Linear agent session activities are append-only and cannot be deleted",
                "linear",
            )

        await self._ensure_valid_token()
        await self._graphql(
            _DELETE_COMMENT_MUTATION,
            {"id": message_id},
            "commentDelete",
        )

    async def add_reaction(self, _thread_id: str, message_id: str, emoji: Any) -> None:
        await self._ensure_valid_token()
        emoji_str = _resolve_emoji(emoji)
        if not emoji_str:
            return
        await self._graphql(
            _CREATE_REACTION_MUTATION,
            {"input": {"commentId": message_id, "emoji": emoji_str}},
            "reactionCreate",
        )

    async def remove_reaction(self, _thread_id: str, _message_id: str, _emoji: Any) -> None:
        self.logger.warn(
            "removeReaction is not fully supported on Linear — reaction ID lookup would be required"
        )

    async def start_typing(self, thread_id: str, status: str | None = None) -> None:
        await self._ensure_valid_token()
        decoded = self.decode_thread_id(thread_id)

        agent_session_id = decoded.get("agentSessionId")
        if agent_session_id:
            await self._create_agent_activity(
                agent_session_id=agent_session_id,
                content={"type": "thought", "body": status or "Thinking..."},
                ephemeral=True,
            )
            return

        self.logger.warn(
            "start_typing is only supported in agent session threads. Ignoring for comment thread."
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(
        self,
        thread_id: str,
        text_stream: AsyncIterable[Any],
        _options: Any = None,
    ) -> dict[str, Any]:
        """Accumulate streaming chunks then post as a single comment.

        Upstream also supports a live ``edit_message`` loop for comment threads;
        to keep the port compact we defer to a single post at the end. Agent
        sessions receive one ``response`` activity containing the full text.
        """

        text = ""
        async for chunk in text_stream:
            if isinstance(chunk, str):
                text += chunk
            elif isinstance(chunk, dict) and chunk.get("type") == "markdown_text":
                text += str(chunk.get("text", ""))

        return await self.post_message(thread_id, {"markdown": text})

    # ------------------------------------------------------------------
    # Fetch helpers
    # ------------------------------------------------------------------

    async def fetch_messages(self, thread_id: str, options: Any = None) -> dict[str, Any]:
        await self._ensure_valid_token()
        decoded = self.decode_thread_id(thread_id)
        opts = options if isinstance(options, dict) else {}
        limit = int(opts.get("limit") or 50)

        comment_id = decoded.get("commentId")
        if comment_id:
            root = await self._graphql(_COMMENT_QUERY, {"id": comment_id}, "comment")
            children = await self._graphql(
                _COMMENT_REPLIES_QUERY,
                {"parentId": comment_id, "first": limit},
                "comments",
            )
            comments: list[dict[str, Any]] = []
            root_comment = (root or {}).get("comment")
            if root_comment:
                comments.append(root_comment)
            for node in ((children or {}).get("comments") or {}).get("nodes") or []:
                comments.append(node)
            page_info = ((children or {}).get("comments") or {}).get("pageInfo") or {}
        else:
            data = await self._graphql(
                _ISSUE_COMMENTS_QUERY,
                {"issueId": decoded["issueId"], "first": limit},
                "comments",
            )
            comments = ((data or {}).get("comments") or {}).get("nodes") or []
            page_info = ((data or {}).get("comments") or {}).get("pageInfo") or {}

        messages = [
            self.parse_message(
                cast(
                    "LinearRawMessage",
                    {
                        "kind": "comment",
                        "comment": _normalize_comment(c, decoded["issueId"]),
                        "organizationId": self._resolved_organization_id(),
                    },
                )
            )
            for c in comments
        ]

        next_cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
        return {"messages": messages, "nextCursor": next_cursor}

    async def fetch_thread(self, thread_id: str) -> dict[str, Any]:
        await self._ensure_valid_token()
        decoded = self.decode_thread_id(thread_id)
        issue_id = decoded["issueId"]

        data = await self._graphql(_ISSUE_QUERY, {"id": issue_id}, "issue")
        issue = (data or {}).get("issue") or {}
        identifier = str(issue.get("identifier") or "")
        title = str(issue.get("title") or "")

        return {
            "id": thread_id,
            "channelId": f"linear:{issue_id}",
            "channelName": f"{identifier}: {title}".strip(": ") or issue_id,
            "isDM": False,
            "metadata": {
                "issueId": issue_id,
                "agentSessionId": decoded.get("agentSessionId"),
                "identifier": identifier,
                "title": title,
                "url": issue.get("url"),
            },
        }

    # ------------------------------------------------------------------
    # Low-level GraphQL + HTTP
    # ------------------------------------------------------------------

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any],
        operation: str,
    ) -> dict[str, Any] | None:
        client = await self._get_http_client()
        access_token = self._resolve_access_token()

        try:
            response = await client.post(
                self.api_url,
                headers={
                    "Authorization": access_token,
                    "Content-Type": "application/json",
                    "User-Agent": f"chat-adapter-linear/{self.user_name}",
                },
                content=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            )
        except httpx.HTTPError as err:
            raise NetworkError("linear", f"Linear API error during {operation}: {err}") from err

        if response.status_code >= 400:
            handle_linear_error(response, operation)

        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as err:
            raise NetworkError(
                "linear", f"Linear API returned invalid JSON during {operation}"
            ) from err

        handle_linear_graphql_body(body, operation)
        data = body.get("data")
        if data is None:
            return None
        return cast("dict[str, Any]", data)

    async def _create_agent_activity(
        self,
        *,
        agent_session_id: str,
        content: dict[str, Any],
        ephemeral: bool = False,
    ) -> dict[str, Any]:
        input_payload: dict[str, Any] = {
            "agentSessionId": agent_session_id,
            "content": content,
        }
        if ephemeral:
            input_payload["ephemeral"] = True

        data = await self._graphql(
            _CREATE_AGENT_ACTIVITY_MUTATION,
            {"input": input_payload},
            "agentActivityCreate",
        )
        payload = (data or {}).get("agentActivityCreate") or {}
        activity = payload.get("agentActivity")
        if not (payload.get("success") and activity):
            raise AdapterError(
                f"Failed to create Linear agent activity for session {agent_session_id}",
                "linear",
            )
        return cast("dict[str, Any]", activity)

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    def _resolve_access_token(self) -> str:
        ctx = _request_context.get()
        if ctx is not None:
            return ctx["access_token"]
        if self._default_access_token:
            return self._default_access_token
        raise AuthenticationError(
            "linear",
            "No Linear access token available. "
            "In multi-tenant mode, ensure the webhook is being processed "
            "or use with_installation().",
        )

    def _resolved_organization_id(self) -> str:
        ctx = _request_context.get()
        if ctx is not None:
            return str(ctx["installation"].get("organizationId") or "")
        return self._default_organization_id or ""

    def _bot_actor(self) -> dict[str, Any]:
        return {
            "type": "bot",
            "id": self._default_bot_user_id or "",
            "displayName": self.user_name,
            "fullName": self.user_name,
            "email": None,
            "avatarUrl": None,
        }

    async def _fetch_oauth_token(self, body: dict[str, str], error_message: str) -> dict[str, Any]:
        client = await self._get_http_client()
        try:
            response = await client.post(
                LINEAR_OAUTH_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=urlencode(body).encode("utf-8"),
            )
        except httpx.HTTPError as err:
            raise NetworkError("linear", f"{error_message}: {err}") from err

        if response.status_code >= 400:
            try:
                error_body = response.text
            except Exception:
                error_body = ""
            raise AuthenticationError(
                "linear",
                f"{error_message}: {response.status_code} {error_body}".rstrip(),
            )
        try:
            return cast("dict[str, Any]", response.json())
        except (ValueError, json.JSONDecodeError) as err:
            raise NetworkError("linear", f"{error_message}: invalid JSON response") from err

    async def _fetch_viewer_identity(self, access_token: str) -> dict[str, str]:
        client = await self._get_http_client()
        try:
            response = await client.post(
                self.api_url,
                headers={
                    "Authorization": access_token,
                    "Content-Type": "application/json",
                    "User-Agent": f"chat-adapter-linear/{self.user_name}",
                },
                content=json.dumps({"query": _VIEWER_QUERY}).encode("utf-8"),
            )
        except httpx.HTTPError as err:
            raise NetworkError("linear", f"Linear API error during viewer: {err}") from err

        if response.status_code >= 400:
            handle_linear_error(response, "viewer")

        body = response.json()
        handle_linear_graphql_body(body, "viewer")

        viewer = ((body or {}).get("data") or {}).get("viewer") or {}
        if not viewer:
            raise AuthenticationError(
                "linear",
                "Failed to resolve client identity for Linear installation.",
            )
        organization = viewer.get("organization") or {}
        return {
            "botUserId": str(viewer.get("id") or ""),
            "displayName": str(viewer.get("displayName") or ""),
            "organizationId": str(organization.get("id") or ""),
        }

    async def _refresh_client_credentials_token(self) -> None:
        if not self._client_credentials:
            return

        creds = self._client_credentials
        data = await self._fetch_oauth_token(
            {
                "grant_type": "client_credentials",
                "client_id": creds["clientId"],
                "client_secret": creds["clientSecret"],
                "scope": ",".join(creds["scopes"]),
            },
            "Failed to fetch Linear client credentials token",
        )

        self._default_access_token = str(data.get("access_token") or "")
        expires_in = data.get("expires_in")
        if isinstance(expires_in, int):
            # Refresh 1h early, match upstream.
            self._access_token_expiry_ms = int(time.time() * 1000) + expires_in * 1000 - 3600000
        else:
            self._access_token_expiry_ms = None

        self.logger.info("Linear client credentials token obtained")

    async def _ensure_valid_token(self) -> None:
        if _request_context.get() is not None:
            return
        if (
            self._client_credentials
            and self._access_token_expiry_ms is not None
            and int(time.time() * 1000) > self._access_token_expiry_ms
        ):
            self.logger.info("Linear access token expired, refreshing...")
            await self._refresh_client_credentials_token()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_env_scopes(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]


def _parse_query(query: dict[str, str] | str) -> dict[str, str]:
    if isinstance(query, dict):
        return dict(query)
    from urllib.parse import parse_qs

    parsed = parse_qs(query)
    return {k: v[0] for k, v in parsed.items() if v}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_comment(node: dict[str, Any], issue_id: str) -> LinearCommentData:
    user = node.get("user") or {}
    return {
        "id": str(node.get("id") or ""),
        "body": str(node.get("body") or ""),
        "issueId": issue_id,
        "parentId": node.get("parentId"),
        "createdAt": str(node.get("createdAt") or ""),
        "updatedAt": str(node.get("updatedAt") or ""),
        "url": node.get("url"),
        "user": {
            "type": "user",
            "id": str(user.get("id") or ""),
            "displayName": str(user.get("displayName") or ""),
            "fullName": str(user.get("name") or ""),
            "email": user.get("email"),
            "avatarUrl": user.get("avatarUrl"),
        },
    }


def create_linear_adapter(config: LinearAdapterConfig | None = None) -> LinearAdapter:
    """Factory for :class:`LinearAdapter`. Mirrors upstream ``createLinearAdapter``."""

    return LinearAdapter(config)


__all__ = [
    "LINEAR_API_URL",
    "LINEAR_OAUTH_TOKEN_URL",
    "LinearAdapter",
    "create_linear_adapter",
    "verify_linear_signature",
]
