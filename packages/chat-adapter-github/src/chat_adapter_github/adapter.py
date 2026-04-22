"""GitHub adapter for chat-py.

Python port of upstream ``packages/adapter-github/src/index.ts``.

Supports three authentication modes:

* **Personal Access Token (PAT)** — simplest, single-user.
* **Single-tenant GitHub App** — ``appId`` + ``privateKey`` + ``installationId``.
* **Multi-tenant GitHub App** — ``appId`` + ``privateKey``; the installation ID
  is extracted from each webhook payload and cached per repo in chat state.

The adapter covers PR-level comments (``issue_comment`` API, Conversation tab),
review comment threads (line-specific comments in Files Changed tab), and
issue comments. All REST calls go through :mod:`httpx`; webhook signatures
are verified with HMAC-SHA256 (``X-Hub-Signature-256``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

import httpx
from chat_adapter_shared import (
    NetworkError,
    ValidationError,
    extract_card,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .cards import card_to_github_markdown
from .errors import handle_github_error
from .markdown import GitHubFormatConverter
from .thread_id import (
    GitHubThreadId,
    channel_id_from_thread_id,
    decode_channel_id,
    decode_thread_id,
    encode_thread_id,
)
from .types import (
    GitHubIssueComment,
    GitHubRawMessage,
    GitHubReactionContent,
    GitHubRepository,
    GitHubReviewComment,
    GitHubUser,
)

if TYPE_CHECKING:
    from chat import Logger, Message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"

# Installation access tokens are valid for up to an hour; refresh a bit early.
_INSTALLATION_TOKEN_TTL_SECONDS = 50 * 60
# App JWTs are valid for up to 10 minutes; refresh early.
_APP_JWT_TTL_SECONDS = 9 * 60

_REACTION_MAP: dict[str, GitHubReactionContent] = {
    "thumbs_up": "+1",
    "+1": "+1",
    "thumbs_down": "-1",
    "-1": "-1",
    "laugh": "laugh",
    "smile": "laugh",
    "confused": "confused",
    "thinking": "confused",
    "heart": "heart",
    "love_eyes": "heart",
    "hooray": "hooray",
    "party": "hooray",
    "confetti": "hooray",
    "rocket": "rocket",
    "eyes": "eyes",
}


# ---------------------------------------------------------------------------
# Config TypedDicts
# ---------------------------------------------------------------------------


class GitHubAdapterBaseConfig(TypedDict, total=False):
    """Common fields for all :class:`GitHubAdapter` configs."""

    apiUrl: str
    botUserId: int
    logger: Logger
    userName: str
    webhookSecret: str


class GitHubAdapterPATConfig(GitHubAdapterBaseConfig, total=False):
    """PAT (personal access token) config — single user / user-token auth."""

    token: str


class GitHubAdapterAppConfig(GitHubAdapterBaseConfig, total=False):
    """Single-tenant GitHub App config — fixed ``installationId``."""

    appId: str
    installationId: int
    privateKey: str


class GitHubAdapterMultiTenantAppConfig(GitHubAdapterBaseConfig, total=False):
    """Multi-tenant GitHub App config — no fixed ``installationId``."""

    appId: str
    privateKey: str


GitHubAdapterConfig = (
    GitHubAdapterPATConfig | GitHubAdapterAppConfig | GitHubAdapterMultiTenantAppConfig
)
"""Discriminated config union for :class:`GitHubAdapter`."""


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_github_signature(secret: str, signature: str | None, body: bytes | str) -> bool:
    """Verify a GitHub webhook ``X-Hub-Signature-256`` value.

    GitHub formats the signature as ``sha256=<hex>``. Returns ``False`` on any
    error (missing header, bad hex, length mismatch, mismatch) — never raises.
    """

    if not signature:
        return False
    body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        return False
    expected = f"sha256={digest}"
    try:
        return hmac.compare_digest(signature, expected)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# GitHubAdapter
# ---------------------------------------------------------------------------


class GitHubAdapter:
    """GitHub platform adapter.

    Handles ``issue_comment`` and ``pull_request_review_comment`` webhooks,
    verifies the ``X-Hub-Signature-256`` HMAC, and exposes the standard
    :class:`chat.Adapter` Protocol via :mod:`httpx`.
    """

    name = "github"

    def __init__(self, config: GitHubAdapterConfig | None = None) -> None:
        cfg: dict[str, Any] = dict(config or {})

        webhook_secret = cfg.get("webhookSecret") or os.environ.get("GITHUB_WEBHOOK_SECRET")
        if not webhook_secret:
            raise ValidationError(
                "github",
                "webhookSecret is required. Set GITHUB_WEBHOOK_SECRET or provide it in config.",
            )
        self.webhook_secret: str = str(webhook_secret)

        logger = cfg.get("logger")
        if logger is None:
            from chat import ConsoleLogger

            logger = ConsoleLogger("info").child("github")
        self.logger: Logger = logger

        self.user_name: str = (
            cfg.get("userName") or os.environ.get("GITHUB_BOT_USERNAME") or "github-bot"
        )
        self._bot_user_id: int | None = cfg.get("botUserId")
        self.api_url: str = cfg.get("apiUrl") or os.environ.get("GITHUB_API_URL") or GITHUB_API_BASE

        token: str | None = cfg.get("token")
        app_id: str | None = cfg.get("appId")
        private_key: str | None = cfg.get("privateKey")
        installation_id: int | None = cfg.get("installationId")

        has_explicit_auth = bool(token or app_id or private_key)

        self._token: str | None = None
        self._app_id: str | None = None
        self._app_private_key: rsa.RSAPrivateKey | None = None
        self._fixed_installation_id: int | None = None
        # cache {installation_id: (token, expires_at_epoch_s)}
        self._installation_tokens: dict[int, tuple[str, float]] = {}
        # cached app JWT
        self._app_jwt: tuple[str, float] | None = None

        if token:
            self._token = str(token)
        elif app_id and private_key:
            self._app_id = str(app_id)
            self._app_private_key = _load_private_key(str(private_key))
            if installation_id:
                self._fixed_installation_id = int(installation_id)
            else:
                self.logger.info(
                    "GitHub adapter initialized in multi-tenant mode "
                    "(installation ID will be extracted from webhooks)"
                )
        elif has_explicit_auth:
            raise ValidationError(
                "github",
                "Authentication is required. Set GITHUB_TOKEN or "
                "GITHUB_APP_ID/GITHUB_PRIVATE_KEY, or provide token/appId+privateKey in config.",
            )
        else:
            env_token = os.environ.get("GITHUB_TOKEN")
            if env_token:
                self._token = env_token
            else:
                env_app_id = os.environ.get("GITHUB_APP_ID")
                env_pk = os.environ.get("GITHUB_PRIVATE_KEY")
                if env_app_id and env_pk:
                    self._app_id = env_app_id
                    self._app_private_key = _load_private_key(env_pk)
                    env_install = os.environ.get("GITHUB_INSTALLATION_ID")
                    if env_install:
                        try:
                            self._fixed_installation_id = int(env_install)
                        except ValueError as err:
                            raise ValidationError(
                                "github",
                                f"Invalid GITHUB_INSTALLATION_ID: {env_install}",
                            ) from err
                    else:
                        self.logger.info(
                            "GitHub adapter initialized in multi-tenant mode "
                            "(installation ID will be extracted from webhooks)"
                        )
                else:
                    raise ValidationError(
                        "github",
                        "Authentication is required. Set GITHUB_TOKEN or "
                        "GITHUB_APP_ID/GITHUB_PRIVATE_KEY, or provide "
                        "token/appId+privateKey in config.",
                    )

        self.format_converter = GitHubFormatConverter()
        self._http_client: httpx.AsyncClient | None = None
        self._chat: Any = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def bot_user_id(self) -> str | None:
        return str(self._bot_user_id) if self._bot_user_id is not None else None

    @property
    def is_multi_tenant(self) -> bool:
        return (
            self._app_private_key is not None
            and self._token is None
            and self._fixed_installation_id is None
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, chat: Any) -> None:
        self._chat = chat
        # Only works for PAT or fixed-installation modes
        if self._bot_user_id is None and (self._token or self._fixed_installation_id is not None):
            try:
                user = await self._request(
                    "GET",
                    "/user",
                    installation_id=self._fixed_installation_id,
                    operation="getAuthenticated",
                )
                data = user.json()
                self._bot_user_id = int(data.get("id", 0)) or None
                self.logger.info(
                    "GitHub auth completed",
                    {"botUserId": self._bot_user_id, "login": data.get("login")},
                )
            except Exception as exc:
                self.logger.warn("Could not fetch bot user ID", {"error": str(exc)})

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Thread ID helpers (delegate to module-level functions)
    # ------------------------------------------------------------------

    def encode_thread_id(self, platform_data: GitHubThreadId) -> str:
        return encode_thread_id(platform_data)

    def decode_thread_id(self, thread_id: str) -> GitHubThreadId:
        return decode_thread_id(thread_id)

    def channel_id_from_thread_id(self, thread_id: str) -> str:
        return channel_id_from_thread_id(thread_id)

    # ------------------------------------------------------------------
    # Installation management
    # ------------------------------------------------------------------

    def _installation_key(self, owner: str, repo: str) -> str:
        return f"github:install:{owner}/{repo}"

    async def _store_installation_id(self, owner: str, repo: str, installation_id: int) -> None:
        if not (self._chat and self.is_multi_tenant):
            return
        state = getattr(self._chat, "get_state", None) or getattr(self._chat, "getState", None)
        if state is None:
            return
        state_obj = state() if callable(state) else state
        setter = getattr(state_obj, "set", None)
        if setter is None:
            return
        await setter(self._installation_key(owner, repo), installation_id)
        self.logger.debug(
            "Stored installation ID",
            {"owner": owner, "repo": repo, "installationId": installation_id},
        )

    async def _get_stored_installation_id(self, owner: str, repo: str) -> int | None:
        if not (self._chat and self.is_multi_tenant):
            return None
        state = getattr(self._chat, "get_state", None) or getattr(self._chat, "getState", None)
        if state is None:
            return None
        state_obj = state() if callable(state) else state
        getter = getattr(state_obj, "get", None)
        if getter is None:
            return None
        value = await getter(self._installation_key(owner, repo))
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    async def get_installation_id(self, thread: Any) -> int | None:
        """Return the GitHub App installation ID for a thread, if any."""

        if self._fixed_installation_id is not None:
            return self._fixed_installation_id
        if not self.is_multi_tenant:
            return None
        thread_id = thread if isinstance(thread, str) else getattr(thread, "id", None)
        if thread_id is None:
            return None
        decoded = self.decode_thread_id(thread_id)
        if self._chat is None:
            raise ValidationError(
                "github",
                "Adapter not initialized. Ensure chat.initialize() has been called first.",
            )
        return await self._get_stored_installation_id(decoded["owner"], decoded["repo"])

    # ------------------------------------------------------------------
    # Webhook handling
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        body: bytes | str,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """Verify and dispatch an incoming GitHub webhook.

        Returns ``(status, headers, body)`` matching the shape used by
        :meth:`chat.Chat.handle_webhook`.
        """

        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        body_str = body_bytes.decode("utf-8", errors="replace")
        normalized = {k.lower(): v for k, v in (headers or {}).items()}
        self.logger.debug("GitHub webhook raw body", {"body": body_str[:500]})

        signature = normalized.get("x-hub-signature-256")
        if not verify_github_signature(self.webhook_secret, signature, body_bytes):
            return 401, {}, "Invalid signature"

        event_type = normalized.get("x-github-event")
        self.logger.debug("GitHub webhook event type", {"eventType": event_type})

        if event_type == "ping":
            self.logger.info("GitHub webhook ping received")
            return 200, {}, "pong"

        try:
            payload = json.loads(body_str)
        except ValueError:
            self.logger.error(
                "GitHub webhook invalid JSON",
                {
                    "contentType": normalized.get("content-type"),
                    "bodyPreview": body_str[:200],
                },
            )
            return (
                400,
                {},
                "Invalid JSON. Make sure webhook Content-Type is set to application/json",
            )

        installation = payload.get("installation") or {}
        installation_id = installation.get("id") if isinstance(installation, dict) else None
        repo = payload.get("repository") or {}
        if (
            installation_id
            and self.is_multi_tenant
            and isinstance(repo, dict)
            and isinstance(repo.get("owner"), dict)
        ):
            await self._store_installation_id(
                repo["owner"].get("login", ""),
                repo.get("name", ""),
                int(installation_id),
            )

        if event_type == "issue_comment" and payload.get("action") == "created":
            await self._handle_issue_comment(payload, installation_id)
        elif event_type == "pull_request_review_comment" and payload.get("action") == "created":
            await self._handle_review_comment(payload, installation_id)

        return 200, {}, "ok"

    async def _handle_issue_comment(
        self, payload: dict[str, Any], _installation_id: int | None
    ) -> None:
        if self._chat is None:
            self.logger.warn("Chat instance not initialized, ignoring comment")
            return

        comment = payload.get("comment") or {}
        issue = payload.get("issue") or {}
        repository = payload.get("repository") or {}
        sender = payload.get("sender") or {}

        is_pr = bool(issue.get("pull_request"))
        thread_type: Literal["pr", "issue"] = "pr" if is_pr else "issue"
        owner_login = (repository.get("owner") or {}).get("login", "")
        repo_name = repository.get("name", "")
        issue_number = int(issue.get("number", 0))

        thread_id = self.encode_thread_id(
            {
                "owner": owner_login,
                "repo": repo_name,
                "prNumber": issue_number,
                "type": thread_type,
            }
        )

        message = self._parse_issue_comment(
            cast("GitHubIssueComment", comment),
            {"owner": repository.get("owner") or {}, "name": repo_name},
            issue_number,
            thread_id,
            thread_type,
        )

        if sender.get("id") == self._bot_user_id:
            self.logger.debug(
                "Ignoring message from self",
                {"messageId": comment.get("id")},
            )
            return

        await self._dispatch_message(thread_id, message)

    async def _handle_review_comment(
        self, payload: dict[str, Any], _installation_id: int | None
    ) -> None:
        if self._chat is None:
            self.logger.warn("Chat instance not initialized, ignoring comment")
            return

        comment = payload.get("comment") or {}
        pull_request = payload.get("pull_request") or {}
        repository = payload.get("repository") or {}
        sender = payload.get("sender") or {}

        root_comment_id = int(comment.get("in_reply_to_id") or comment.get("id") or 0)
        owner_login = (repository.get("owner") or {}).get("login", "")
        repo_name = repository.get("name", "")
        pr_number = int(pull_request.get("number", 0))

        thread_id = self.encode_thread_id(
            {
                "owner": owner_login,
                "repo": repo_name,
                "prNumber": pr_number,
                "reviewCommentId": root_comment_id,
            }
        )

        message = self._parse_review_comment(
            cast("GitHubReviewComment", comment),
            {"owner": repository.get("owner") or {}, "name": repo_name},
            pr_number,
            thread_id,
        )

        if sender.get("id") == self._bot_user_id:
            self.logger.debug(
                "Ignoring message from self",
                {"messageId": comment.get("id")},
            )
            return

        await self._dispatch_message(thread_id, message)

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

    def _parse_issue_comment(
        self,
        comment: GitHubIssueComment,
        repository: dict[str, Any],
        pr_number: int,
        thread_id: str,
        thread_type: Literal["pr", "issue"] = "pr",
    ) -> Message[Any]:
        from chat import Message, MessageMetadata

        author = self._parse_author(cast("GitHubUser", comment.get("user") or {}))
        created_at = str(comment.get("created_at") or "")
        updated_at = str(comment.get("updated_at") or "")
        edited = bool(created_at and updated_at and created_at != updated_at)
        body = str(comment.get("body") or "")
        owner = cast("GitHubUser", repository.get("owner") or {})

        raw: GitHubRawMessage = cast(
            "GitHubRawMessage",
            {
                "type": "issue_comment",
                "comment": comment,
                "repository": cast(
                    "GitHubRepository",
                    {
                        "id": 0,
                        "name": str(repository.get("name") or ""),
                        "full_name": f"{owner.get('login', '')}/{repository.get('name', '')}",
                        "owner": owner,
                    },
                ),
                "prNumber": pr_number,
                "threadType": thread_type,
            },
        )

        return Message(
            id=str(comment.get("id") or ""),
            thread_id=thread_id,
            text=self.format_converter.extract_plain_text(body),
            formatted=self.format_converter.to_ast(body),
            raw=raw,
            author=author,
            metadata=MessageMetadata(
                date_sent=_parse_iso(created_at) or datetime.now(UTC),
                edited=edited,
                edited_at=_parse_iso(updated_at) if edited else None,
            ),
            attachments=[],
        )

    def _parse_review_comment(
        self,
        comment: GitHubReviewComment,
        repository: dict[str, Any],
        pr_number: int,
        thread_id: str,
    ) -> Message[Any]:
        from chat import Message, MessageMetadata

        author = self._parse_author(cast("GitHubUser", comment.get("user") or {}))
        created_at = str(comment.get("created_at") or "")
        updated_at = str(comment.get("updated_at") or "")
        edited = bool(created_at and updated_at and created_at != updated_at)
        body = str(comment.get("body") or "")
        owner = cast("GitHubUser", repository.get("owner") or {})

        raw: GitHubRawMessage = cast(
            "GitHubRawMessage",
            {
                "type": "review_comment",
                "comment": comment,
                "repository": cast(
                    "GitHubRepository",
                    {
                        "id": 0,
                        "name": str(repository.get("name") or ""),
                        "full_name": f"{owner.get('login', '')}/{repository.get('name', '')}",
                        "owner": owner,
                    },
                ),
                "prNumber": pr_number,
            },
        )

        return Message(
            id=str(comment.get("id") or ""),
            thread_id=thread_id,
            text=self.format_converter.extract_plain_text(body),
            formatted=self.format_converter.to_ast(body),
            raw=raw,
            author=author,
            metadata=MessageMetadata(
                date_sent=_parse_iso(created_at) or datetime.now(UTC),
                edited=edited,
                edited_at=_parse_iso(updated_at) if edited else None,
            ),
            attachments=[],
        )

    def _parse_author(self, user: GitHubUser) -> Any:
        from chat import Author

        user_id = user.get("id")
        login = user.get("login", "")
        return Author(
            user_id=str(user_id) if user_id is not None else "",
            user_name=login,
            full_name=login,
            is_bot=user.get("type") == "Bot",
            is_me=user_id == self._bot_user_id,
        )

    def parse_message(self, raw: GitHubRawMessage) -> Message[Any]:
        raw_dict = cast("dict[str, Any]", raw)
        if raw_dict.get("type") == "issue_comment":
            repository = raw_dict.get("repository") or {}
            owner = (repository.get("owner") or {}).get("login", "")
            repo = repository.get("name", "")
            pr_number = int(raw_dict.get("prNumber", 0))
            thread_type: Literal["pr", "issue"] = cast(
                "Literal['pr', 'issue']", raw_dict.get("threadType") or "pr"
            )
            thread_id = self.encode_thread_id(
                {
                    "owner": owner,
                    "repo": repo,
                    "prNumber": pr_number,
                    "type": thread_type,
                }
            )
            return self._parse_issue_comment(
                cast("GitHubIssueComment", raw_dict.get("comment") or {}),
                {"owner": repository.get("owner") or {}, "name": repo},
                pr_number,
                thread_id,
                thread_type,
            )
        comment = raw_dict.get("comment") or {}
        root_comment_id = int(comment.get("in_reply_to_id") or comment.get("id") or 0)
        repository = raw_dict.get("repository") or {}
        owner = (repository.get("owner") or {}).get("login", "")
        repo = repository.get("name", "")
        pr_number = int(raw_dict.get("prNumber", 0))
        thread_id = self.encode_thread_id(
            {
                "owner": owner,
                "repo": repo,
                "prNumber": pr_number,
                "reviewCommentId": root_comment_id,
            }
        )
        return self._parse_review_comment(
            cast("GitHubReviewComment", comment),
            {"owner": repository.get("owner") or {}, "name": repo},
            pr_number,
            thread_id,
        )

    def render_formatted(self, content: Any) -> str:
        return self.format_converter.from_ast(content)

    # ------------------------------------------------------------------
    # REST: post / edit / delete / reactions
    # ------------------------------------------------------------------

    async def post_message(self, thread_id: str, message: Any) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        owner = decoded["owner"]
        repo = decoded["repo"]
        pr_number = decoded["prNumber"]
        review_comment_id = decoded.get("reviewCommentId")
        thread_type = decoded.get("type") or "pr"

        body = self._render_body(message)
        installation_id = await self._installation_id_for(owner, repo)

        if review_comment_id:
            response = await self._request(
                "POST",
                f"/repos/{owner}/{repo}/pulls/{pr_number}/comments/{review_comment_id}/replies",
                installation_id=installation_id,
                json_body={"body": body},
                operation="createReplyForReviewComment",
            )
            comment = response.json()
            return {
                "id": str(comment.get("id", "")),
                "threadId": thread_id,
                "raw": {
                    "type": "review_comment",
                    "comment": comment,
                    "repository": _minimal_repo(owner, repo),
                    "prNumber": pr_number,
                },
            }
        response = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            installation_id=installation_id,
            json_body={"body": body},
            operation="createComment",
        )
        comment = response.json()
        return {
            "id": str(comment.get("id", "")),
            "threadId": thread_id,
            "raw": {
                "type": "issue_comment",
                "comment": comment,
                "repository": _minimal_repo(owner, repo),
                "prNumber": pr_number,
                "threadType": thread_type,
            },
        }

    async def edit_message(self, thread_id: str, message_id: str, message: Any) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        owner = decoded["owner"]
        repo = decoded["repo"]
        pr_number = decoded["prNumber"]
        review_comment_id = decoded.get("reviewCommentId")
        thread_type = decoded.get("type") or "pr"

        body = self._render_body(message)
        comment_id = int(message_id)
        installation_id = await self._installation_id_for(owner, repo)

        if review_comment_id:
            response = await self._request(
                "PATCH",
                f"/repos/{owner}/{repo}/pulls/comments/{comment_id}",
                installation_id=installation_id,
                json_body={"body": body},
                operation="updateReviewComment",
            )
            comment = response.json()
            return {
                "id": str(comment.get("id", "")),
                "threadId": thread_id,
                "raw": {
                    "type": "review_comment",
                    "comment": comment,
                    "repository": _minimal_repo(owner, repo),
                    "prNumber": pr_number,
                },
            }
        response = await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            installation_id=installation_id,
            json_body={"body": body},
            operation="updateComment",
        )
        comment = response.json()
        return {
            "id": str(comment.get("id", "")),
            "threadId": thread_id,
            "raw": {
                "type": "issue_comment",
                "comment": comment,
                "repository": _minimal_repo(owner, repo),
                "prNumber": pr_number,
                "threadType": thread_type,
            },
        }

    async def delete_message(self, thread_id: str, message_id: str) -> None:
        decoded = self.decode_thread_id(thread_id)
        owner = decoded["owner"]
        repo = decoded["repo"]
        review_comment_id = decoded.get("reviewCommentId")
        comment_id = int(message_id)
        installation_id = await self._installation_id_for(owner, repo)

        if review_comment_id:
            await self._request(
                "DELETE",
                f"/repos/{owner}/{repo}/pulls/comments/{comment_id}",
                installation_id=installation_id,
                operation="deleteReviewComment",
            )
        else:
            await self._request(
                "DELETE",
                f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
                installation_id=installation_id,
                operation="deleteComment",
            )

    async def stream(
        self,
        thread_id: str,
        text_stream: Any,
        _options: Any = None,
    ) -> dict[str, Any]:
        """Accumulate streaming text and post once — GitHub has no live edit flow."""

        text = ""
        async for chunk in text_stream:
            if isinstance(chunk, str):
                text += chunk
            elif isinstance(chunk, dict) and chunk.get("type") == "markdown_text":
                text += str(chunk.get("text", ""))
        return await self.post_message(thread_id, {"markdown": text})

    async def add_reaction(self, thread_id: str, message_id: str, emoji: Any) -> None:
        decoded = self.decode_thread_id(thread_id)
        owner = decoded["owner"]
        repo = decoded["repo"]
        review_comment_id = decoded.get("reviewCommentId")
        comment_id = int(message_id)
        content = _emoji_to_github_reaction(emoji)
        installation_id = await self._installation_id_for(owner, repo)

        path = (
            f"/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions"
            if review_comment_id
            else f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
        )
        await self._request(
            "POST",
            path,
            installation_id=installation_id,
            json_body={"content": content},
            operation="createReaction",
        )

    async def remove_reaction(self, thread_id: str, message_id: str, emoji: Any) -> None:
        decoded = self.decode_thread_id(thread_id)
        owner = decoded["owner"]
        repo = decoded["repo"]
        review_comment_id = decoded.get("reviewCommentId")
        comment_id = int(message_id)
        content = _emoji_to_github_reaction(emoji)
        installation_id = await self._installation_id_for(owner, repo)

        # Multi-tenant mode has no global token, so initialize() can't detect bot_user_id.
        if self._bot_user_id is None:
            try:
                user_response = await self._request(
                    "GET",
                    "/user",
                    installation_id=installation_id,
                    operation="getAuthenticated",
                )
                self._bot_user_id = int(user_response.json().get("id", 0)) or None
            except Exception:
                self.logger.warn("Could not detect bot user ID for reaction removal")

        path = (
            f"/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions"
            if review_comment_id
            else f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
        )
        response = await self._request(
            "GET",
            path,
            installation_id=installation_id,
            operation="listReactions",
        )
        reactions = response.json() or []
        reaction = next(
            (
                r
                for r in reactions
                if r.get("content") == content
                and ((r.get("user") or {}).get("id") == self._bot_user_id)
            ),
            None,
        )
        if reaction:
            await self._request(
                "DELETE",
                f"{path}/{reaction['id']}",
                installation_id=installation_id,
                operation="deleteReaction",
            )

    async def start_typing(self, _thread_id: str, _status: str | None = None) -> None:
        """GitHub has no typing indicator."""

    # ------------------------------------------------------------------
    # REST: fetch / list
    # ------------------------------------------------------------------

    async def fetch_messages(self, thread_id: str, options: Any = None) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        owner = decoded["owner"]
        repo = decoded["repo"]
        pr_number = decoded["prNumber"]
        review_comment_id = decoded.get("reviewCommentId")
        thread_type = decoded.get("type") or "pr"

        opts = options if isinstance(options, dict) else {}
        limit = int(opts.get("limit") or 100)
        direction = opts.get("direction") or "backward"

        installation_id = await self._installation_id_for(owner, repo)
        messages: list[Message[Any]]

        if review_comment_id:
            response = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls/{pr_number}/comments?per_page=100",
                installation_id=installation_id,
                operation="listReviewComments",
            )
            all_comments = response.json() or []
            thread_comments = [
                c
                for c in all_comments
                if c.get("id") == review_comment_id or c.get("in_reply_to_id") == review_comment_id
            ]
            messages = [
                self._parse_review_comment(
                    cast("GitHubReviewComment", c),
                    {
                        "owner": {"id": 0, "login": owner, "type": "User"},
                        "name": repo,
                    },
                    pr_number,
                    thread_id,
                )
                for c in thread_comments
            ]
        else:
            response = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page={limit}",
                installation_id=installation_id,
                operation="listComments",
            )
            comments = response.json() or []
            messages = [
                self._parse_issue_comment(
                    cast("GitHubIssueComment", c),
                    {
                        "owner": {"id": 0, "login": owner, "type": "User"},
                        "name": repo,
                    },
                    pr_number,
                    thread_id,
                    cast("Literal['pr', 'issue']", thread_type),
                )
                for c in comments
            ]

        messages.sort(key=lambda m: m.metadata.date_sent)
        if direction == "backward" and len(messages) > limit:
            messages = messages[-limit:]
        elif direction == "forward" and len(messages) > limit:
            messages = messages[:limit]

        return {"messages": messages, "nextCursor": None}

    async def fetch_thread(self, thread_id: str) -> dict[str, Any]:
        decoded = self.decode_thread_id(thread_id)
        owner = decoded["owner"]
        repo = decoded["repo"]
        pr_number = decoded["prNumber"]
        review_comment_id = decoded.get("reviewCommentId")
        thread_type = decoded.get("type") or "pr"

        installation_id = await self._installation_id_for(owner, repo)

        if thread_type == "issue":
            response = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/issues/{pr_number}",
                installation_id=installation_id,
                operation="getIssue",
            )
            issue = response.json()
            return {
                "id": thread_id,
                "channelId": f"{owner}/{repo}",
                "channelName": f"{repo} #{pr_number}",
                "isDM": False,
                "metadata": {
                    "owner": owner,
                    "repo": repo,
                    "issueNumber": pr_number,
                    "issueTitle": issue.get("title"),
                    "issueState": issue.get("state"),
                    "type": "issue",
                },
            }

        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            installation_id=installation_id,
            operation="getPull",
        )
        pr = response.json()
        return {
            "id": thread_id,
            "channelId": f"{owner}/{repo}",
            "channelName": f"{repo} #{pr_number}",
            "isDM": False,
            "metadata": {
                "owner": owner,
                "repo": repo,
                "prNumber": pr_number,
                "prTitle": pr.get("title"),
                "prState": pr.get("state"),
                "reviewCommentId": review_comment_id,
            },
        }

    async def list_threads(self, channel_id: str, options: Any = None) -> dict[str, Any]:
        owner, repo = decode_channel_id(channel_id)
        opts = options if isinstance(options, dict) else {}
        limit = int(opts.get("limit") or 30)
        cursor = opts.get("cursor")
        page = int(cursor) if cursor else 1
        installation_id = await self._installation_id_for(owner, repo)

        self.logger.debug("GitHub API: pulls.list", {"owner": owner, "repo": repo, "limit": limit})

        response = await self._request(
            "GET",
            (
                f"/repos/{owner}/{repo}/pulls"
                f"?state=open&sort=updated&direction=desc"
                f"&per_page={limit}&page={page}"
            ),
            installation_id=installation_id,
            operation="listPulls",
        )
        pulls = response.json() or []

        threads: list[dict[str, Any]] = []
        for pr in pulls:
            thread_id = self.encode_thread_id(
                {
                    "owner": owner,
                    "repo": repo,
                    "prNumber": int(pr.get("number", 0)),
                }
            )
            threads.append(
                {
                    "id": thread_id,
                    "rootMessage": self._build_pr_root_message(pr, owner, repo, thread_id),
                    "lastReplyAt": _parse_iso(str(pr.get("updated_at") or "")),
                }
            )

        next_cursor = str(page + 1) if len(pulls) == limit else None
        return {"threads": threads, "nextCursor": next_cursor}

    async def fetch_channel_info(self, channel_id: str) -> dict[str, Any]:
        owner, repo = decode_channel_id(channel_id)
        installation_id = await self._installation_id_for(owner, repo)
        self.logger.debug("GitHub API: repos.get", {"owner": owner, "repo": repo})
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}",
            installation_id=installation_id,
            operation="getRepo",
        )
        repo_data = response.json()
        return {
            "id": channel_id,
            "name": repo_data.get("full_name", f"{owner}/{repo}"),
            "isDM": False,
            "metadata": {
                "owner": owner,
                "repo": repo,
                "description": repo_data.get("description"),
                "visibility": repo_data.get("visibility"),
                "defaultBranch": repo_data.get("default_branch"),
                "openIssuesCount": repo_data.get("open_issues_count"),
            },
        }

    # ------------------------------------------------------------------
    # Internal: rendering + HTTP
    # ------------------------------------------------------------------

    def _render_body(self, message: Any) -> str:
        from chat import convert_emoji_placeholders

        card = extract_card(message)
        if card:
            body = card_to_github_markdown(cast("dict[str, Any]", card))
        else:
            body = self.format_converter.render_postable(message)
        return convert_emoji_placeholders(body, "github")

    def _build_pr_root_message(
        self, pr: dict[str, Any], owner: str, repo: str, thread_id: str
    ) -> Message[Any]:
        from chat import Message, MessageMetadata

        title = str(pr.get("title") or "")
        body = str(pr.get("body") or title)
        user = cast("GitHubUser", pr.get("user") or {})
        created_at = str(pr.get("created_at") or "")
        updated_at = str(pr.get("updated_at") or "")

        raw: GitHubRawMessage = cast(
            "GitHubRawMessage",
            {
                "type": "issue_comment",
                "comment": {
                    "id": int(pr.get("number", 0)),
                    "body": body,
                    "user": user,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "html_url": str(pr.get("html_url") or ""),
                },
                "repository": _minimal_repo(owner, repo),
                "prNumber": int(pr.get("number", 0)),
                "threadType": "pr",
            },
        )

        return Message(
            id=str(pr.get("number") or ""),
            thread_id=thread_id,
            text=title,
            formatted=self.format_converter.to_ast(title),
            raw=raw,
            author=self._parse_author(user),
            metadata=MessageMetadata(
                date_sent=_parse_iso(created_at) or datetime.now(UTC),
                edited=bool(created_at and updated_at and created_at != updated_at),
            ),
            attachments=[],
        )

    async def _installation_id_for(self, owner: str, repo: str) -> int | None:
        if self._fixed_installation_id is not None:
            return self._fixed_installation_id
        if self.is_multi_tenant:
            return await self._get_stored_installation_id(owner, repo)
        return None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        installation_id: int | None = None,
        json_body: Any = None,
        operation: str,
    ) -> httpx.Response:
        client = await self._get_http_client()
        auth_token = await self._resolve_auth_token(installation_id)
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"chat-adapter-github/{self.user_name}",
        }
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        content: bytes | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            content = json.dumps(json_body).encode("utf-8")

        url = f"{self.api_url}{path}"
        try:
            response = await client.request(method, url, headers=headers, content=content)
        except httpx.HTTPError as err:
            raise NetworkError(
                "github",
                f"GitHub API error during {operation}: {err}",
            ) from err
        if response.status_code >= 400:
            handle_github_error(response, operation)
        return response

    async def _resolve_auth_token(self, installation_id: int | None) -> str | None:
        if self._token:
            return self._token
        if self._app_private_key is None or self._app_id is None:
            return None
        resolved_installation = installation_id or self._fixed_installation_id
        if resolved_installation is None:
            raise ValidationError(
                "github",
                "Installation ID required for multi-tenant mode. "
                "This usually means you're trying to make an API call outside of a webhook context. "
                "For proactive messages, use thread IDs from previous webhook interactions.",
            )
        return await self._get_installation_token(resolved_installation)

    def _get_app_jwt(self) -> str:
        now = time.time()
        if self._app_jwt and self._app_jwt[1] > now + 30:
            return self._app_jwt[0]
        assert self._app_private_key is not None and self._app_id is not None

        iat = int(now) - 60  # clock skew buffer
        exp = iat + _APP_JWT_TTL_SECONDS
        payload = {"iat": iat, "exp": exp, "iss": self._app_id}
        token = _rs256_encode({"alg": "RS256", "typ": "JWT"}, payload, self._app_private_key)
        self._app_jwt = (token, exp)
        return token

    async def _get_installation_token(self, installation_id: int) -> str:
        now = time.time()
        cached = self._installation_tokens.get(installation_id)
        if cached and cached[1] > now + 60:
            return cached[0]

        client = await self._get_http_client()
        jwt_token = self._get_app_jwt()
        url = f"{self.api_url}/app/installations/{installation_id}/access_tokens"
        try:
            response = await client.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {jwt_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": f"chat-adapter-github/{self.user_name}",
                },
            )
        except httpx.HTTPError as err:
            raise NetworkError(
                "github",
                f"GitHub API error during createInstallationToken: {err}",
            ) from err

        if response.status_code >= 400:
            handle_github_error(response, "createInstallationToken")

        data = response.json()
        token = str(data.get("token", ""))
        expires_at = now + _INSTALLATION_TOKEN_TTL_SECONDS
        self._installation_tokens[installation_id] = (token, expires_at)
        return token


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _minimal_repo(owner: str, repo: str) -> dict[str, Any]:
    return {
        "id": 0,
        "name": repo,
        "full_name": f"{owner}/{repo}",
        "owner": {"id": 0, "login": owner, "type": "User"},
    }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _emoji_to_github_reaction(emoji: Any) -> GitHubReactionContent:
    name = (
        emoji
        if isinstance(emoji, str)
        else (emoji.get("name") if isinstance(emoji, dict) else getattr(emoji, "name", None))
    )
    if not isinstance(name, str):
        return "+1"
    return _REACTION_MAP.get(name, "+1")


def _load_private_key(key_text: str) -> rsa.RSAPrivateKey:
    """Load an RSA private key from a PEM string.

    Accepts either a literal PEM or an environment-variable value where newlines
    have been encoded as ``\\n`` (common with dotenv / shell). Raises
    :class:`ValidationError` on bad input.
    """

    try:
        pem = key_text.replace("\\n", "\n").encode("utf-8")
        loaded = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as err:
        raise ValidationError("github", f"Invalid GitHub private key: {err}") from err
    if not isinstance(loaded, rsa.RSAPrivateKey):
        raise ValidationError("github", "GitHub private key must be an RSA key")
    return loaded


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _rs256_encode(
    header: dict[str, Any], payload: dict[str, Any], private_key: rsa.RSAPrivateKey
) -> str:
    """Produce a compact RS256 JWT — just enough for GitHub App auth.

    Pulling in PyJWT would be fine but adds a dependency for a 10-line routine.
    """

    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


def create_github_adapter(config: GitHubAdapterConfig | None = None) -> GitHubAdapter:
    """Factory for :class:`GitHubAdapter`. Mirrors upstream ``createGitHubAdapter``."""

    return GitHubAdapter(config)


__all__ = [
    "GITHUB_API_BASE",
    "GitHubAdapter",
    "GitHubAdapterAppConfig",
    "GitHubAdapterBaseConfig",
    "GitHubAdapterConfig",
    "GitHubAdapterMultiTenantAppConfig",
    "GitHubAdapterPATConfig",
    "create_github_adapter",
    "verify_github_signature",
]
