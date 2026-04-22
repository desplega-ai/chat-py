"""Tests for RedisStateAdapter (mocked redis client via fakeredis).

Python port of upstream ``packages/state-redis/src/index.test.ts`` plus
the broader state-adapter suite ported from state-memory / state-pg.

Uses :mod:`fakeredis` (with ``[lua]`` extra) as an in-process redis
implementation so the adapter exercises its real SET/NX/PX, SADD/SREM,
LPUSH/LRANGE, and Lua EVAL paths without needing a live redis server.
End-to-end tests against a real instance are gated on ``REDIS_URL``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis as fakeredis_async
import pytest
from chat_adapter_state_redis import RedisStateAdapter, create_redis_state

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def adapter() -> AsyncIterator[RedisStateAdapter]:
    """Provide a connected adapter backed by a fresh FakeRedis instance."""

    client = fakeredis_async.FakeRedis()
    a = RedisStateAdapter(client=client)
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
    def test_exports_create_redis_state(self) -> None:
        assert callable(create_redis_state)

    def test_exports_redis_state_adapter(self) -> None:
        assert callable(RedisStateAdapter)


# ---------------------------------------------------------------------------
# create_redis_state factory
# ---------------------------------------------------------------------------


class TestCreateRedisState:
    def test_creates_adapter_with_url_option(self) -> None:
        a = create_redis_state(url="redis://localhost:6379")
        assert isinstance(a, RedisStateAdapter)

    def test_creates_adapter_with_custom_key_prefix(self) -> None:
        a = create_redis_state(
            url="redis://localhost:6379",
            key_prefix="custom-prefix",
        )
        assert isinstance(a, RedisStateAdapter)

    def test_creates_adapter_with_existing_client(self) -> None:
        client = fakeredis_async.FakeRedis()
        a = create_redis_state(client=client)
        assert isinstance(a, RedisStateAdapter)
        assert a.get_client() is client

    def test_raises_when_no_url_or_env_var_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDIS_URL", raising=False)
        with pytest.raises(ValueError, match="Redis url is required"):
            create_redis_state()

    def test_uses_redis_url_env_var_as_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        a = create_redis_state()
        assert isinstance(a, RedisStateAdapter)

    def test_rejects_both_url_and_client(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            RedisStateAdapter(url="redis://x", client=fakeredis_async.FakeRedis())

    def test_rejects_neither_url_nor_client(self) -> None:
        with pytest.raises(ValueError, match="url= or client="):
            RedisStateAdapter()


# ---------------------------------------------------------------------------
# ensure_connected guard
# ---------------------------------------------------------------------------


class TestEnsureConnected:
    async def test_subscribe_raises_before_connect(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.subscribe("thread1")

    async def test_acquire_lock_raises_before_connect(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.acquire_lock("thread1", 5000)

    async def test_get_raises_before_connect(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.get("key")

    async def test_set_raises_before_connect(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.set("key", "value")

    async def test_append_to_list_raises_before_connect(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        with pytest.raises(RuntimeError, match="not connected"):
            await a.append_to_list("key", "value")

    async def test_enqueue_raises_before_connect(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        entry = {"message": "x", "enqueued_at": 0, "expires_at": 0}
        with pytest.raises(RuntimeError, match="not connected"):
            await a.enqueue("thread1", entry, 10)


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnection:
    async def test_connect_is_idempotent(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        await a.connect()
        await a.connect()
        await a.subscribe("thread1")
        assert await a.is_subscribed("thread1") is True

    async def test_disconnect_without_connect_is_noop(self) -> None:
        a = RedisStateAdapter(client=fakeredis_async.FakeRedis())
        # Must not raise.
        await a.disconnect()

    async def test_disconnect_does_not_close_injected_client(self) -> None:
        client = fakeredis_async.FakeRedis()
        a = RedisStateAdapter(client=client)
        await a.connect()
        await a.disconnect()

        # Client should still work (not aclosed).
        assert await client.ping() is True

    async def test_ping_failure_propagates_from_connect(self) -> None:
        class _FailingClient:
            async def ping(self) -> bool:
                raise ConnectionError("boom")

        a = RedisStateAdapter(client=_FailingClient())
        with pytest.raises(ConnectionError, match="boom"):
            await a.connect()


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class TestSubscriptions:
    async def test_subscribe_marks_thread_as_subscribed(self, adapter: RedisStateAdapter) -> None:
        await adapter.subscribe("slack:C123:1234.5678")
        assert await adapter.is_subscribed("slack:C123:1234.5678") is True

    async def test_unsubscribe_removes_subscription(self, adapter: RedisStateAdapter) -> None:
        await adapter.subscribe("slack:C123:1234.5678")
        await adapter.unsubscribe("slack:C123:1234.5678")
        assert await adapter.is_subscribed("slack:C123:1234.5678") is False

    async def test_is_subscribed_false_for_unknown_thread(self, adapter: RedisStateAdapter) -> None:
        assert await adapter.is_subscribed("unknown") is False

    async def test_subscribe_is_idempotent(self, adapter: RedisStateAdapter) -> None:
        await adapter.subscribe("thread1")
        await adapter.subscribe("thread1")
        assert await adapter.is_subscribed("thread1") is True


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


class TestLocking:
    async def test_acquires_lock(self, adapter: RedisStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
        assert lock["thread_id"] == "thread1"
        assert lock["token"].startswith("redis_")

    async def test_prevents_double_locking(self, adapter: RedisStateAdapter) -> None:
        lock1 = await adapter.acquire_lock("thread1", 5000)
        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock1 is not None
        assert lock2 is None

    async def test_releases_lock(self, adapter: RedisStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
        await adapter.release_lock(lock)

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None

    async def test_release_with_wrong_token_does_not_release(
        self, adapter: RedisStateAdapter
    ) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        await adapter.release_lock({"thread_id": "thread1", "token": "fake", "expires_at": 0})

        # Real lock still held.
        assert await adapter.acquire_lock("thread1", 5000) is None

    async def test_extends_a_lock(self, adapter: RedisStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 100)
        assert lock is not None

        extended = await adapter.extend_lock(lock, 5000)
        assert extended is True

        # Still locked right after — can't re-acquire.
        assert await adapter.acquire_lock("thread1", 5000) is None

    async def test_extend_with_wrong_token_returns_false(self, adapter: RedisStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        extended = await adapter.extend_lock(
            {"thread_id": "thread1", "token": "fake", "expires_at": 0},
            5000,
        )
        assert extended is False

    async def test_allows_relocking_after_expiry(self, adapter: RedisStateAdapter) -> None:
        lock1 = await adapter.acquire_lock("thread1", 10)
        assert lock1 is not None

        await asyncio.sleep(0.050)

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None
        assert lock2["token"] != lock1["token"]

    async def test_force_release_lock_regardless_of_token(self, adapter: RedisStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        await adapter.force_release_lock("thread1")

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None
        assert lock2["token"] != lock["token"]

    async def test_force_release_non_existent_is_noop(self, adapter: RedisStateAdapter) -> None:
        # Must not raise — redis DEL on missing key returns 0.
        await adapter.force_release_lock("never-locked")

    async def test_locks_isolated_across_threads(self, adapter: RedisStateAdapter) -> None:
        a = await adapter.acquire_lock("thread-a", 5000)
        b = await adapter.acquire_lock("thread-b", 5000)
        assert a is not None and b is not None
        assert a["token"] != b["token"]


# ---------------------------------------------------------------------------
# Cache — get/set/delete/set_if_not_exists
# ---------------------------------------------------------------------------


class TestCache:
    async def test_set_and_get_roundtrip_primitive(self, adapter: RedisStateAdapter) -> None:
        await adapter.set("key1", "hello")
        assert await adapter.get("key1") == "hello"

    async def test_set_and_get_roundtrip_complex(self, adapter: RedisStateAdapter) -> None:
        value = {"nested": [1, 2, {"x": True}]}
        await adapter.set("key1", value)
        assert await adapter.get("key1") == value

    async def test_get_missing_key_returns_none(self, adapter: RedisStateAdapter) -> None:
        assert await adapter.get("nope") is None

    async def test_delete_removes_value(self, adapter: RedisStateAdapter) -> None:
        await adapter.set("key1", "v")
        await adapter.delete("key1")
        assert await adapter.get("key1") is None

    async def test_set_with_ttl_expires(self, adapter: RedisStateAdapter) -> None:
        await adapter.set("key1", "v", 10)
        await asyncio.sleep(0.050)
        assert await adapter.get("key1") is None

    async def test_set_without_ttl_persists(self, adapter: RedisStateAdapter) -> None:
        await adapter.set("key1", "v")
        await asyncio.sleep(0.020)
        assert await adapter.get("key1") == "v"


class TestSetIfNotExists:
    async def test_sets_when_missing(self, adapter: RedisStateAdapter) -> None:
        assert await adapter.set_if_not_exists("k", "first") is True
        assert await adapter.get("k") == "first"

    async def test_does_not_overwrite(self, adapter: RedisStateAdapter) -> None:
        await adapter.set_if_not_exists("k", "first")
        assert await adapter.set_if_not_exists("k", "second") is False
        assert await adapter.get("k") == "first"

    async def test_allows_setting_after_ttl_expiry(self, adapter: RedisStateAdapter) -> None:
        await adapter.set_if_not_exists("k", "first", 10)
        await asyncio.sleep(0.050)
        assert await adapter.set_if_not_exists("k", "second") is True
        assert await adapter.get("k") == "second"

    async def test_respects_ttl_on_new_value(self, adapter: RedisStateAdapter) -> None:
        await adapter.set_if_not_exists("k", "v", 10)
        await asyncio.sleep(0.050)
        assert await adapter.get("k") is None


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


class TestAppendToListGetList:
    async def test_append_and_retrieve(self, adapter: RedisStateAdapter) -> None:
        await adapter.append_to_list("L", {"id": 1})
        await adapter.append_to_list("L", {"id": 2})
        assert await adapter.get_list("L") == [{"id": 1}, {"id": 2}]

    async def test_empty_for_missing_key(self, adapter: RedisStateAdapter) -> None:
        assert await adapter.get_list("nope") == []

    async def test_trims_to_max_length_keeping_newest(self, adapter: RedisStateAdapter) -> None:
        for i in range(1, 6):
            await adapter.append_to_list("L", {"id": i}, {"max_length": 3})
        assert await adapter.get_list("L") == [
            {"id": 3},
            {"id": 4},
            {"id": 5},
        ]

    async def test_ttl_option(self, adapter: RedisStateAdapter) -> None:
        await adapter.append_to_list("L", {"id": 1}, {"ttl_ms": 50})
        await asyncio.sleep(0.120)
        assert await adapter.get_list("L") == []

    async def test_lists_isolated_by_key(self, adapter: RedisStateAdapter) -> None:
        await adapter.append_to_list("a", 1)
        await adapter.append_to_list("b", 2)
        assert await adapter.get_list("a") == [1]
        assert await adapter.get_list("b") == [2]

    async def test_accepts_camel_case_options(self, adapter: RedisStateAdapter) -> None:
        # Options from upstream callers may arrive in camelCase.
        for i in range(1, 5):
            await adapter.append_to_list("L", i, {"maxLength": 2})
        assert await adapter.get_list("L") == [3, 4]


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


def _entry(
    idx: int,
    expires_at_offset_ms: int = 60_000,
) -> dict[str, Any]:
    now = 0
    return {
        "message": {"id": f"m{idx}"},
        "enqueued_at": now,
        "expires_at": now + expires_at_offset_ms,
    }


class TestEnqueueDequeueQueueDepth:
    async def test_enqueue_returns_depth(self, adapter: RedisStateAdapter) -> None:
        depth = await adapter.enqueue("thread1", _entry(1), 10)
        assert depth == 1

    async def test_dequeue_returns_entry(self, adapter: RedisStateAdapter) -> None:
        entry = _entry(1)
        await adapter.enqueue("thread1", entry, 10)

        received = await adapter.dequeue("thread1")
        assert received == entry

    async def test_dequeue_empty_returns_none(self, adapter: RedisStateAdapter) -> None:
        assert await adapter.dequeue("thread1") is None

    async def test_queue_depth_zero_when_empty(self, adapter: RedisStateAdapter) -> None:
        assert await adapter.queue_depth("thread1") == 0

    async def test_fifo_order(self, adapter: RedisStateAdapter) -> None:
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

        assert await adapter.queue_depth("thread1") == 0

    async def test_trims_to_max_size_keeping_newest(self, adapter: RedisStateAdapter) -> None:
        for i in range(1, 6):
            await adapter.enqueue("thread1", _entry(i), 3)
        assert await adapter.queue_depth("thread1") == 3

        r1 = await adapter.dequeue("thread1")
        r2 = await adapter.dequeue("thread1")
        r3 = await adapter.dequeue("thread1")
        assert r1["message"] == {"id": "m3"}
        assert r2["message"] == {"id": "m4"}
        assert r3["message"] == {"id": "m5"}

    async def test_max_size_one_debounce_behaviour(self, adapter: RedisStateAdapter) -> None:
        await adapter.enqueue("thread1", _entry(1), 1)
        await adapter.enqueue("thread1", _entry(2), 1)
        await adapter.enqueue("thread1", _entry(3), 1)

        assert await adapter.queue_depth("thread1") == 1
        result = await adapter.dequeue("thread1")
        assert result["message"] == {"id": "m3"}

    async def test_queues_isolated_by_thread(self, adapter: RedisStateAdapter) -> None:
        await adapter.enqueue("a", _entry(1), 10)
        await adapter.enqueue("b", _entry(2), 10)

        assert await adapter.queue_depth("a") == 1
        assert await adapter.queue_depth("b") == 1

        ra = await adapter.dequeue("a")
        rb = await adapter.dequeue("b")
        assert ra["message"] == {"id": "m1"}
        assert rb["message"] == {"id": "m2"}


# ---------------------------------------------------------------------------
# Key prefix isolation
# ---------------------------------------------------------------------------


class TestKeyPrefix:
    async def test_distinct_prefixes_isolate_data(self) -> None:
        client = fakeredis_async.FakeRedis()
        a1 = RedisStateAdapter(client=client, key_prefix="app1")
        a2 = RedisStateAdapter(client=client, key_prefix="app2")

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
        a = create_redis_state(url=os.environ["REDIS_URL"])
        await a.connect()
        await a.disconnect()

    async def test_force_release_a_lock_regardless_of_token(self) -> None:
        a = create_redis_state(url=os.environ["REDIS_URL"])
        await a.connect()
        try:
            lock = await a.acquire_lock("thread-force-test", 5000)
            assert lock is not None

            await a.force_release_lock("thread-force-test")

            lock2 = await a.acquire_lock("thread-force-test", 5000)
            assert lock2 is not None
            assert lock2["token"] != lock["token"]
        finally:
            await a.force_release_lock("thread-force-test")
            await a.disconnect()

    async def test_no_op_when_force_releasing_non_existent_lock(self) -> None:
        a = create_redis_state(url=os.environ["REDIS_URL"])
        await a.connect()
        try:
            # Must not raise.
            await a.force_release_lock("nonexistent-lock-never-set")
        finally:
            await a.disconnect()
