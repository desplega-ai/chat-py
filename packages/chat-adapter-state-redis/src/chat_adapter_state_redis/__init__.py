"""Redis state adapter for chat-py.

Python port of upstream ``packages/state-redis/src/index.ts``. Backs the
``chat`` ``StateAdapter`` protocol with Redis, using the ``redis`` package's
native asyncio API (``redis.asyncio``). Suitable for multi-process /
production deployments where an in-memory backend would be lost or
inconsistent across instances.

Key layout is identical to upstream
(``{key_prefix}:{sub,lock,cache,queue,list}:{id}`` plus a subscriptions
set at ``{key_prefix}:subscriptions``) so a single Redis instance can
serve both Python and TypeScript SDKs side-by-side.

The adapter accepts either a Redis ``url`` (we create and own a client)
or an existing ``client`` (we do not tear it down on ``disconnect``).
Tests can also inject any client-like object that implements the subset
of ``redis.asyncio.Redis`` methods actually used by the adapter — the
:mod:`fakeredis` async client works out of the box.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

__all__ = [
    "RedisStateAdapter",
    "__version__",
    "create_redis_state",
]


if TYPE_CHECKING:  # pragma: no cover
    from chat import Logger


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    """Current time as integer milliseconds since the epoch."""

    return int(time.time() * 1000)


def _generate_token() -> str:
    """Generate a redis-flavoured opaque lock token.

    Mirrors upstream's ``redis_<ts>_<rand>`` format. Random component uses
    ``secrets`` for cryptographic uniqueness instead of ``Math.random``.
    """

    return f"redis_{_now_ms()}_{secrets.token_hex(6)}"


def _lock_thread_id(lock: Any) -> str | None:
    if isinstance(lock, dict):
        return lock.get("thread_id") or lock.get("threadId")
    return getattr(lock, "thread_id", None) or getattr(lock, "threadId", None)


def _lock_token(lock: Any) -> str | None:
    if isinstance(lock, dict):
        return lock.get("token")
    return getattr(lock, "token", None)


def _entry_expires_at_ms(entry: Any) -> int | None:
    if isinstance(entry, dict):
        value = entry.get("expires_at") or entry.get("expiresAt")
    else:
        value = getattr(entry, "expires_at", None) or getattr(entry, "expiresAt", None)
    if value is None:
        return None
    return int(value)


def _decode_bytes(value: Any) -> Any:
    """Normalise a redis return value to ``str`` when it's ``bytes``.

    ``redis.asyncio`` returns ``bytes`` by default; we don't force
    ``decode_responses=True`` on injected clients because callers may share
    a client with other code that expects bytes. We handle the conversion
    centrally on reads.
    """

    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _parse_cached_json(value: Any) -> Any:
    """Attempt JSON decoding; fall back to the raw string if it fails.

    Upstream tries ``JSON.parse`` and falls back to returning the raw string
    — we do the same.
    """

    if value is None:
        return None
    decoded = _decode_bytes(value)
    try:
        return json.loads(decoded)
    except (TypeError, ValueError):
        return decoded


def _entry_from_bytes(value: Any) -> Any:
    if value is None:
        return None
    decoded = _decode_bytes(value)
    return json.loads(decoded)


# Lua scripts — shared with upstream byte-for-byte.
_RELEASE_LOCK_SCRIPT = """
      if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
      else
        return 0
      end
    """

_EXTEND_LOCK_SCRIPT = """
      if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("pexpire", KEYS[1], ARGV[2])
      else
        return 0
      end
    """

_APPEND_TO_LIST_SCRIPT = """
      redis.call("rpush", KEYS[1], ARGV[1])
      if tonumber(ARGV[2]) > 0 then
        redis.call("ltrim", KEYS[1], -tonumber(ARGV[2]), -1)
      end
      if tonumber(ARGV[3]) > 0 then
        redis.call("pexpire", KEYS[1], tonumber(ARGV[3]))
      end
      return 1
    """

_ENQUEUE_SCRIPT = """
      redis.call("rpush", KEYS[1], ARGV[1])
      if tonumber(ARGV[2]) > 0 then
        redis.call("ltrim", KEYS[1], -tonumber(ARGV[2]), -1)
      end
      redis.call("pexpire", KEYS[1], ARGV[3])
      return redis.call("llen", KEYS[1])
    """


# ---------------------------------------------------------------------------
# Logger fallback
# ---------------------------------------------------------------------------


class _NullLogger:
    """Minimal ``Logger`` implementation used when none is supplied."""

    def debug(self, message: str, *args: object) -> None:  # pragma: no cover
        pass

    def info(self, message: str, *args: object) -> None:  # pragma: no cover
        pass

    def warn(self, message: str, *args: object) -> None:  # pragma: no cover
        pass

    def error(self, message: str, *args: object) -> None:  # pragma: no cover
        pass

    def child(self, prefix: str) -> _NullLogger:  # pragma: no cover
        return self


def _default_logger() -> Logger:
    """Return a ``ConsoleLogger('info').child('redis')`` when available."""

    try:
        from chat import ConsoleLogger

        return ConsoleLogger("info").child("redis")
    except Exception:  # pragma: no cover — optional dep
        return _NullLogger()


# ---------------------------------------------------------------------------
# RedisStateAdapter
# ---------------------------------------------------------------------------


class RedisStateAdapter:
    """Redis-backed implementation of the ``chat`` ``StateAdapter`` protocol.

    Suitable for multi-process and production deployments. State is persisted
    in Redis and shared across concurrent processes using ``SET NX PX`` for
    lock acquisition and Lua scripts for compare-and-delete / extend / atomic
    list trim.
    """

    _client: Any
    _key_prefix: str
    _logger: Any
    _owns_client: bool
    _connected: bool

    def __init__(
        self,
        *,
        url: str | None = None,
        client: Any | None = None,
        key_prefix: str | None = None,
        logger: Any | None = None,
    ) -> None:
        """Construct a Redis state adapter.

        Exactly one of ``url`` or ``client`` must be provided.

        Args:
            url: Redis connection URL (e.g. ``redis://localhost:6379``). When
                provided, the adapter creates and owns an async ``Redis``
                client.
            client: Existing ``redis.asyncio.Redis`` (or compatible) instance.
                The adapter will use it without closing it on
                :meth:`disconnect`.
            key_prefix: Prefix for every Redis key the adapter reads or
                writes. Defaults to ``"chat-sdk"`` to match upstream.
            logger: Optional ``chat.Logger`` instance. Defaults to a silent
                logger if the ``chat`` package isn't available.
        """

        if client is not None and url is not None:
            raise ValueError("RedisStateAdapter accepts either url= or client=, not both.")

        if client is not None:
            self._client = client
            self._owns_client = False
        elif url is not None:
            self._client = self._create_client_from_url(url)
            self._owns_client = True
        else:
            raise ValueError("RedisStateAdapter requires either url= or client=.")

        self._key_prefix = key_prefix or "chat-sdk"
        self._logger = logger if logger is not None else _default_logger()
        self._connected = False

    @staticmethod
    def _create_client_from_url(url: str) -> Any:
        """Lazily import ``redis.asyncio`` and build a client from ``url``."""

        import redis.asyncio as aredis  # local import — optional runtime dep

        return aredis.Redis.from_url(url)

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _key(self, kind: str, id_: str) -> str:
        return f"{self._key_prefix}:{kind}:{id_}"

    def _subscriptions_set_key(self) -> str:
        return f"{self._key_prefix}:subscriptions"

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return

        try:
            await self._client.ping()
            self._connected = True
        except Exception as error:
            self._logger.error("Redis connect failed", {"error": error})
            raise

    async def disconnect(self) -> None:
        if not self._connected:
            return

        if self._owns_client:
            # Prefer ``aclose`` (redis-py >=5) but fall back to ``close`` for
            # duck-typed clients that only expose the legacy method name.
            closer = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
            if closer is not None:
                await closer()

        self._connected = False

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def subscribe(self, thread_id: str) -> None:
        self._ensure_connected()
        await self._client.sadd(self._subscriptions_set_key(), thread_id)

    async def unsubscribe(self, thread_id: str) -> None:
        self._ensure_connected()
        await self._client.srem(self._subscriptions_set_key(), thread_id)

    async def is_subscribed(self, thread_id: str) -> bool:
        self._ensure_connected()
        result = await self._client.sismember(self._subscriptions_set_key(), thread_id)
        # redis-py returns either ``bool`` (RESP3) or ``int`` (RESP2); both
        # map cleanly to a truthy/falsy check.
        return bool(result)

    # ------------------------------------------------------------------
    # Locks
    # ------------------------------------------------------------------

    async def acquire_lock(self, thread_id: str, ttl_ms: int) -> dict[str, Any] | None:
        self._ensure_connected()

        token = _generate_token()
        lock_key = self._key("lock", thread_id)

        # Atomic SET NX PX for lock acquisition.
        acquired = await self._client.set(lock_key, token, nx=True, px=ttl_ms)

        if not acquired:
            return None

        return {
            "thread_id": thread_id,
            "token": token,
            "expires_at": _now_ms() + ttl_ms,
        }

    async def force_release_lock(self, thread_id: str) -> None:
        self._ensure_connected()
        lock_key = self._key("lock", thread_id)
        await self._client.delete(lock_key)

    async def release_lock(self, lock: Any) -> None:
        self._ensure_connected()

        thread_id = _lock_thread_id(lock)
        token = _lock_token(lock)
        if thread_id is None or token is None:
            return

        lock_key = self._key("lock", thread_id)
        await self._client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)

    async def extend_lock(self, lock: Any, ttl_ms: int) -> bool:
        self._ensure_connected()

        thread_id = _lock_thread_id(lock)
        token = _lock_token(lock)
        if thread_id is None or token is None:
            return False

        lock_key = self._key("lock", thread_id)
        result = await self._client.eval(
            _EXTEND_LOCK_SCRIPT,
            1,
            lock_key,
            token,
            str(ttl_ms),
        )
        return bool(result == 1)

    # ------------------------------------------------------------------
    # Generic cache
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        self._ensure_connected()

        value = await self._client.get(self._key("cache", key))
        return _parse_cached_json(value)

    async def set(self, key: str, value: Any, ttl_ms: int | None = None) -> None:
        self._ensure_connected()

        cache_key = self._key("cache", key)
        serialized = json.dumps(value)

        if ttl_ms:
            await self._client.set(cache_key, serialized, px=ttl_ms)
        else:
            await self._client.set(cache_key, serialized)

    async def set_if_not_exists(
        self,
        key: str,
        value: Any,
        ttl_ms: int | None = None,
    ) -> bool:
        self._ensure_connected()

        cache_key = self._key("cache", key)
        serialized = json.dumps(value)

        if ttl_ms:
            result = await self._client.set(cache_key, serialized, nx=True, px=ttl_ms)
        else:
            result = await self._client.set(cache_key, serialized, nx=True)

        return bool(result)

    async def delete(self, key: str) -> None:
        self._ensure_connected()
        await self._client.delete(self._key("cache", key))

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
        max_length = options.get("max_length") or options.get("maxLength") or 0
        ttl_ms = options.get("ttl_ms") or options.get("ttlMs") or 0

        list_key = f"{self._key_prefix}:list:{key}"
        serialized = json.dumps(value)

        await self._client.eval(
            _APPEND_TO_LIST_SCRIPT,
            1,
            list_key,
            serialized,
            str(max_length),
            str(ttl_ms),
        )

    async def get_list(self, key: str) -> list[Any]:
        self._ensure_connected()

        list_key = f"{self._key_prefix}:list:{key}"
        values = await self._client.lrange(list_key, 0, -1)
        return [json.loads(_decode_bytes(v)) for v in values]

    # ------------------------------------------------------------------
    # Queues
    # ------------------------------------------------------------------

    async def enqueue(self, thread_id: str, entry: Any, max_size: int) -> int:
        self._ensure_connected()

        queue_key = self._key("queue", thread_id)
        serialized = json.dumps(entry)

        expires_at_ms = _entry_expires_at_ms(entry)
        # Match upstream: ``Math.max(expiresAt - Date.now(), 60_000)``. A
        # missing ``expires_at`` falls through to the 60s floor.
        ttl_ms = max((expires_at_ms or 0) - _now_ms(), 60_000)

        result = await self._client.eval(
            _ENQUEUE_SCRIPT,
            1,
            queue_key,
            serialized,
            str(max_size),
            str(ttl_ms),
        )
        return int(result)

    async def dequeue(self, thread_id: str) -> Any | None:
        self._ensure_connected()

        queue_key = self._key("queue", thread_id)
        value = await self._client.lpop(queue_key)
        return _entry_from_bytes(value)

    async def queue_depth(self, thread_id: str) -> int:
        self._ensure_connected()

        queue_key = self._key("queue", thread_id)
        return int(await self._client.llen(queue_key))

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_client(self) -> Any:
        """Return the underlying Redis client (for advanced use cases and tests)."""

        return self._client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("RedisStateAdapter is not connected. Call connect() first.")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_redis_state(
    *,
    url: str | None = None,
    client: Any | None = None,
    key_prefix: str | None = None,
    logger: Any | None = None,
) -> RedisStateAdapter:
    """Factory mirroring upstream ``createRedisState()``.

    If ``client`` is provided it wins unconditionally. Otherwise we fall back
    to ``url`` or the ``REDIS_URL`` environment variable. Raises
    :class:`ValueError` if none of those resolve to a Redis URL.
    """

    if client is not None:
        return RedisStateAdapter(client=client, key_prefix=key_prefix, logger=logger)

    resolved = url or os.environ.get("REDIS_URL")
    if not resolved:
        raise ValueError("Redis url is required. Set REDIS_URL or provide url in options.")

    return RedisStateAdapter(url=resolved, key_prefix=key_prefix, logger=logger)
