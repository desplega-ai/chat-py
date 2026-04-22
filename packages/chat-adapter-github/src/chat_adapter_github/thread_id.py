"""Thread ID encoding/decoding for the GitHub adapter.

Python port of upstream ``packages/adapter-github/src/index.ts`` (the
``encodeThreadId`` / ``decodeThreadId`` / ``channelIdFromThreadId`` helpers).

Thread ID formats:

* PR-level: ``github:{owner}/{repo}:{prNumber}``
* Issue-level: ``github:{owner}/{repo}:issue:{issueNumber}``
* Review comment: ``github:{owner}/{repo}:{prNumber}:rc:{reviewCommentId}``
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from chat_adapter_shared import ValidationError

_REVIEW_COMMENT_THREAD_PATTERN = re.compile(r"^([^/]+)/([^:]+):(\d+):rc:(\d+)$")
_ISSUE_THREAD_PATTERN = re.compile(r"^([^/]+)/([^:]+):issue:(\d+)$")
_PR_THREAD_PATTERN = re.compile(r"^([^/]+)/([^:]+):(\d+)$")


class GitHubThreadId(TypedDict, total=False):
    """Decoded GitHub thread ID data.

    ``prNumber`` carries both PR numbers and issue numbers because GitHub
    uses a shared number space for issues and pull requests.
    """

    owner: str
    prNumber: int
    repo: str
    reviewCommentId: int
    type: Literal["pr", "issue"]


def encode_thread_id(platform_data: GitHubThreadId) -> str:
    """Build the canonical GitHub thread ID string.

    Raises :class:`ValidationError` when ``type == "issue"`` is combined with
    a ``reviewCommentId`` — review comments only exist on pull requests.
    """

    owner = platform_data["owner"]
    repo = platform_data["repo"]
    pr_number = platform_data["prNumber"]
    thread_type = platform_data.get("type")
    review_comment_id = platform_data.get("reviewCommentId")

    if thread_type == "issue" and review_comment_id is not None:
        raise ValidationError(
            "github",
            "Review comments are not supported on issue threads",
        )

    if thread_type == "issue":
        return f"github:{owner}/{repo}:issue:{pr_number}"
    if review_comment_id is not None:
        return f"github:{owner}/{repo}:{pr_number}:rc:{review_comment_id}"
    return f"github:{owner}/{repo}:{pr_number}"


def decode_thread_id(thread_id: str) -> GitHubThreadId:
    """Inverse of :func:`encode_thread_id`.

    Raises :class:`ValidationError` on malformed input or a non-``github:``
    prefix.
    """

    if not thread_id.startswith("github:"):
        raise ValidationError("github", f"Invalid GitHub thread ID: {thread_id}")

    without_prefix = thread_id[len("github:") :]

    rc_match = _REVIEW_COMMENT_THREAD_PATTERN.match(without_prefix)
    if rc_match:
        return {
            "owner": rc_match.group(1),
            "repo": rc_match.group(2),
            "prNumber": int(rc_match.group(3)),
            "type": "pr",
            "reviewCommentId": int(rc_match.group(4)),
        }

    issue_match = _ISSUE_THREAD_PATTERN.match(without_prefix)
    if issue_match:
        return {
            "owner": issue_match.group(1),
            "repo": issue_match.group(2),
            "prNumber": int(issue_match.group(3)),
            "type": "issue",
        }

    pr_match = _PR_THREAD_PATTERN.match(without_prefix)
    if pr_match:
        return {
            "owner": pr_match.group(1),
            "repo": pr_match.group(2),
            "prNumber": int(pr_match.group(3)),
            "type": "pr",
        }

    raise ValidationError("github", f"Invalid GitHub thread ID format: {thread_id}")


def channel_id_from_thread_id(thread_id: str) -> str:
    """Derive the channel ID (``github:{owner}/{repo}``) from a thread ID."""

    decoded = decode_thread_id(thread_id)
    return f"github:{decoded['owner']}/{decoded['repo']}"


def decode_channel_id(channel_id: str) -> tuple[str, str]:
    """Decode ``github:{owner}/{repo}`` into ``(owner, repo)``.

    Raises :class:`ValidationError` if the prefix is missing or the body lacks
    the ``/`` separator.
    """

    if not channel_id.startswith("github:"):
        raise ValidationError("github", f"Invalid GitHub channel ID: {channel_id}")
    without_prefix = channel_id[len("github:") :]
    slash_index = without_prefix.find("/")
    if slash_index == -1:
        raise ValidationError("github", f"Invalid GitHub channel ID: {channel_id}")
    return without_prefix[:slash_index], without_prefix[slash_index + 1 :]


__all__ = [
    "GitHubThreadId",
    "channel_id_from_thread_id",
    "decode_channel_id",
    "decode_thread_id",
    "encode_thread_id",
]
