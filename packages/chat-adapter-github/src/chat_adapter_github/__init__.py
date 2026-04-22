"""GitHub adapter for chat-py.

Python port of upstream ``packages/adapter-github``. Exposes
:class:`GitHubAdapter` and helpers for thread-id encoding, webhook signature
verification, card-to-markdown translation, and a GFM-flavoured format
converter.
"""

from __future__ import annotations

from chat_adapter_github.adapter import (
    GITHUB_API_BASE,
    GitHubAdapter,
    GitHubAdapterAppConfig,
    GitHubAdapterBaseConfig,
    GitHubAdapterConfig,
    GitHubAdapterMultiTenantAppConfig,
    GitHubAdapterPATConfig,
    create_github_adapter,
    verify_github_signature,
)
from chat_adapter_github.cards import card_to_github_markdown, card_to_plain_text
from chat_adapter_github.errors import handle_github_error
from chat_adapter_github.markdown import GitHubFormatConverter
from chat_adapter_github.thread_id import (
    GitHubThreadId,
    channel_id_from_thread_id,
    decode_channel_id,
    decode_thread_id,
    encode_thread_id,
)
from chat_adapter_github.types import (
    GitHubInstallation,
    GitHubIssueComment,
    GitHubIssueCommentRaw,
    GitHubIssueRef,
    GitHubPullRequest,
    GitHubRawMessage,
    GitHubReactionContent,
    GitHubRepository,
    GitHubReviewComment,
    GitHubReviewCommentRaw,
    GitHubUser,
    IssueCommentWebhookPayload,
    PullRequestReviewCommentWebhookPayload,
)

__version__ = "0.1.0"

__all__ = [
    "GITHUB_API_BASE",
    "GitHubAdapter",
    "GitHubAdapterAppConfig",
    "GitHubAdapterBaseConfig",
    "GitHubAdapterConfig",
    "GitHubAdapterMultiTenantAppConfig",
    "GitHubAdapterPATConfig",
    "GitHubFormatConverter",
    "GitHubInstallation",
    "GitHubIssueComment",
    "GitHubIssueCommentRaw",
    "GitHubIssueRef",
    "GitHubPullRequest",
    "GitHubRawMessage",
    "GitHubReactionContent",
    "GitHubRepository",
    "GitHubReviewComment",
    "GitHubReviewCommentRaw",
    "GitHubThreadId",
    "GitHubUser",
    "IssueCommentWebhookPayload",
    "PullRequestReviewCommentWebhookPayload",
    "card_to_github_markdown",
    "card_to_plain_text",
    "channel_id_from_thread_id",
    "create_github_adapter",
    "decode_channel_id",
    "decode_thread_id",
    "encode_thread_id",
    "handle_github_error",
    "verify_github_signature",
]
