"""Shared test utilities for chat package tests — port of ``packages/chat/src/mock-adapter.ts``.

Provides:

- :data:`mock_logger` — a :class:`~chat.logger.Logger` that captures all log calls.
- :func:`create_mock_adapter` — creates an in-process mock :class:`~chat.types.Adapter`
  with :class:`unittest.mock.AsyncMock` method stubs.
- :func:`create_mock_state` — creates a mock :class:`~chat.types.StateAdapter`
  with working in-memory subscriptions, locks, and cache.
- :func:`create_test_message` — creates a test :class:`~chat.message.Message`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from chat.logger import Logger, LogLevel
from chat.markdown import parse_markdown
from chat.message import Message
from chat.types import Lock, QueueEntry

# ============================================================================
# mock_logger
# ============================================================================


class _CapturingLogger:
    """A :class:`~chat.logger.Logger` that captures all log calls via :class:`MagicMock`."""

    __slots__ = ("child_mock", "debug", "error", "info", "log", "warn")

    def __init__(self) -> None:
        self.debug: MagicMock = MagicMock()
        self.info: MagicMock = MagicMock()
        self.warn: MagicMock = MagicMock()
        self.error: MagicMock = MagicMock()
        self.log: MagicMock = MagicMock()
        self.child_mock: MagicMock = MagicMock(return_value=self)

    def child(self, _bindings: dict[str, Any] | None = None) -> Logger:
        return self.child_mock(_bindings)

    def reset(self) -> None:
        """Reset all captured calls — handy inside per-test ``setUp``."""
        for mock in (
            self.debug,
            self.info,
            self.warn,
            self.error,
            self.log,
            self.child_mock,
        ):
            mock.reset_mock()


mock_logger = _CapturingLogger()
"""A singleton capturing logger, shared across tests — matches upstream ``mockLogger``.

Call :meth:`mock_logger.reset` between tests if you care about a clean history.
"""


# ============================================================================
# create_mock_adapter
# ============================================================================


def create_mock_adapter(name: str = "slack") -> Any:
    """Create a mock :class:`~chat.types.Adapter` for testing.

    Returns a :class:`~types.SimpleNamespace` whose methods are
    :class:`~unittest.mock.AsyncMock` (for async methods) or
    :class:`~unittest.mock.MagicMock` (for sync methods). Supports the same
    assertion helpers as the upstream ``vi.fn()``-based mocks.
    """

    # async methods
    async_noop = AsyncMock(return_value=None)
    post_msg_default = {"id": "msg-1", "threadId": None, "raw": {}}

    def _decode(id: str) -> dict[str, str | None]:
        parts = id.split(":")
        channel = parts[1] if len(parts) > 1 else None
        thread = parts[2] if len(parts) > 2 else None
        return {"channel": channel, "thread": thread}

    # `encode_thread_id` in Python mirrors the upstream behavior: takes a dict
    # with ``channel`` / ``thread`` keys and returns ``"{name}:{channel}:{thread}"``.
    encode_mock = MagicMock(side_effect=lambda data: f"{name}:{data['channel']}:{data['thread']}")
    decode_mock = MagicMock(side_effect=_decode)

    open_dm_mock = AsyncMock(side_effect=lambda user_id: f"{name}:D{user_id}:")
    is_dm_mock = MagicMock(side_effect=lambda thread_id: ":D" in thread_id)

    channel_id_from_thread_id_mock = MagicMock(
        side_effect=lambda thread_id: ":".join(thread_id.split(":")[:2])
    )

    fetch_channel_info_mock = AsyncMock(
        side_effect=lambda channel_id: {
            "id": channel_id,
            "name": f"#{channel_id}",
            "isDM": False,
            "metadata": {},
        }
    )

    adapter = SimpleNamespace(
        name=name,
        user_name=f"{name}-bot",
        initialize=AsyncMock(return_value=None),
        disconnect=AsyncMock(return_value=None),
        handle_webhook=AsyncMock(return_value=(200, {}, b"ok")),
        post_message=AsyncMock(return_value=dict(post_msg_default)),
        edit_message=AsyncMock(return_value=dict(post_msg_default)),
        delete_message=async_noop,
        add_reaction=async_noop,
        remove_reaction=async_noop,
        start_typing=async_noop,
        fetch_messages=AsyncMock(return_value={"messages": [], "nextCursor": None}),
        fetch_thread=AsyncMock(return_value={"id": "t1", "channelId": "c1", "metadata": {}}),
        fetch_message=AsyncMock(return_value=None),
        encode_thread_id=encode_mock,
        decode_thread_id=decode_mock,
        parse_message=MagicMock(),
        render_formatted=MagicMock(return_value="formatted"),
        open_dm=open_dm_mock,
        is_dm=is_dm_mock,
        get_channel_visibility=MagicMock(return_value="unknown"),
        open_modal=AsyncMock(return_value={"viewId": "V123"}),
        channel_id_from_thread_id=channel_id_from_thread_id_mock,
        fetch_channel_messages=AsyncMock(return_value={"messages": [], "nextCursor": None}),
        list_threads=AsyncMock(return_value={"threads": [], "nextCursor": None}),
        fetch_channel_info=fetch_channel_info_mock,
        post_channel_message=AsyncMock(return_value=dict(post_msg_default)),
    )
    return adapter


# ============================================================================
# create_mock_state
# ============================================================================


@dataclass(slots=True)
class MockStateAdapter:
    """Mock state adapter with working in-memory storage.

    Methods are :class:`~unittest.mock.AsyncMock` (matching upstream
    ``vi.fn()``), so tests can assert on calls. ``cache`` exposes the backing
    :class:`dict` for direct inspection.
    """

    cache: dict[str, Any] = field(default_factory=dict)
    subscriptions: set[str] = field(default_factory=set)
    locks: dict[str, Lock] = field(default_factory=dict)
    queues: dict[str, list[QueueEntry]] = field(default_factory=dict)

    connect: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=None))
    disconnect: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=None))
    subscribe: AsyncMock = field(default_factory=lambda: AsyncMock())
    unsubscribe: AsyncMock = field(default_factory=lambda: AsyncMock())
    is_subscribed: AsyncMock = field(default_factory=lambda: AsyncMock())
    acquire_lock: AsyncMock = field(default_factory=lambda: AsyncMock())
    force_release_lock: AsyncMock = field(default_factory=lambda: AsyncMock())
    release_lock: AsyncMock = field(default_factory=lambda: AsyncMock())
    extend_lock: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=True))
    get: AsyncMock = field(default_factory=lambda: AsyncMock())
    set: AsyncMock = field(default_factory=lambda: AsyncMock())
    set_if_not_exists: AsyncMock = field(default_factory=lambda: AsyncMock())
    delete: AsyncMock = field(default_factory=lambda: AsyncMock())
    append_to_list: AsyncMock = field(default_factory=lambda: AsyncMock())
    enqueue: AsyncMock = field(default_factory=lambda: AsyncMock())
    dequeue: AsyncMock = field(default_factory=lambda: AsyncMock())
    queue_depth: AsyncMock = field(default_factory=lambda: AsyncMock())
    get_list: AsyncMock = field(default_factory=lambda: AsyncMock())


def _now_ms() -> int:
    """Return the current Unix time in milliseconds (matches JS ``Date.now()``)."""
    return int(datetime.now(UTC).timestamp() * 1000)


def create_mock_state() -> MockStateAdapter:
    """Create a mock state adapter for testing.

    Has working in-memory subscriptions, locks, cache, and queues. All methods
    are :class:`AsyncMock` so tests can still assert on call counts/arguments.
    """
    state = MockStateAdapter()

    async def subscribe(thread_id: str) -> None:
        state.subscriptions.add(thread_id)

    async def unsubscribe(thread_id: str) -> None:
        state.subscriptions.discard(thread_id)

    async def is_subscribed(thread_id: str) -> bool:
        return thread_id in state.subscriptions

    async def acquire_lock(thread_id: str, ttl_ms: int) -> Lock | None:
        if thread_id in state.locks:
            return None
        lock = Lock(thread_id=thread_id, token="test-token", expires_at=_now_ms() + ttl_ms)
        state.locks[thread_id] = lock
        return lock

    async def force_release_lock(thread_id: str) -> None:
        state.locks.pop(thread_id, None)

    async def release_lock(lock: Lock) -> None:
        state.locks.pop(lock.thread_id, None)

    async def get_(key: str) -> Any:
        return state.cache.get(key)

    async def set_(key: str, value: Any, ttl_ms: int | None = None) -> None:
        state.cache[key] = value

    async def set_if_not_exists(key: str, value: Any, ttl_ms: int | None = None) -> bool:
        if key in state.cache:
            return False
        state.cache[key] = value
        return True

    async def delete_(key: str) -> None:
        state.cache.pop(key, None)

    async def append_to_list(key: str, value: Any, options: dict[str, Any] | None = None) -> None:
        current = state.cache.get(key)
        lst: list[Any] = list(current) if isinstance(current, list) else []
        lst.append(value)
        max_length = options.get("maxLength") if options else None
        if max_length and len(lst) > max_length:
            lst = lst[len(lst) - max_length :]
        state.cache[key] = lst

    async def enqueue(thread_id: str, entry: QueueEntry, max_size: int) -> int:
        queue = state.queues.setdefault(thread_id, [])
        queue.append(entry)
        if len(queue) > max_size:
            del queue[: len(queue) - max_size]
        return len(queue)

    async def dequeue(thread_id: str) -> QueueEntry | None:
        queue = state.queues.get(thread_id)
        if not queue:
            return None
        entry = queue.pop(0)
        if not queue:
            state.queues.pop(thread_id, None)
        return entry

    async def queue_depth(thread_id: str) -> int:
        queue = state.queues.get(thread_id)
        return len(queue) if queue else 0

    async def get_list(key: str) -> list[Any]:
        val = state.cache.get(key)
        return list(val) if isinstance(val, list) else []

    # Wire side-effects onto the AsyncMocks so call assertions still work.
    state.subscribe.side_effect = subscribe
    state.unsubscribe.side_effect = unsubscribe
    state.is_subscribed.side_effect = is_subscribed
    state.acquire_lock.side_effect = acquire_lock
    state.force_release_lock.side_effect = force_release_lock
    state.release_lock.side_effect = release_lock
    state.get.side_effect = get_
    state.set.side_effect = set_
    state.set_if_not_exists.side_effect = set_if_not_exists
    state.delete.side_effect = delete_
    state.append_to_list.side_effect = append_to_list
    state.enqueue.side_effect = enqueue
    state.dequeue.side_effect = dequeue
    state.queue_depth.side_effect = queue_depth
    state.get_list.side_effect = get_list

    return state


# ============================================================================
# create_test_message
# ============================================================================


def create_test_message(
    id: str,
    text: str,
    **overrides: Any,
) -> Message[Any]:
    """Create a test :class:`~chat.message.Message` with sensible defaults.

    ``overrides`` takes the same kwargs as :class:`~chat.message.Message`
    (e.g. ``thread_id``, ``raw``, ``author``, ``metadata``, ``attachments``,
    ``is_mention``, ``links``).
    """
    from chat.types import Author, MessageMetadata

    defaults: dict[str, Any] = {
        "id": id,
        "thread_id": "slack:C123:1234.5678",
        "text": text,
        "formatted": parse_markdown(text),
        "raw": {},
        "author": Author(
            user_id="U123",
            user_name="testuser",
            full_name="Test User",
            is_bot=False,
            is_me=False,
        ),
        "metadata": MessageMetadata(
            date_sent=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            edited=False,
        ),
        "attachments": [],
        "links": [],
    }
    defaults.update(overrides)
    return Message(**defaults)


# Re-export LogLevel for symmetry with ts (mock-adapter.ts does not import it,
# but some downstream tests will need it).
__all__ = [
    "LogLevel",
    "MockStateAdapter",
    "create_mock_adapter",
    "create_mock_state",
    "create_test_message",
    "mock_logger",
]
