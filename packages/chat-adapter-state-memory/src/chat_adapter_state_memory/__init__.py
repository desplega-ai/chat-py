"""In-memory state adapter for chat-py (development and testing).

Python port of upstream ``packages/state-memory/src/index.ts``. Stores
subscriptions, locks, cache entries, lists, and queues in process memory.

WARNING: state is not persisted across restarts. Use a persistent state
adapter (e.g. ``chat-adapter-state-pg``) for production.
"""

from __future__ import annotations

import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Any, TypedDict

__version__ = "0.1.0"

__all__ = [
    "MemoryStateAdapter",
    "__version__",
    "create_memory_state",
]


# ---------------------------------------------------------------------------
# Types — mirror the ``chat`` core ``Lock`` / ``QueueEntry`` shapes.
#
# We re-declare them locally as ``TypedDict`` so this module loads cleanly
# even before chat-core exports the canonical Protocols. Once chat-core ships
# them, callers can pass either dicts or those Protocols interchangeably.
# ---------------------------------------------------------------------------


class _LockDict(TypedDict, total=False):
    thread_id: str
    token: str
    expires_at: int


class _QueueEntryDict(TypedDict, total=False):
    message: Any
    enqueued_at: int
    expires_at: int


@dataclass(slots=True)
class _MemoryLock:
    thread_id: str
    token: str
    expires_at: int

    def to_dict(self) -> _LockDict:
        return {
            "thread_id": self.thread_id,
            "token": self.token,
            "expires_at": self.expires_at,
        }


@dataclass(slots=True)
class _CachedValue:
    value: Any
    expires_at: int | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    """Return the current time as integer milliseconds since the epoch."""

    return int(time.time() * 1000)


def _generate_token() -> str:
    """Generate a unique-enough opaque lock token."""

    # Mirrors upstream's ``mem_<ts>_<rand>`` format. Random component uses
    # ``secrets`` for cryptographic uniqueness instead of Math.random.
    return f"mem_{_now_ms()}_{secrets.token_hex(8)}"


def _lock_token(lock: Any) -> str | None:
    """Read ``token`` from a dict-shaped or attr-shaped lock."""

    if isinstance(lock, dict):
        return lock.get("token")
    return getattr(lock, "token", None)


def _lock_thread_id(lock: Any) -> str | None:
    """Read ``thread_id`` from a dict-shaped or attr-shaped lock."""

    if isinstance(lock, dict):
        return lock.get("thread_id") or lock.get("threadId")
    return getattr(lock, "thread_id", None) or getattr(lock, "threadId", None)


# ---------------------------------------------------------------------------
# MemoryStateAdapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemoryStateAdapter:
    """In-memory implementation of the ``chat`` ``StateAdapter`` protocol.

    Suitable for development, testing, and single-process bots. For
    multi-process or production deployments, use a persistent backend such as
    :mod:`chat_adapter_state_pg`.
    """

    _subscriptions: set[str] = field(default_factory=set, init=False)
    _locks: dict[str, _MemoryLock] = field(default_factory=dict, init=False)
    _cache: dict[str, _CachedValue] = field(default_factory=dict, init=False)
    _queues: dict[str, list[Any]] = field(default_factory=dict, init=False)
    _connected: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return

        if os.environ.get("NODE_ENV") == "production":
            print(
                "[chat] MemoryStateAdapter is not recommended for production. "
                "Consider using chat-adapter-state-pg instead.",
                file=sys.stderr,
            )
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self._subscriptions.clear()
        self._locks.clear()
        self._queues.clear()

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def subscribe(self, thread_id: str) -> None:
        self._ensure_connected()
        self._subscriptions.add(thread_id)

    async def unsubscribe(self, thread_id: str) -> None:
        self._ensure_connected()
        self._subscriptions.discard(thread_id)

    async def is_subscribed(self, thread_id: str) -> bool:
        self._ensure_connected()
        return thread_id in self._subscriptions

    # ------------------------------------------------------------------
    # Locks
    # ------------------------------------------------------------------

    async def acquire_lock(self, thread_id: str, ttl_ms: int) -> _LockDict | None:
        self._ensure_connected()
        self._clean_expired_locks()

        existing = self._locks.get(thread_id)
        if existing is not None and existing.expires_at > _now_ms():
            return None

        lock = _MemoryLock(
            thread_id=thread_id,
            token=_generate_token(),
            expires_at=_now_ms() + ttl_ms,
        )
        self._locks[thread_id] = lock
        return lock.to_dict()

    async def force_release_lock(self, thread_id: str) -> None:
        self._ensure_connected()
        self._locks.pop(thread_id, None)

    async def release_lock(self, lock: Any) -> None:
        self._ensure_connected()

        thread_id = _lock_thread_id(lock)
        if thread_id is None:
            return

        existing = self._locks.get(thread_id)
        if existing is not None and existing.token == _lock_token(lock):
            self._locks.pop(thread_id, None)

    async def extend_lock(self, lock: Any, ttl_ms: int) -> bool:
        self._ensure_connected()

        thread_id = _lock_thread_id(lock)
        if thread_id is None:
            return False

        existing = self._locks.get(thread_id)
        if existing is None or existing.token != _lock_token(lock):
            return False

        if existing.expires_at < _now_ms():
            self._locks.pop(thread_id, None)
            return False

        existing.expires_at = _now_ms() + ttl_ms
        return True

    # ------------------------------------------------------------------
    # Generic cache
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        self._ensure_connected()

        cached = self._cache.get(key)
        if cached is None:
            return None

        if cached.expires_at is not None and cached.expires_at <= _now_ms():
            self._cache.pop(key, None)
            return None

        return cached.value

    async def set(self, key: str, value: Any, ttl_ms: int | None = None) -> None:
        self._ensure_connected()

        self._cache[key] = _CachedValue(
            value=value,
            expires_at=(_now_ms() + ttl_ms) if ttl_ms else None,
        )

    async def set_if_not_exists(
        self,
        key: str,
        value: Any,
        ttl_ms: int | None = None,
    ) -> bool:
        self._ensure_connected()

        existing = self._cache.get(key)
        if existing is not None:
            if existing.expires_at is not None and existing.expires_at <= _now_ms():
                self._cache.pop(key, None)
            else:
                return False

        self._cache[key] = _CachedValue(
            value=value,
            expires_at=(_now_ms() + ttl_ms) if ttl_ms else None,
        )
        return True

    async def delete(self, key: str) -> None:
        self._ensure_connected()
        self._cache.pop(key, None)

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    async def append_to_list(
        self,
        key: str,
        value: Any,
        options: dict[str, int] | None = None,
    ) -> None:
        self._ensure_connected()

        options = options or {}
        max_length = options.get("max_length") or options.get("maxLength")
        ttl_ms = options.get("ttl_ms") or options.get("ttlMs")

        cached = self._cache.get(key)
        list_value: list[Any]

        if (
            cached is not None
            and cached.expires_at is not None
            and cached.expires_at <= _now_ms()
        ):
            list_value = []
        elif cached is not None and isinstance(cached.value, list):
            list_value = cached.value
        else:
            list_value = []

        list_value.append(value)

        if max_length and len(list_value) > max_length:
            list_value = list_value[len(list_value) - max_length :]

        self._cache[key] = _CachedValue(
            value=list_value,
            expires_at=(_now_ms() + ttl_ms) if ttl_ms else None,
        )

    async def get_list(self, key: str) -> list[Any]:
        self._ensure_connected()

        cached = self._cache.get(key)
        if cached is None:
            return []

        if cached.expires_at is not None and cached.expires_at <= _now_ms():
            self._cache.pop(key, None)
            return []

        if isinstance(cached.value, list):
            return cached.value

        return []

    # ------------------------------------------------------------------
    # Queues
    # ------------------------------------------------------------------

    async def enqueue(self, thread_id: str, entry: Any, max_size: int) -> int:
        self._ensure_connected()

        queue = self._queues.get(thread_id)
        if queue is None:
            queue = []
            self._queues[thread_id] = queue

        queue.append(entry)

        if len(queue) > max_size:
            del queue[: len(queue) - max_size]

        return len(queue)

    async def dequeue(self, thread_id: str) -> Any | None:
        self._ensure_connected()

        queue = self._queues.get(thread_id)
        if not queue:
            return None

        entry = queue.pop(0)

        if not queue:
            self._queues.pop(thread_id, None)

        return entry

    async def queue_depth(self, thread_id: str) -> int:
        self._ensure_connected()

        queue = self._queues.get(thread_id)
        return len(queue) if queue else 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError(
                "MemoryStateAdapter is not connected. Call connect() first."
            )

    def _clean_expired_locks(self) -> None:
        now = _now_ms()
        expired = [tid for tid, lock in self._locks.items() if lock.expires_at <= now]
        for thread_id in expired:
            self._locks.pop(thread_id, None)

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def _get_subscription_count(self) -> int:
        return len(self._subscriptions)

    def _get_lock_count(self) -> int:
        self._clean_expired_locks()
        return len(self._locks)


def create_memory_state() -> MemoryStateAdapter:
    """Factory mirroring upstream ``createMemoryState()``."""

    return MemoryStateAdapter()
