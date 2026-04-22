"""ioredis-flavoured Redis state adapter for chat-py.

**Python thin shim.** In Node.js, upstream ships two separate packages —
``@chat-sdk/state-redis`` (wraps the ``redis`` / node-redis client) and
``@chat-sdk/state-ioredis`` (wraps the legacy ``ioredis`` client). In Python
both collapse onto a single canonical asyncio client: :mod:`redis.asyncio`
(``redis-py`` >=5). There is no Python equivalent of the ioredis-vs-node-redis
split.

To preserve API parity with the upstream workspace — so TypeScript code that
imports ``createIoRedisState`` / ``IoRedisStateAdapter`` has a byte-compatible
Python twin — we ship this package as a thin subclass of
:class:`chat_adapter_state_redis.RedisStateAdapter`. The only observable
difference is the lock-token prefix (``ioredis_`` vs ``redis_``), which
matches upstream byte-for-byte.

See ``packages/chat-adapter-state-redis/src/chat_adapter_state_redis/__init__.py``
for the full implementation — that's where all behaviour lives.

Upstream TS sources (reference):

- ``packages/state-redis/src/index.ts``
- ``packages/state-ioredis/src/index.ts``

Related Linear issue: DES-190.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

from chat_adapter_state_redis import RedisStateAdapter

__version__ = "0.1.0"

__all__ = [
    "IoRedisStateAdapter",
    "__version__",
    "create_ioredis_state",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _generate_ioredis_token() -> str:
    """Mirror upstream ``ioredis_<ts>_<rand>`` token format.

    The only functional difference between state-redis and state-ioredis on
    the wire. ``secrets.token_hex`` instead of ``Math.random`` for crypto
    strength — same choice as state-redis.
    """

    return f"ioredis_{_now_ms()}_{secrets.token_hex(6)}"


class IoRedisStateAdapter(RedisStateAdapter):
    """ioredis-flavoured Redis state adapter.

    Inherits all behaviour from :class:`RedisStateAdapter`. The only override
    is :meth:`acquire_lock`, which emits tokens prefixed ``ioredis_`` instead
    of ``redis_`` — matching upstream ``packages/state-ioredis/src/index.ts``
    byte-for-byte so a single Redis instance can host mixed Python / TS
    clients that inspect the token prefix.

    Python unified node-redis and ioredis onto ``redis-py``'s asyncio client,
    so there is no semantic divergence to preserve. See the module docstring.
    """

    async def acquire_lock(self, thread_id: str, ttl_ms: int) -> dict[str, Any] | None:
        self._ensure_connected()

        token = _generate_ioredis_token()
        lock_key = self._key("lock", thread_id)

        acquired = await self._client.set(lock_key, token, nx=True, px=ttl_ms)
        if not acquired:
            return None

        return {
            "thread_id": thread_id,
            "token": token,
            "expires_at": _now_ms() + ttl_ms,
        }


def create_ioredis_state(
    *,
    url: str | None = None,
    client: Any | None = None,
    key_prefix: str | None = None,
    logger: Any | None = None,
) -> IoRedisStateAdapter:
    """Factory mirroring upstream ``createIoRedisState()``.

    If ``client`` is supplied it wins unconditionally. Otherwise falls back to
    ``url`` or the ``REDIS_URL`` environment variable. Raises
    :class:`ValueError` if none of those resolve.
    """

    if client is not None:
        return IoRedisStateAdapter(client=client, key_prefix=key_prefix, logger=logger)

    resolved = url or os.environ.get("REDIS_URL")
    if not resolved:
        raise ValueError("Redis url is required. Set REDIS_URL or provide url in options.")

    return IoRedisStateAdapter(url=resolved, key_prefix=key_prefix, logger=logger)
