"""Type definitions for the GitHub adapter.

Python port of upstream ``packages/adapter-github/src/types.ts``. GitHub
webhook payloads are plain JSON; these :class:`TypedDict` shells document
the fields we actually touch so callers get IDE completion without forcing
runtime validation.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# =============================================================================
# Raw message and user/repo shapes
# =============================================================================


class GitHubUser(TypedDict, total=False):
    """GitHub user object (simplified)."""

    avatar_url: str
    id: int
    login: str
    type: Literal["User", "Bot", "Organization"]


class GitHubRepository(TypedDict, total=False):
    """GitHub repository object (simplified)."""

    full_name: str
    id: int
    name: str
    owner: GitHubUser


class GitHubPullRequest(TypedDict, total=False):
    """GitHub pull request object (simplified)."""

    body: str | None
    html_url: str
    id: int
    number: int
    state: Literal["open", "closed"]
    title: str
    user: GitHubUser


class GitHubIssueComment(TypedDict, total=False):
    """GitHub issue comment (PR-level comment in Conversation tab)."""

    body: str
    created_at: str
    html_url: str
    id: int
    reactions: dict[str, int | str]
    updated_at: str
    user: GitHubUser


class GitHubReviewComment(TypedDict, total=False):
    """GitHub pull request review comment (line-specific)."""

    body: str
    commit_id: str
    created_at: str
    diff_hunk: str
    html_url: str
    id: int
    in_reply_to_id: int
    line: int
    original_commit_id: str
    original_line: int
    path: str
    reactions: dict[str, int | str]
    side: Literal["LEFT", "RIGHT"]
    start_line: int | None
    start_side: Literal["LEFT", "RIGHT"] | None
    updated_at: str
    user: GitHubUser


class GitHubInstallation(TypedDict, total=False):
    """GitHub App installation info included in webhook payloads."""

    id: int
    node_id: str


class GitHubIssueRef(TypedDict, total=False):
    """Minimal issue reference shape used in issue_comment payloads."""

    number: int
    title: str
    pull_request: dict[str, str]


class IssueCommentWebhookPayload(TypedDict, total=False):
    """Webhook payload for ``issue_comment`` events."""

    action: Literal["created", "edited", "deleted"]
    comment: GitHubIssueComment
    installation: GitHubInstallation
    issue: GitHubIssueRef
    repository: GitHubRepository
    sender: GitHubUser


class PullRequestReviewCommentWebhookPayload(TypedDict, total=False):
    """Webhook payload for ``pull_request_review_comment`` events."""

    action: Literal["created", "edited", "deleted"]
    comment: GitHubReviewComment
    installation: GitHubInstallation
    pull_request: GitHubPullRequest
    repository: GitHubRepository
    sender: GitHubUser


# =============================================================================
# Raw message discriminated union
# =============================================================================


class GitHubIssueCommentRaw(TypedDict, total=False):
    """Raw message payload for issue comments."""

    type: Literal["issue_comment"]
    comment: GitHubIssueComment
    repository: GitHubRepository
    prNumber: int
    threadType: Literal["pr", "issue"]


class GitHubReviewCommentRaw(TypedDict, total=False):
    """Raw message payload for review comments."""

    type: Literal["review_comment"]
    comment: GitHubReviewComment
    repository: GitHubRepository
    prNumber: int


GitHubRawMessage = GitHubIssueCommentRaw | GitHubReviewCommentRaw
"""Platform-specific raw message type for GitHub.

Discriminated on ``type`` (``"issue_comment"`` vs. ``"review_comment"``).
"""


# =============================================================================
# Reaction content enum
# =============================================================================


GitHubReactionContent = Literal[
    "+1",
    "-1",
    "laugh",
    "confused",
    "heart",
    "hooray",
    "rocket",
    "eyes",
]
"""Reaction content types supported by the GitHub REST API."""


__all__ = [
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
    "GitHubUser",
    "IssueCommentWebhookPayload",
    "PullRequestReviewCommentWebhookPayload",
]
