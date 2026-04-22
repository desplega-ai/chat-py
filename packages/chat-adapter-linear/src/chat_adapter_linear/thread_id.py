"""Thread ID encoding/decoding for the Linear adapter.

Python port of upstream ``packages/adapter-linear/src/index.ts``
``encodeThreadId`` / ``decodeThreadId`` / ``channelIdFromThreadId``.

Thread-ID formats:

* Issue-level: ``linear:{issueId}``
* Comment thread: ``linear:{issueId}:c:{commentId}``
* Agent-session (issue-level): ``linear:{issueId}:s:{agentSessionId}``
* Agent-session (comment): ``linear:{issueId}:c:{commentId}:s:{agentSessionId}``
"""

from __future__ import annotations

import re

from chat_adapter_shared import ValidationError

from .types import LinearThreadId

_COMMENT_SESSION_THREAD_PATTERN = re.compile(r"^([^:]+):c:([^:]+):s:([^:]+)$")
_COMMENT_THREAD_PATTERN = re.compile(r"^([^:]+):c:([^:]+)$")
_ISSUE_SESSION_THREAD_PATTERN = re.compile(r"^([^:]+):s:([^:]+)$")


def encode_thread_id(platform_data: LinearThreadId) -> str:
    """Build the canonical Linear thread ID string from decoded parts.

    ``issueId`` is required; ``commentId`` and ``agentSessionId`` both optional.
    """

    issue_id = platform_data.get("issueId")
    if not issue_id:
        raise ValidationError("linear", "issueId is required to encode a Linear thread ID")

    comment_id = platform_data.get("commentId")
    agent_session_id = platform_data.get("agentSessionId")

    if agent_session_id:
        if comment_id:
            return f"linear:{issue_id}:c:{comment_id}:s:{agent_session_id}"
        return f"linear:{issue_id}:s:{agent_session_id}"

    if comment_id:
        return f"linear:{issue_id}:c:{comment_id}"
    return f"linear:{issue_id}"


def decode_thread_id(thread_id: str) -> LinearThreadId:
    """Inverse of :func:`encode_thread_id`.

    Raises :class:`ValidationError` on missing prefix or malformed body.
    """

    if not thread_id.startswith("linear:"):
        raise ValidationError("linear", f"Invalid Linear thread ID: {thread_id}")

    without_prefix = thread_id[len("linear:") :]
    if not without_prefix:
        raise ValidationError("linear", f"Invalid Linear thread ID format: {thread_id}")

    comment_session = _COMMENT_SESSION_THREAD_PATTERN.match(without_prefix)
    if comment_session:
        return {
            "issueId": comment_session.group(1),
            "commentId": comment_session.group(2),
            "agentSessionId": comment_session.group(3),
        }

    issue_session = _ISSUE_SESSION_THREAD_PATTERN.match(without_prefix)
    if issue_session:
        return {
            "issueId": issue_session.group(1),
            "agentSessionId": issue_session.group(2),
        }

    comment = _COMMENT_THREAD_PATTERN.match(without_prefix)
    if comment:
        return {
            "issueId": comment.group(1),
            "commentId": comment.group(2),
        }

    # Issue-level format — the remainder must be a bare issue ID with no ``:``.
    if ":" in without_prefix:
        raise ValidationError("linear", f"Invalid Linear thread ID format: {thread_id}")
    return {"issueId": without_prefix}


def channel_id_from_thread_id(thread_id: str) -> str:
    """Derive the channel ID (``linear:{issueId}``) from a thread ID."""

    decoded = decode_thread_id(thread_id)
    return f"linear:{decoded['issueId']}"


__all__ = [
    "channel_id_from_thread_id",
    "decode_thread_id",
    "encode_thread_id",
]
