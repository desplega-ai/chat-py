"""Type definitions for the Linear adapter.

Python port of upstream ``packages/adapter-linear/src/types.ts``. Upstream
leans on ``@linear/sdk`` for the domain model; here we use :class:`TypedDict`
shells for the payload fields the adapter actually touches, plus dataclass
config variants that mirror the TypeScript discriminated union.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from chat import Logger

# =============================================================================
# Configuration
# =============================================================================


class LinearClientCredentialsConfig(TypedDict, total=False):
    """Client-credentials grant config. ``scopes`` defaults to the standard set."""

    clientId: str
    clientSecret: str
    scopes: list[str]


class LinearInstallation(TypedDict, total=False):
    """Data stored per Linear workspace installation in multi-tenant mode."""

    accessToken: str
    botUserId: str
    expiresAt: int | None
    organizationId: str
    refreshToken: str


class LinearOAuthCallbackOptions(TypedDict, total=False):
    """Options for the OAuth callback exchange."""

    redirectUri: str


LinearAdapterMode = Literal["agent-sessions", "comments"]
"""Incoming webhook handling mode for the Linear adapter."""


class LinearAdapterBaseConfig(TypedDict, total=False):
    """Base configuration shared by all auth methods."""

    apiUrl: str
    logger: Logger
    mode: LinearAdapterMode
    userName: str
    webhookSecret: str


class LinearAdapterAPIKeyConfig(LinearAdapterBaseConfig, total=False):
    """API-key auth — simplest, suitable for personal bots or testing."""

    apiKey: str


class LinearAdapterOAuthConfig(LinearAdapterBaseConfig, total=False):
    """OAuth access-token auth — use a pre-obtained token."""

    accessToken: str


class LinearAdapterMultiTenantConfig(LinearAdapterBaseConfig, total=False):
    """Multi-tenant OAuth-app auth — requires ``clientId`` and ``clientSecret``."""

    clientId: str
    clientSecret: str


class LinearAdapterClientCredentialsConfig(LinearAdapterBaseConfig, total=False):
    """Client-credentials auth — single-tenant, adapter-managed token refresh."""

    clientCredentials: LinearClientCredentialsConfig


LinearAdapterConfig = (
    LinearAdapterAPIKeyConfig
    | LinearAdapterOAuthConfig
    | LinearAdapterMultiTenantConfig
    | LinearAdapterClientCredentialsConfig
)
"""Discriminated config union for :class:`LinearAdapter`."""


# =============================================================================
# Auth
# =============================================================================


class LinearOAuthTokenResponse(TypedDict, total=False):
    """Response shape for Linear OAuth ``/oauth/token`` exchanges."""

    access_token: str
    expires_in: int
    refresh_token: str


# =============================================================================
# Thread ID
# =============================================================================


class LinearThreadId(TypedDict, total=False):
    """Decoded thread ID for Linear.

    ``commentId`` present → comment-level thread (replies nest under this comment).
    ``commentId`` absent → issue-level thread (top-level comments).
    ``agentSessionId`` present → agent-session thread overlay.
    """

    agentSessionId: str
    commentId: str
    issueId: str


LinearAgentSessionThreadId = LinearThreadId
"""A :class:`LinearThreadId` where ``agentSessionId`` is guaranteed present."""


# =============================================================================
# Raw Message Type
# =============================================================================


class LinearActorData(TypedDict, total=False):
    """Data associated with a Linear actor."""

    avatarUrl: str | None
    displayName: str
    email: str | None
    fullName: str
    id: str
    type: Literal["user", "bot"]


class LinearCommentData(TypedDict, total=False):
    """Comment data stored in a :data:`LinearRawMessage`."""

    body: str
    createdAt: str
    id: str
    issueId: str
    parentId: str | None
    updatedAt: str
    url: str | None
    user: LinearActorData


class LinearCommentRawMessage(TypedDict, total=False):
    """Raw message for a standard Linear comment."""

    kind: Literal["comment"]
    comment: LinearCommentData
    organizationId: str


class LinearAgentSessionCommentRawMessage(TypedDict, total=False):
    """Raw message for a comment backed by an agent session."""

    kind: Literal["agent_session_comment"]
    agentSessionId: str
    agentSessionPromptContext: str
    comment: LinearCommentData
    organizationId: str


LinearRawMessage = LinearCommentRawMessage | LinearAgentSessionCommentRawMessage
"""Platform-specific raw message type for Linear.

Discriminated on ``kind``: ``"comment"`` vs ``"agent_session_comment"``.
"""


__all__ = [
    "LinearActorData",
    "LinearAdapterAPIKeyConfig",
    "LinearAdapterBaseConfig",
    "LinearAdapterClientCredentialsConfig",
    "LinearAdapterConfig",
    "LinearAdapterMode",
    "LinearAdapterMultiTenantConfig",
    "LinearAdapterOAuthConfig",
    "LinearAgentSessionCommentRawMessage",
    "LinearAgentSessionThreadId",
    "LinearClientCredentialsConfig",
    "LinearCommentData",
    "LinearCommentRawMessage",
    "LinearInstallation",
    "LinearOAuthCallbackOptions",
    "LinearOAuthTokenResponse",
    "LinearRawMessage",
    "LinearThreadId",
]
