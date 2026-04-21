"""Persistent message history cache — port of ``packages/chat/src/message-history.ts``.

:class:`MessageHistoryCache` is used by adapters that lack server-side
message history APIs (e.g., WhatsApp, Telegram). Messages are atomically
appended via :meth:`chat.types.StateAdapter.append_to_list`, which is safe
to call without holding a thread lock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat.message import Message

if TYPE_CHECKING:
    from chat.types import StateAdapter

# Default maximum number of messages to store per thread.
DEFAULT_MAX_MESSAGES = 100

# Default TTL for message history (7 days in milliseconds).
DEFAULT_TTL_MS = 7 * 24 * 60 * 60 * 1000

# Key prefix for message history entries.
KEY_PREFIX = "msg-history:"


class MessageHistoryCache:
    """Persistent per-thread message history cache backed by a :class:`StateAdapter`.

    Atomically appends via :meth:`StateAdapter.append_to_list` and trims to
    ``max_messages`` (keeps newest). ``raw`` is nulled before persisting to
    save storage.
    """

    __slots__ = ("_max_messages", "_state", "_ttl_ms")

    def __init__(
        self,
        state: StateAdapter,
        *,
        max_messages: int | None = None,
        ttl_ms: int | None = None,
    ) -> None:
        self._state = state
        self._max_messages = max_messages if max_messages is not None else DEFAULT_MAX_MESSAGES
        self._ttl_ms = ttl_ms if ttl_ms is not None else DEFAULT_TTL_MS

    async def append(self, thread_id: str, message: Message) -> None:
        """Atomically append a message to the history for a thread.

        Trims to ``max_messages`` (keeps newest) and refreshes TTL. ``raw``
        is nulled out to save storage.
        """
        key = f"{KEY_PREFIX}{thread_id}"
        serialized = message.to_json()
        # Null the raw platform message to save storage — matches upstream.
        serialized["raw"] = None
        await self._state.append_to_list(
            key,
            serialized,
            {"maxLength": self._max_messages, "ttlMs": self._ttl_ms},
        )

    async def get_messages(self, thread_id: str, limit: int | None = None) -> list[Message]:
        """Get messages for a thread in chronological order (oldest first).

        :param thread_id: The thread ID.
        :param limit: Optional limit — returns the newest ``limit`` messages.
        """
        key = f"{KEY_PREFIX}{thread_id}"
        stored: list[dict] = await self._state.get_list(key)

        if limit is not None and len(stored) > limit:
            sliced = stored[len(stored) - limit :]
        else:
            sliced = stored

        return [Message.from_json(s) for s in sliced]
