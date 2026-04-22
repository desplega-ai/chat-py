"""Thread ID encoding/decoding for the Google Chat adapter.

Python port of upstream ``packages/adapter-gchat/src/thread-utils.ts``.

Thread ID format: ``gchat:{spaceName}[:{b64url(threadName)}][:dm]``.

The space name (``spaces/XYZ``) contains a ``/`` and no ``:`` so it survives a
direct ``str.split(":")``. The thread name is base64url-encoded because it
usually contains a ``/`` too but, more importantly, may include a ``:`` that
would collide with the delimiter.
"""

from __future__ import annotations

import base64
from typing import TypedDict

from chat_adapter_shared import ValidationError


class GoogleChatThreadId(TypedDict, total=False):
    """Decoded Google Chat-specific thread ID data."""

    isDM: bool
    spaceName: str
    threadName: str


def _b64url_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def encode_thread_id(platform_data: GoogleChatThreadId) -> str:
    """Build the canonical thread ID string.

    ``{"spaceName": "spaces/ABC"}`` → ``"gchat:spaces/ABC"``
    ``{"spaceName": "spaces/ABC", "threadName": "spaces/ABC/threads/t1"}`` →
    ``"gchat:spaces/ABC:<b64>"``
    ``{"spaceName": "spaces/DM", "isDM": True}`` → ``"gchat:spaces/DM:dm"``
    """

    space_name = platform_data["spaceName"]
    thread_name = platform_data.get("threadName")
    is_dm = platform_data.get("isDM", False)

    thread_part = f":{_b64url_encode(thread_name)}" if thread_name else ""
    dm_part = ":dm" if is_dm else ""
    return f"gchat:{space_name}{thread_part}{dm_part}"


def decode_thread_id(thread_id: str) -> GoogleChatThreadId:
    """Inverse of :func:`encode_thread_id`.

    Raises :class:`ValidationError` on malformed input.
    """

    is_dm = thread_id.endswith(":dm")
    clean_id = thread_id[:-3] if is_dm else thread_id

    parts = clean_id.split(":")
    if len(parts) < 2 or parts[0] != "gchat":
        raise ValidationError("gchat", f"Invalid Google Chat thread ID: {thread_id}")

    space_name = parts[1]
    thread_name = _b64url_decode(parts[2]) if len(parts) >= 3 and parts[2] else None

    result: GoogleChatThreadId = {"spaceName": space_name, "isDM": is_dm}
    if thread_name is not None:
        result["threadName"] = thread_name
    return result


def is_dm_thread(thread_id: str) -> bool:
    """Return ``True`` if the thread ID has a trailing ``:dm`` marker."""

    return thread_id.endswith(":dm")


__all__ = [
    "GoogleChatThreadId",
    "decode_thread_id",
    "encode_thread_id",
    "is_dm_thread",
]
