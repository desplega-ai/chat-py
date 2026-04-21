"""PostgreSQL state adapter for chat-py.

Python port of upstream ``packages/state-pg/src/index.ts``. Backs the
``chat`` ``StateAdapter`` protocol with a PostgreSQL database, using
``asyncpg`` as the driver. Suitable for multi-process / production
deployments where in-memory state would be lost or inconsistent.

Schema is identical to upstream (``chat_state_subscriptions``,
``chat_state_locks``, ``chat_state_cache``, ``chat_state_lists``,
``chat_state_queues``) so a single database can serve both Python and
TypeScript SDKs side-by-side.

The adapter accepts either a Postgres ``url`` (we create and own a
pool) or an existing ``pool``/``client`` (we do not tear it down on
``disconnect``). Tests can also inject any pool-like object that
implements ``query(text, *params) -> list[Mapping[str, Any]]`` and
``close()`` — the adapter only uses those two methods.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

__version__ = "0.1.0"

__all__ = [
    "PostgresStateAdapter",
    "__version__",
    "create_postgres_state",
]


if TYPE_CHECKING:  # pragma: no cover
    from chat import Logger


# ---------------------------------------------------------------------------
# Types — mirror the ``chat`` core ``Lock`` / ``QueueEntry`` shapes so this
# module loads cleanly even before chat-core exports the canonical Protocols.
# ---------------------------------------------------------------------------


class _LockDict(TypedDict, total=False):
    thread_id: str
    token: str
    expires_at: int


@runtime_checkable
class _PoolLike(Protocol):
    """Minimal pool interface used by the adapter.

    Matches the subset of ``asyncpg.Pool`` the adapter relies on: a
    ``query(text, *params)`` coroutine returning an iterable of row
    mappings, and a ``close()`` coroutine. The real asyncpg ``Pool`` is
    wrapped by :class:`_AsyncpgPool` so both in-process mocks and the
    production driver share a single call site.
    """

    async def query(
        self, text: str, *params: Any
    ) -> list[dict[str, Any]]: ...  # pragma: no cover - structural

    async def close(self) -> None: ...  # pragma: no cover - structural


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _generate_token() -> str:
    """Generate a pg-flavoured opaque lock token."""

    return f"pg_{uuid.uuid4()}"


def _lock_thread_id(lock: Any) -> str | None:
    if isinstance(lock, dict):
        return lock.get("thread_id") or lock.get("threadId")
    return getattr(lock, "thread_id", None) or getattr(lock, "threadId", None)


def _lock_token(lock: Any) -> str | None:
    if isinstance(lock, dict):
        return lock.get("token")
    return getattr(lock, "token", None)


def _to_ms(value: Any) -> int:
    """Coerce a pg ``timestamptz`` (``datetime``) or int to epoch milliseconds."""

    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    # asyncpg returns timezone-aware ``datetime``; ``timestamp()`` is epoch seconds.
    return int(value.timestamp() * 1000)


def _row_get(row: Any, key: str) -> Any:
    """Read ``key`` from a mapping-like row (dict or ``asyncpg.Record``)."""

    if isinstance(row, dict):
        return row.get(key)
    return row[key]


# ---------------------------------------------------------------------------
# asyncpg pool wrapper
# ---------------------------------------------------------------------------


class _AsyncpgPool:
    """Adapter that makes an ``asyncpg.Pool`` look like :class:`_PoolLike`."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def query(self, text: str, *params: Any) -> list[dict[str, Any]]:
        # asyncpg ``fetch`` returns ``list[Record]``; records behave like mappings
        # but ``dict(record)`` gives us a plain ``dict`` which matches the shape
        # mock pools use in tests.
        records = await self._pool.fetch(text, *params)
        return [dict(r) for r in records]

    async def close(self) -> None:
        await self._pool.close()

    @property
    def raw(self) -> Any:
        return self._pool


def _maybe_create_asyncpg_pool(url: str) -> _PoolLike:
    """Create an asyncpg pool lazily (import only when actually needed).

    Pool creation itself is async in asyncpg; we return a proxy that lazily
    initialises on first ``query``/``close`` to keep the adapter constructor
    synchronous like upstream.
    """

    return _LazyAsyncpgPool(url)


class _LazyAsyncpgPool:
    """Deferred-construction asyncpg pool.

    Upstream ``new pg.Pool({connectionString})`` is synchronous, but
    ``asyncpg.create_pool()`` is a coroutine. We preserve the synchronous
    adapter constructor by creating the underlying pool on the first await.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._pool: Any | None = None

    async def _ensure(self) -> Any:
        if self._pool is None:
            import asyncpg  # local import — optional runtime dep

            self._pool = await asyncpg.create_pool(dsn=self._url)
        return self._pool

    async def query(self, text: str, *params: Any) -> list[dict[str, Any]]:
        pool = await self._ensure()
        records = await pool.fetch(text, *params)
        return [dict(r) for r in records]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def raw(self) -> Any:
        return self._pool


# ---------------------------------------------------------------------------
# Logger fallback
# ---------------------------------------------------------------------------


class _NullLogger:
    """Minimal ``Logger`` implementation used when none is supplied.

    Matches the ``Logger`` Protocol without depending on
    ``chat.ConsoleLogger`` (which would emit to stdout/stderr on error). We
    stay silent by default; callers wanting log output pass a ``logger``
    explicitly.
    """

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
    """Return a ``ConsoleLogger('info').child('postgres')`` when available.

    Falls back to :class:`_NullLogger` if ``chat`` isn't importable yet
    (e.g. during partial parallel porting).
    """

    try:
        from chat import ConsoleLogger  # type: ignore[import-not-found]

        return ConsoleLogger("info").child("postgres")
    except Exception:  # pragma: no cover — optional dep
        return _NullLogger()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PostgresStateAdapter
# ---------------------------------------------------------------------------


class PostgresStateAdapter:
    """PostgreSQL-backed implementation of the ``chat`` ``StateAdapter`` protocol.

    Suitable for multi-process and production deployments. State is
    persisted across restarts and safely shared across concurrent
    processes using pg row-level locks and ``ON CONFLICT`` semantics.
    """

    _pool: _PoolLike
    _key_prefix: str
    _logger: Any
    _owns_client: bool
    _connected: bool

    def __init__(
        self,
        *,
        url: str | None = None,
        client: _PoolLike | Any | None = None,
        pool: _PoolLike | Any | None = None,
        key_prefix: str | None = None,
        logger: Any | None = None,
    ) -> None:
        """Construct a state adapter.

        Exactly one of ``url`` or ``client``/``pool`` must be provided. The
        ``client`` and ``pool`` keywords are aliases for the same concept
        (upstream TS uses ``client``; asyncpg terminology prefers ``pool``).

        Args:
            url: Postgres DSN; the adapter creates and owns an ``asyncpg``
                pool.
            client: Existing pool-like object the adapter will use without
                tearing it down on ``disconnect``.
            pool: Alias for ``client``.
            key_prefix: Row-level prefix for isolating unrelated workloads
                sharing a single database (default: ``"chat-sdk"``).
            logger: A ``chat.Logger`` instance. Defaults to a silent logger
                if the ``chat`` package isn't available.
        """

        existing = client if client is not None else pool

        if existing is not None:
            self._pool = self._coerce_pool(existing)
            self._owns_client = False
        elif url is not None:
            self._pool = _maybe_create_asyncpg_pool(url)
            self._owns_client = True
        else:
            raise ValueError("PostgresStateAdapter requires either url= or client=/pool=.")

        self._key_prefix = key_prefix or "chat-sdk"
        self._logger = logger if logger is not None else _default_logger()
        self._connected = False

    @staticmethod
    def _coerce_pool(candidate: Any) -> _PoolLike:
        """Return a pool-like wrapper for ``candidate``.

        Accepts either a real :class:`_PoolLike` (duck-typed ``query`` +
        ``close``) or an :mod:`asyncpg` pool (duck-typed ``fetch`` +
        ``close``). Tests inject their own mocks with a ``query`` method —
        we pass those through.
        """

        if hasattr(candidate, "query"):
            return candidate
        return _AsyncpgPool(candidate)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return

        try:
            await self._pool.query("SELECT 1")
            await self._ensure_schema()
            self._connected = True
        except Exception as error:
            self._logger.error("Postgres connect failed", {"error": error})
            raise

    async def disconnect(self) -> None:
        if not self._connected:
            return

        if self._owns_client:
            await self._pool.close()

        self._connected = False

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def subscribe(self, thread_id: str) -> None:
        self._ensure_connected()
        await self._pool.query(
            "INSERT INTO chat_state_subscriptions (key_prefix, thread_id)\n"
            "VALUES ($1, $2)\n"
            "ON CONFLICT DO NOTHING",
            self._key_prefix,
            thread_id,
        )

    async def unsubscribe(self, thread_id: str) -> None:
        self._ensure_connected()
        await self._pool.query(
            "DELETE FROM chat_state_subscriptions\nWHERE key_prefix = $1 AND thread_id = $2",
            self._key_prefix,
            thread_id,
        )

    async def is_subscribed(self, thread_id: str) -> bool:
        self._ensure_connected()
        rows = await self._pool.query(
            "SELECT 1 FROM chat_state_subscriptions\n"
            "WHERE key_prefix = $1 AND thread_id = $2\n"
            "LIMIT 1",
            self._key_prefix,
            thread_id,
        )
        return len(rows) > 0

    # ------------------------------------------------------------------
    # Locks
    # ------------------------------------------------------------------

    async def acquire_lock(self, thread_id: str, ttl_ms: int) -> _LockDict | None:
        self._ensure_connected()

        token = _generate_token()

        rows = await self._pool.query(
            "INSERT INTO chat_state_locks (key_prefix, thread_id, token, expires_at)\n"
            "VALUES ($1, $2, $3, now() + $4 * interval '1 millisecond')\n"
            "ON CONFLICT (key_prefix, thread_id) DO UPDATE\n"
            "  SET token = EXCLUDED.token,\n"
            "      expires_at = EXCLUDED.expires_at,\n"
            "      updated_at = now()\n"
            "  WHERE chat_state_locks.expires_at <= now()\n"
            "RETURNING thread_id, token, expires_at",
            self._key_prefix,
            thread_id,
            token,
            ttl_ms,
        )

        if not rows:
            return None

        row = rows[0]
        return {
            "thread_id": _row_get(row, "thread_id"),
            "token": _row_get(row, "token"),
            "expires_at": _to_ms(_row_get(row, "expires_at")),
        }

    async def force_release_lock(self, thread_id: str) -> None:
        self._ensure_connected()
        await self._pool.query(
            "DELETE FROM chat_state_locks\nWHERE key_prefix = $1 AND thread_id = $2",
            self._key_prefix,
            thread_id,
        )

    async def release_lock(self, lock: Any) -> None:
        self._ensure_connected()

        thread_id = _lock_thread_id(lock)
        token = _lock_token(lock)
        if thread_id is None or token is None:
            return

        await self._pool.query(
            "DELETE FROM chat_state_locks\nWHERE key_prefix = $1 AND thread_id = $2 AND token = $3",
            self._key_prefix,
            thread_id,
            token,
        )

    async def extend_lock(self, lock: Any, ttl_ms: int) -> bool:
        self._ensure_connected()

        thread_id = _lock_thread_id(lock)
        token = _lock_token(lock)
        if thread_id is None or token is None:
            return False

        rows = await self._pool.query(
            "UPDATE chat_state_locks\n"
            "SET expires_at = now() + $1 * interval '1 millisecond',\n"
            "    updated_at = now()\n"
            "WHERE key_prefix = $2\n"
            "  AND thread_id = $3\n"
            "  AND token = $4\n"
            "  AND expires_at > now()\n"
            "RETURNING thread_id",
            ttl_ms,
            self._key_prefix,
            thread_id,
            token,
        )
        return len(rows) > 0

    # ------------------------------------------------------------------
    # Generic cache
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        self._ensure_connected()

        rows = await self._pool.query(
            "SELECT value FROM chat_state_cache\n"
            "WHERE key_prefix = $1 AND cache_key = $2\n"
            "  AND (expires_at IS NULL OR expires_at > now())\n"
            "LIMIT 1",
            self._key_prefix,
            key,
        )

        if not rows:
            # Opportunistic cleanup of expired entry.
            await self._pool.query(
                "DELETE FROM chat_state_cache\n"
                "WHERE key_prefix = $1 AND cache_key = $2\n"
                "  AND expires_at <= now()",
                self._key_prefix,
                key,
            )
            return None

        value = _row_get(rows[0], "value")
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    async def set(
        self,
        key: str,
        value: Any,
        ttl_ms: int | None = None,
    ) -> None:
        self._ensure_connected()

        serialized = json.dumps(value)

        await self._pool.query(
            "INSERT INTO chat_state_cache (key_prefix, cache_key, value, expires_at)\n"
            "VALUES ($1, $2, $3,\n"
            "        CASE WHEN $4::bigint IS NULL THEN NULL\n"
            "             ELSE now() + $4::bigint * interval '1 millisecond' END)\n"
            "ON CONFLICT (key_prefix, cache_key) DO UPDATE\n"
            "  SET value = EXCLUDED.value,\n"
            "      expires_at = EXCLUDED.expires_at,\n"
            "      updated_at = now()",
            self._key_prefix,
            key,
            serialized,
            ttl_ms,
        )

    async def set_if_not_exists(
        self,
        key: str,
        value: Any,
        ttl_ms: int | None = None,
    ) -> bool:
        self._ensure_connected()

        serialized = json.dumps(value)

        rows = await self._pool.query(
            "INSERT INTO chat_state_cache (key_prefix, cache_key, value, expires_at)\n"
            "VALUES ($1, $2, $3,\n"
            "        CASE WHEN $4::bigint IS NULL THEN NULL\n"
            "             ELSE now() + $4::bigint * interval '1 millisecond' END)\n"
            "ON CONFLICT (key_prefix, cache_key) DO NOTHING\n"
            "RETURNING cache_key",
            self._key_prefix,
            key,
            serialized,
            ttl_ms,
        )
        return len(rows) > 0

    async def delete(self, key: str) -> None:
        self._ensure_connected()
        await self._pool.query(
            "DELETE FROM chat_state_cache\nWHERE key_prefix = $1 AND cache_key = $2",
            self._key_prefix,
            key,
        )

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

        serialized = json.dumps(value)

        # Insert the new entry.
        await self._pool.query(
            "INSERT INTO chat_state_lists (key_prefix, list_key, value, expires_at)\n"
            "VALUES ($1, $2, $3,\n"
            "        CASE WHEN $4::bigint IS NULL THEN NULL\n"
            "             ELSE now() + $4::bigint * interval '1 millisecond' END)",
            self._key_prefix,
            key,
            serialized,
            ttl_ms,
        )

        # Trim overflow if max_length is specified.
        if max_length:
            await self._pool.query(
                "DELETE FROM chat_state_lists\n"
                "WHERE key_prefix = $1 AND list_key = $2 AND seq IN (\n"
                "  SELECT seq FROM chat_state_lists\n"
                "  WHERE key_prefix = $1 AND list_key = $2\n"
                "  ORDER BY seq ASC\n"
                "  OFFSET 0\n"
                "  LIMIT GREATEST(\n"
                "    (SELECT count(*) FROM chat_state_lists WHERE key_prefix = $1 AND list_key = $2) - $3,\n"
                "    0\n"
                "  )\n"
                ")",
                self._key_prefix,
                key,
                max_length,
            )

        # Update TTL on all entries for this key.
        if ttl_ms:
            await self._pool.query(
                "UPDATE chat_state_lists\n"
                "SET expires_at = now() + $3 * interval '1 millisecond'\n"
                "WHERE key_prefix = $1 AND list_key = $2",
                self._key_prefix,
                key,
                ttl_ms,
            )

    async def get_list(self, key: str) -> list[Any]:
        self._ensure_connected()

        rows = await self._pool.query(
            "SELECT value FROM chat_state_lists\n"
            "WHERE key_prefix = $1 AND list_key = $2\n"
            "  AND (expires_at IS NULL OR expires_at > now())\n"
            "ORDER BY seq ASC",
            self._key_prefix,
            key,
        )

        return [json.loads(_row_get(r, "value")) for r in rows]

    # ------------------------------------------------------------------
    # Queues
    # ------------------------------------------------------------------

    async def enqueue(self, thread_id: str, entry: Any, max_size: int) -> int:
        self._ensure_connected()

        serialized = json.dumps(entry)
        expires_at_ms = (
            entry.get("expires_at") or entry.get("expiresAt")
            if isinstance(entry, dict)
            else getattr(entry, "expires_at", None) or getattr(entry, "expiresAt", None)
        )

        # Purge expired entries first to avoid phantom queue pressure.
        await self._pool.query(
            "DELETE FROM chat_state_queues\n"
            "WHERE key_prefix = $1 AND thread_id = $2 AND expires_at <= now()",
            self._key_prefix,
            thread_id,
        )

        # Insert the new entry. Parameterise expires_at as a bigint epoch-ms so
        # we don't require callers to pre-convert to datetime.
        await self._pool.query(
            "INSERT INTO chat_state_queues (key_prefix, thread_id, value, expires_at)\n"
            "VALUES ($1, $2, $3, to_timestamp($4::bigint / 1000.0))",
            self._key_prefix,
            thread_id,
            serialized,
            int(expires_at_ms) if expires_at_ms is not None else 0,
        )

        # Trim overflow (keep newest max_size non-expired entries).
        if max_size > 0:
            await self._pool.query(
                "DELETE FROM chat_state_queues\n"
                "WHERE key_prefix = $1 AND thread_id = $2 AND seq IN (\n"
                "  SELECT seq FROM chat_state_queues\n"
                "  WHERE key_prefix = $1 AND thread_id = $2\n"
                "    AND expires_at > now()\n"
                "  ORDER BY seq ASC\n"
                "  OFFSET 0\n"
                "  LIMIT GREATEST(\n"
                "    (SELECT count(*) FROM chat_state_queues\n"
                "     WHERE key_prefix = $1 AND thread_id = $2 AND expires_at > now()) - $3,\n"
                "    0\n"
                "  )\n"
                ")",
                self._key_prefix,
                thread_id,
                max_size,
            )

        # Return current non-expired depth.
        rows = await self._pool.query(
            "SELECT count(*) as depth FROM chat_state_queues\n"
            "WHERE key_prefix = $1 AND thread_id = $2 AND expires_at > now()",
            self._key_prefix,
            thread_id,
        )
        depth = _row_get(rows[0], "depth") if rows else 0
        return int(depth)

    async def dequeue(self, thread_id: str) -> Any | None:
        self._ensure_connected()

        # Purge expired entries first.
        await self._pool.query(
            "DELETE FROM chat_state_queues\n"
            "WHERE key_prefix = $1 AND thread_id = $2 AND expires_at <= now()",
            self._key_prefix,
            thread_id,
        )

        # Atomically select + delete the oldest non-expired entry.
        rows = await self._pool.query(
            "DELETE FROM chat_state_queues\n"
            "WHERE key_prefix = $1 AND thread_id = $2\n"
            "  AND seq = (\n"
            "    SELECT seq FROM chat_state_queues\n"
            "    WHERE key_prefix = $1 AND thread_id = $2\n"
            "      AND expires_at > now()\n"
            "    ORDER BY seq ASC\n"
            "    LIMIT 1\n"
            "  )\n"
            "RETURNING value",
            self._key_prefix,
            thread_id,
        )

        if not rows:
            return None

        return json.loads(_row_get(rows[0], "value"))

    async def queue_depth(self, thread_id: str) -> int:
        self._ensure_connected()

        rows = await self._pool.query(
            "SELECT count(*) as depth FROM chat_state_queues\n"
            "WHERE key_prefix = $1 AND thread_id = $2 AND expires_at > now()",
            self._key_prefix,
            thread_id,
        )
        depth = _row_get(rows[0], "depth") if rows else 0
        return int(depth)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_client(self) -> _PoolLike:
        """Return the underlying pool (for advanced use cases and tests)."""

        return self._pool

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("PostgresStateAdapter is not connected. Call connect() first.")

    async def _ensure_schema(self) -> None:
        """Create the full schema if it doesn't already exist.

        Statement shapes intentionally match upstream to preserve binary
        compatibility for cross-language workloads sharing a database.
        """

        await self._pool.query(
            "CREATE TABLE IF NOT EXISTS chat_state_subscriptions (\n"
            "  key_prefix text NOT NULL,\n"
            "  thread_id text NOT NULL,\n"
            "  created_at timestamptz NOT NULL DEFAULT now(),\n"
            "  PRIMARY KEY (key_prefix, thread_id)\n"
            ")"
        )
        await self._pool.query(
            "CREATE TABLE IF NOT EXISTS chat_state_locks (\n"
            "  key_prefix text NOT NULL,\n"
            "  thread_id text NOT NULL,\n"
            "  token text NOT NULL,\n"
            "  expires_at timestamptz NOT NULL,\n"
            "  updated_at timestamptz NOT NULL DEFAULT now(),\n"
            "  PRIMARY KEY (key_prefix, thread_id)\n"
            ")"
        )
        await self._pool.query(
            "CREATE TABLE IF NOT EXISTS chat_state_cache (\n"
            "  key_prefix text NOT NULL,\n"
            "  cache_key text NOT NULL,\n"
            "  value text NOT NULL,\n"
            "  expires_at timestamptz,\n"
            "  updated_at timestamptz NOT NULL DEFAULT now(),\n"
            "  PRIMARY KEY (key_prefix, cache_key)\n"
            ")"
        )
        await self._pool.query(
            "CREATE INDEX IF NOT EXISTS chat_state_locks_expires_idx\n"
            "ON chat_state_locks (expires_at)"
        )
        await self._pool.query(
            "CREATE INDEX IF NOT EXISTS chat_state_cache_expires_idx\n"
            "ON chat_state_cache (expires_at)"
        )
        await self._pool.query(
            "CREATE TABLE IF NOT EXISTS chat_state_lists (\n"
            "  key_prefix text NOT NULL,\n"
            "  list_key text NOT NULL,\n"
            "  seq bigserial NOT NULL,\n"
            "  value text NOT NULL,\n"
            "  expires_at timestamptz,\n"
            "  PRIMARY KEY (key_prefix, list_key, seq)\n"
            ")"
        )
        await self._pool.query(
            "CREATE INDEX IF NOT EXISTS chat_state_lists_expires_idx\n"
            "ON chat_state_lists (expires_at)"
        )
        await self._pool.query(
            "CREATE TABLE IF NOT EXISTS chat_state_queues (\n"
            "  key_prefix text NOT NULL,\n"
            "  thread_id text NOT NULL,\n"
            "  seq bigserial NOT NULL,\n"
            "  value text NOT NULL,\n"
            "  expires_at timestamptz NOT NULL,\n"
            "  PRIMARY KEY (key_prefix, thread_id, seq)\n"
            ")"
        )
        await self._pool.query(
            "CREATE INDEX IF NOT EXISTS chat_state_queues_expires_idx\n"
            "ON chat_state_queues (expires_at)"
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_postgres_state(
    *,
    url: str | None = None,
    client: Any | None = None,
    pool: Any | None = None,
    key_prefix: str | None = None,
    logger: Any | None = None,
) -> PostgresStateAdapter:
    """Factory mirroring upstream ``createPostgresState()``.

    If no ``url``/``client``/``pool`` is provided, falls back to the
    ``POSTGRES_URL`` or ``DATABASE_URL`` environment variable. Raises
    :class:`ValueError` if none of those resolve to a Postgres DSN.
    """

    existing = client if client is not None else pool
    if existing is not None:
        return PostgresStateAdapter(
            client=existing,
            key_prefix=key_prefix,
            logger=logger,
        )

    resolved = url or os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not resolved:
        raise ValueError(
            "Postgres url is required. Set POSTGRES_URL or DATABASE_URL, or provide it in options."
        )

    return PostgresStateAdapter(
        url=resolved,
        key_prefix=key_prefix,
        logger=logger,
    )
