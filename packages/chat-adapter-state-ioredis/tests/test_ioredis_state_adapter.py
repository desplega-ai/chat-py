"""Tests for IoRedisStateAdapter (thin shim over RedisStateAdapter).

Python port of upstream ``packages/state-ioredis/src/index.test.ts``. Because
the Python implementation delegates to :class:`RedisStateAdapter` (there is
no node-redis-vs-ioredis split in redis-py), the test surface asserts:

1. The class hierarchy is correct (``IoRedisStateAdapter`` is-a
   ``RedisStateAdapter``).
2. The factory produces ``IoRedisStateAdapter`` instances for every calling
   pattern (url, existing client, REDIS_URL fallback, rejection paths).
3. Lock tokens are prefixed ``ioredis_`` — the sole observable difference
   from state-redis, and the one behaviour upstream's test suite pins.
4. All inherited behaviour (subscriptions, locks, cache, lists, queues,
   prefix isolation) works end-to-end with :mod:`fakeredis` — we re-run the
   behavioural matrix so regressions in the base class are caught here too.

Integration tests against a real Redis are gated on ``REDIS_URL``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis as fakeredis_async
import pytest
from chat_adapter_state_ioredis import IoRedisStateAdapter, create_ioredis_state
from chat_adapter_state_redis import RedisStateAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def adapter() -> AsyncIterator[IoRedisStateAdapter]:
    """Provide a connected adapter backed by a fresh FakeRedis instance."""

    client = fakeredis_async.FakeRedis()
    a = IoRedisStateAdapter(client=client)
    await a.connect()
    try:
        yield a
    finally:
        await a.disconnect()
        await client.aclose()


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------


class TestExports:
    def test_exports_create_ioredis_state(self) -> None:
        assert callable(create_ioredis_state)

    def test_exports_ioredis_state_adapter(self) -> None:
        assert callable(IoRedisStateAdapter)

    def test_is_subclass_of_redis_state_adapter(self) -> None:
        # Python unifies node-redis + ioredis onto redis-py; the ioredis
        # package is a thin shim. Guarantee the is-a relationship so callers
        # that type-check against the base class still accept us.
        assert issubclass(IoRedisStateAdapter, RedisStateAdapter)


# ---------------------------------------------------------------------------
# create_ioredis_state factory
# ---------------------------------------------------------------------------


class TestCreateIoRedisState:
    def test_creates_adapter_with_url_option(self) -> None:
        a = create_ioredis_state(url="redis://localhost:6379")
        assert isinstance(a, IoRedisStateAdapter)

    def test_creates_adapter_with_custom_key_prefix(self) -> None:
        a = create_ioredis_state(
            url="redis://localhost:6379",
            key_prefix="custom-prefix",
        )
        assert isinstance(a, IoRedisStateAdapter)

    def test_creates_adapter_with_existing_client(self) -> None:
        client = fakeredis_async.FakeRedis()
        a = create_ioredis_state(client=client)
        assert isinstance(a, IoRedisStateAdapter)
        assert a.get_client() is client

    def test_raises_when_no_url_or_env_var_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDIS_URL", raising=False)
        with pytest.raises(ValueError, match="Redis url is required"):
            create_ioredis_state()

    def test_uses_redis_url_env_var_as_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        a = create_ioredis_state()
        assert isinstance(a, IoRedisStateAdapter)

    def test_rejects_both_url_and_client(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            IoRedisStateAdapter(url="redis://x", client=fakeredis_async.FakeRedis())

    def test_rejects_neither_url_nor_client(self) -> None:
        with pytest.raises(ValueError, match="url= or client="):
            IoRedisStateAdapter()


# ---------------------------------------------------------------------------
# ensure_connected guard
# ---------------------------------------------------------------------------


class TestEnsureConnected:
    async def test_subscribe_raises_before_connect(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.subscribe("thread1")

    async def test_acquire_lock_raises_before_connect(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.acquire_lock("thread1", 5000)

    async def test_get_raises_before_connect(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.get("key")

    async def test_set_raises_before_connect(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.set("key", "value")

    async def test_append_to_list_raises_before_connect(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.append_to_list("key", "value")

    async def test_enqueue_raises_before_connect(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        entry = {"message": "x", "enqueued_at": 0, "expires_at": 0}
        with pytest.raises(RuntimeError, match="not connected"):
            await a.enqueue("thread1", entry, 10)


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnection:
    async def test_connect_is_idempotent(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        await a.connect()
        await a.connect()
        await a.subscribe("thread1")
        assert await a.is_subscribed("thread1") is True

    async def test_disconnect_without_connect_is_noop(self) -> None:
        a = IoRedisStateAdapter(client=fakeredis_async.FakeRedis())
        await a.disconnect()

    async def test_disconnect_does_not_close_injected_client(self) -> None:
        client = fakeredis_async.FakeRedis()
        a = IoRedisStateAdapter(client=client)
        await a.connect()
        await a.disconnect()

        # Client should still work (not aclosed).
        assert await client.ping() is True

    async def test_ping_failure_propagates_from_connect(self) -> None:
        class _FailingClient:
            async def ping(self) -> bool:
                raise ConnectionError("boom")

        a = IoRedisStateAdapter(client=_FailingClient())
        with pytest.raises(ConnectionError, match="boom"):
            await a.connect()


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class TestSubscriptions:
    async def test_subscribe_marks_thread_as_subscribed(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.subscribe("slack:C123:1234.5678")
        assert await adapter.is_subscribed("slack:C123:1234.5678") is True

    async def test_unsubscribe_removes_subscription(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.subscribe("slack:C123:1234.5678")
        await adapter.unsubscribe("slack:C123:1234.5678")
        assert await adapter.is_subscribed("slack:C123:1234.5678") is False

    async def test_is_subscribed_false_for_unknown_thread(
        self, adapter: IoRedisStateAdapter
    ) -> None:
        assert await adapter.is_subscribed("unknown") is False

    async def test_subscribe_is_idempotent(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.subscribe("thread1")
        await adapter.subscribe("thread1")
        assert await adapter.is_subscribed("thread1") is True


# ---------------------------------------------------------------------------
# Locks (including the ioredis_-token parity test)
# ---------------------------------------------------------------------------


class TestLocking:
    async def test_acquires_lock_with_ioredis_token_prefix(
        self, adapter: IoRedisStateAdapter
    ) -> None:
        """Upstream ``state-ioredis`` pins ``ioredis_`` as the token prefix."""

        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
        assert lock["thread_id"] == "thread1"
        assert lock["token"].startswith("ioredis_")
        # Crucially: must not accidentally share the state-redis prefix.
        assert not lock["token"].startswith("redis_")

    async def test_prevents_double_locking(self, adapter: IoRedisStateAdapter) -> None:
        lock1 = await adapter.acquire_lock("thread1", 5000)
        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock1 is not None
        assert lock2 is None

    async def test_releases_lock(self, adapter: IoRedisStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
        await adapter.release_lock(lock)

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None

    async def test_release_with_wrong_token_does_not_release(
        self, adapter: IoRedisStateAdapter
    ) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        await adapter.release_lock({"thread_id": "thread1", "token": "fake", "expires_at": 0})

        assert await adapter.acquire_lock("thread1", 5000) is None

    async def test_extends_a_lock(self, adapter: IoRedisStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 100)
        assert lock is not None

        extended = await adapter.extend_lock(lock, 5000)
        assert extended is True

        assert await adapter.acquire_lock("thread1", 5000) is None

    async def test_extend_with_wrong_token_returns_false(
        self, adapter: IoRedisStateAdapter
    ) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        extended = await adapter.extend_lock(
            {"thread_id": "thread1", "token": "fake", "expires_at": 0},
            5000,
        )
        assert extended is False

    async def test_allows_relocking_after_expiry(self, adapter: IoRedisStateAdapter) -> None:
        lock1 = await adapter.acquire_lock("thread1", 10)
        assert lock1 is not None

        await asyncio.sleep(0.050)

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None
        assert lock2["token"] != lock1["token"]

    async def test_force_release_lock_regardless_of_token(
        self, adapter: IoRedisStateAdapter
    ) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        await adapter.force_release_lock("thread1")

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None
        assert lock2["token"] != lock["token"]

    async def test_force_release_non_existent_is_noop(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.force_release_lock("never-locked")

    async def test_locks_isolated_across_threads(self, adapter: IoRedisStateAdapter) -> None:
        a = await adapter.acquire_lock("thread-a", 5000)
        b = await adapter.acquire_lock("thread-b", 5000)
        assert a is not None and b is not None
        assert a["token"] != b["token"]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class TestCache:
    async def test_set_and_get_roundtrip_primitive(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.set("key1", "hello")
        assert await adapter.get("key1") == "hello"

    async def test_set_and_get_roundtrip_complex(self, adapter: IoRedisStateAdapter) -> None:
        value = {"nested": [1, 2, {"x": True}]}
        await adapter.set("key1", value)
        assert await adapter.get("key1") == value

    async def test_get_missing_key_returns_none(self, adapter: IoRedisStateAdapter) -> None:
        assert await adapter.get("nope") is None

    async def test_delete_removes_value(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.set("key1", "v")
        await adapter.delete("key1")
        assert await adapter.get("key1") is None

    async def test_set_with_ttl_expires(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.set("key1", "v", 10)
        await asyncio.sleep(0.050)
        assert await adapter.get("key1") is None

    async def test_set_without_ttl_persists(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.set("key1", "v")
        await asyncio.sleep(0.020)
        assert await adapter.get("key1") == "v"


class TestSetIfNotExists:
    async def test_sets_when_missing(self, adapter: IoRedisStateAdapter) -> None:
        assert await adapter.set_if_not_exists("k", "first") is True
        assert await adapter.get("k") == "first"

    async def test_does_not_overwrite(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.set_if_not_exists("k", "first")
        assert await adapter.set_if_not_exists("k", "second") is False
        assert await adapter.get("k") == "first"

    async def test_allows_setting_after_ttl_expiry(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.set_if_not_exists("k", "first", 10)
        await asyncio.sleep(0.050)
        assert await adapter.set_if_not_exists("k", "second") is True
        assert await adapter.get("k") == "second"


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


class TestAppendToListGetList:
    async def test_append_and_retrieve(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.append_to_list("L", {"id": 1})
        await adapter.append_to_list("L", {"id": 2})
        assert await adapter.get_list("L") == [{"id": 1}, {"id": 2}]

    async def test_empty_for_missing_key(self, adapter: IoRedisStateAdapter) -> None:
        assert await adapter.get_list("nope") == []

    async def test_trims_to_max_length_keeping_newest(self, adapter: IoRedisStateAdapter) -> None:
        for i in range(1, 6):
            await adapter.append_to_list("L", {"id": i}, {"max_length": 3})
        assert await adapter.get_list("L") == [{"id": 3}, {"id": 4}, {"id": 5}]

    async def test_accepts_camel_case_options(self, adapter: IoRedisStateAdapter) -> None:
        for i in range(1, 5):
            await adapter.append_to_list("L", i, {"maxLength": 2})
        assert await adapter.get_list("L") == [3, 4]


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


def _entry(idx: int, expires_at_offset_ms: int = 60_000) -> dict[str, Any]:
    return {
        "message": {"id": f"m{idx}"},
        "enqueued_at": 0,
        "expires_at": expires_at_offset_ms,
    }


class TestEnqueueDequeueQueueDepth:
    async def test_enqueue_returns_depth(self, adapter: IoRedisStateAdapter) -> None:
        depth = await adapter.enqueue("thread1", _entry(1), 10)
        assert depth == 1

    async def test_dequeue_returns_entry(self, adapter: IoRedisStateAdapter) -> None:
        entry = _entry(1)
        await adapter.enqueue("thread1", entry, 10)

        received = await adapter.dequeue("thread1")
        assert received == entry

    async def test_dequeue_empty_returns_none(self, adapter: IoRedisStateAdapter) -> None:
        assert await adapter.dequeue("thread1") is None

    async def test_fifo_order(self, adapter: IoRedisStateAdapter) -> None:
        await adapter.enqueue("thread1", _entry(1), 10)
        await adapter.enqueue("thread1", _entry(2), 10)
        await adapter.enqueue("thread1", _entry(3), 10)

        assert await adapter.queue_depth("thread1") == 3

        r1 = await adapter.dequeue("thread1")
        r2 = await adapter.dequeue("thread1")
        r3 = await adapter.dequeue("thread1")
        assert r1["message"] == {"id": "m1"}
        assert r2["message"] == {"id": "m2"}
        assert r3["message"] == {"id": "m3"}

    async def test_trims_to_max_size_keeping_newest(self, adapter: IoRedisStateAdapter) -> None:
        for i in range(1, 6):
            await adapter.enqueue("thread1", _entry(i), 3)
        assert await adapter.queue_depth("thread1") == 3

        r1 = await adapter.dequeue("thread1")
        r2 = await adapter.dequeue("thread1")
        r3 = await adapter.dequeue("thread1")
        assert r1["message"] == {"id": "m3"}
        assert r2["message"] == {"id": "m4"}
        assert r3["message"] == {"id": "m5"}


# ---------------------------------------------------------------------------
# Key prefix isolation
# ---------------------------------------------------------------------------


class TestKeyPrefix:
    async def test_distinct_prefixes_isolate_data(self) -> None:
        client = fakeredis_async.FakeRedis()
        a1 = IoRedisStateAdapter(client=client, key_prefix="app1")
        a2 = IoRedisStateAdapter(client=client, key_prefix="app2")

        await a1.connect()
        await a2.connect()

        await a1.subscribe("thread1")
        assert await a2.is_subscribed("thread1") is False

        await a1.set("k", "v1")
        await a2.set("k", "v2")
        assert await a1.get("k") == "v1"
        assert await a2.get("k") == "v2"

        await a1.disconnect()
        await a2.disconnect()
        await client.aclose()


# ---------------------------------------------------------------------------
# Integration tests (opt-in via REDIS_URL)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("REDIS_URL"),
    reason="REDIS_URL not set — skipping integration tests against a real redis.",
)
class TestIntegration:
    async def test_connects_to_real_redis(self) -> None:
        a = create_ioredis_state(url=os.environ["REDIS_URL"])
        await a.connect()
        await a.disconnect()

    async def test_lock_round_trip_on_real_redis(self) -> None:
        a = create_ioredis_state(url=os.environ["REDIS_URL"])
        await a.connect()
        try:
            lock = await a.acquire_lock("ioredis-thread-int-test", 5000)
            assert lock is not None
            assert lock["token"].startswith("ioredis_")

            await a.force_release_lock("ioredis-thread-int-test")

            lock2 = await a.acquire_lock("ioredis-thread-int-test", 5000)
            assert lock2 is not None
            assert lock2["token"] != lock["token"]
        finally:
            await a.force_release_lock("ioredis-thread-int-test")
            await a.disconnect()
