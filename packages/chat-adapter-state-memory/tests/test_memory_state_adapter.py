"""Tests for MemoryStateAdapter.

Python port of upstream ``packages/state-memory/src/index.test.ts``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from chat_adapter_state_memory import MemoryStateAdapter, create_memory_state

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def adapter() -> AsyncIterator[MemoryStateAdapter]:
    a = create_memory_state()
    await a.connect()
    yield a
    await a.disconnect()


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class TestSubscriptions:
    async def test_should_subscribe_to_a_thread(self, adapter: MemoryStateAdapter) -> None:
        await adapter.subscribe("slack:C123:1234.5678")
        assert await adapter.is_subscribed("slack:C123:1234.5678") is True

    async def test_should_unsubscribe_from_a_thread(self, adapter: MemoryStateAdapter) -> None:
        await adapter.subscribe("slack:C123:1234.5678")
        await adapter.unsubscribe("slack:C123:1234.5678")
        assert await adapter.is_subscribed("slack:C123:1234.5678") is False


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


class TestLocking:
    async def test_should_acquire_a_lock(self, adapter: MemoryStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
        assert lock["thread_id"] == "thread1"
        assert lock["token"]

    async def test_should_prevent_double_locking(self, adapter: MemoryStateAdapter) -> None:
        lock1 = await adapter.acquire_lock("thread1", 5000)
        lock2 = await adapter.acquire_lock("thread1", 5000)

        assert lock1 is not None
        assert lock2 is None

    async def test_should_release_a_lock(self, adapter: MemoryStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
        await adapter.release_lock(lock)

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None

    async def test_should_not_release_a_lock_with_wrong_token(
        self, adapter: MemoryStateAdapter
    ) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        await adapter.release_lock(
            {
                "thread_id": "thread1",
                "token": "fake-token",
                "expires_at": 0,
            }
        )

        # Original lock should still be held
        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is None

        # Clean up
        await adapter.release_lock(lock)

    async def test_should_allow_re_locking_after_expiry(self, adapter: MemoryStateAdapter) -> None:
        lock1 = await adapter.acquire_lock("thread1", 10)  # 10ms TTL

        await asyncio.sleep(0.020)  # 20ms

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None
        assert lock1 is not None
        assert lock2["token"] != lock1["token"]

    async def test_should_extend_a_lock(self, adapter: MemoryStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 100)
        assert lock is not None

        extended = await adapter.extend_lock(lock, 5000)
        assert extended is True

        # Should still be locked
        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is None

    async def test_should_force_release_a_lock_regardless_of_token(
        self, adapter: MemoryStateAdapter
    ) -> None:
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None

        await adapter.force_release_lock("thread1")

        lock2 = await adapter.acquire_lock("thread1", 5000)
        assert lock2 is not None
        assert lock2["token"] != lock["token"]

    async def test_should_no_op_when_force_releasing_a_non_existent_lock(
        self, adapter: MemoryStateAdapter
    ) -> None:
        result = await adapter.force_release_lock("nonexistent")
        assert result is None

    async def test_should_not_extend_an_expired_lock(self, adapter: MemoryStateAdapter) -> None:
        lock = await adapter.acquire_lock("thread1", 10)
        assert lock is not None

        await asyncio.sleep(0.020)

        extended = await adapter.extend_lock(lock, 5000)
        assert extended is False


# ---------------------------------------------------------------------------
# set_if_not_exists
# ---------------------------------------------------------------------------


class TestSetIfNotExists:
    async def test_should_set_a_value_when_key_does_not_exist(
        self, adapter: MemoryStateAdapter
    ) -> None:
        result = await adapter.set_if_not_exists("key1", "value1")
        assert result is True
        assert await adapter.get("key1") == "value1"

    async def test_should_not_overwrite_an_existing_key(self, adapter: MemoryStateAdapter) -> None:
        await adapter.set_if_not_exists("key1", "first")
        result = await adapter.set_if_not_exists("key1", "second")
        assert result is False
        assert await adapter.get("key1") == "first"

    async def test_should_allow_setting_after_ttl_expiry(self, adapter: MemoryStateAdapter) -> None:
        await adapter.set_if_not_exists("key1", "first", 10)
        await asyncio.sleep(0.020)
        result = await adapter.set_if_not_exists("key1", "second")
        assert result is True
        assert await adapter.get("key1") == "second"

    async def test_should_respect_ttl_on_the_new_value(self, adapter: MemoryStateAdapter) -> None:
        await adapter.set_if_not_exists("key1", "value", 10)
        await asyncio.sleep(0.020)
        assert await adapter.get("key1") is None


# ---------------------------------------------------------------------------
# append_to_list / get_list
# ---------------------------------------------------------------------------


class TestAppendToListGetList:
    async def test_should_append_and_retrieve_list_items(self, adapter: MemoryStateAdapter) -> None:
        await adapter.append_to_list("list1", {"id": 1})
        await adapter.append_to_list("list1", {"id": 2})

        result = await adapter.get_list("list1")
        assert result == [{"id": 1}, {"id": 2}]

    async def test_should_return_empty_array_for_non_existent_list(
        self, adapter: MemoryStateAdapter
    ) -> None:
        result = await adapter.get_list("nonexistent")
        assert result == []

    async def test_should_trim_to_max_length_keeping_newest(
        self, adapter: MemoryStateAdapter
    ) -> None:
        for i in range(1, 6):
            await adapter.append_to_list("list1", {"id": i}, {"max_length": 3})

        result = await adapter.get_list("list1")
        assert result == [{"id": 3}, {"id": 4}, {"id": 5}]

    async def test_should_respect_ttl_on_lists(self, adapter: MemoryStateAdapter) -> None:
        await adapter.append_to_list("list1", {"id": 1}, {"ttl_ms": 10})
        await asyncio.sleep(0.020)

        result = await adapter.get_list("list1")
        assert result == []

    async def test_should_refresh_ttl_on_subsequent_appends(
        self, adapter: MemoryStateAdapter
    ) -> None:
        # Each append refreshes TTL — second append within first append's TTL
        # should preserve both entries.
        await adapter.append_to_list("list1", {"id": 1}, {"ttl_ms": 100})
        await asyncio.sleep(0.030)

        # Append again — refreshes TTL to 100ms from now
        await adapter.append_to_list("list1", {"id": 2}, {"ttl_ms": 100})

        result = await adapter.get_list("list1")
        assert result == [{"id": 1}, {"id": 2}]

    async def test_should_keep_lists_isolated_by_key(self, adapter: MemoryStateAdapter) -> None:
        await adapter.append_to_list("list-a", "a")
        await adapter.append_to_list("list-b", "b")

        assert await adapter.get_list("list-a") == ["a"]
        assert await adapter.get_list("list-b") == ["b"]

    async def test_should_start_fresh_after_expired_list(self, adapter: MemoryStateAdapter) -> None:
        await adapter.append_to_list("list1", {"id": 1}, {"ttl_ms": 10})
        await asyncio.sleep(0.020)

        await adapter.append_to_list("list1", {"id": 2})
        result = await adapter.get_list("list1")
        assert result == [{"id": 2}]


# ---------------------------------------------------------------------------
# enqueue / dequeue / queue_depth
# ---------------------------------------------------------------------------


class TestEnqueueDequeueQueueDepth:
    async def test_should_enqueue_and_dequeue_a_single_entry(
        self, adapter: MemoryStateAdapter
    ) -> None:
        entry = {
            "message": {"id": "m1", "text": "hello"},
            "enqueued_at": 1000,
            "expires_at": 90000,
        }
        depth = await adapter.enqueue("thread1", entry, 10)
        assert depth == 1

        result = await adapter.dequeue("thread1")
        assert result == entry

    async def test_should_return_none_when_dequeuing_from_empty_queue(
        self, adapter: MemoryStateAdapter
    ) -> None:
        result = await adapter.dequeue("thread1")
        assert result is None

    async def test_should_return_none_when_dequeuing_from_nonexistent_thread(
        self, adapter: MemoryStateAdapter
    ) -> None:
        result = await adapter.dequeue("nonexistent")
        assert result is None

    async def test_should_return_zero_depth_for_empty_queue(
        self, adapter: MemoryStateAdapter
    ) -> None:
        depth = await adapter.queue_depth("thread1")
        assert depth == 0

    async def test_should_dequeue_in_fifo_order(self, adapter: MemoryStateAdapter) -> None:
        e1 = {"message": {"id": "m1"}, "enqueued_at": 1000, "expires_at": 90000}
        e2 = {"message": {"id": "m2"}, "enqueued_at": 2000, "expires_at": 90000}
        e3 = {"message": {"id": "m3"}, "enqueued_at": 3000, "expires_at": 90000}

        await adapter.enqueue("thread1", e1, 10)
        await adapter.enqueue("thread1", e2, 10)
        await adapter.enqueue("thread1", e3, 10)

        assert await adapter.queue_depth("thread1") == 3

        r1 = await adapter.dequeue("thread1")
        assert r1["message"] == {"id": "m1"}

        r2 = await adapter.dequeue("thread1")
        assert r2["message"] == {"id": "m2"}

        r3 = await adapter.dequeue("thread1")
        assert r3["message"] == {"id": "m3"}

        assert await adapter.dequeue("thread1") is None
        assert await adapter.queue_depth("thread1") == 0

    async def test_should_trim_to_max_size_keeping_newest_entries(
        self, adapter: MemoryStateAdapter
    ) -> None:
        for i in range(1, 6):
            await adapter.enqueue(
                "thread1",
                {"message": {"id": f"m{i}"}, "enqueued_at": i * 1000, "expires_at": 90000},
                3,
            )

        assert await adapter.queue_depth("thread1") == 3

        r1 = await adapter.dequeue("thread1")
        assert r1["message"] == {"id": "m3"}

        r2 = await adapter.dequeue("thread1")
        assert r2["message"] == {"id": "m4"}

        r3 = await adapter.dequeue("thread1")
        assert r3["message"] == {"id": "m5"}

    async def test_should_handle_max_size_of_one_debounce_behavior(
        self, adapter: MemoryStateAdapter
    ) -> None:
        await adapter.enqueue(
            "thread1",
            {"message": {"id": "m1"}, "enqueued_at": 1000, "expires_at": 90000},
            1,
        )
        await adapter.enqueue(
            "thread1",
            {"message": {"id": "m2"}, "enqueued_at": 2000, "expires_at": 90000},
            1,
        )
        await adapter.enqueue(
            "thread1",
            {"message": {"id": "m3"}, "enqueued_at": 3000, "expires_at": 90000},
            1,
        )

        assert await adapter.queue_depth("thread1") == 1
        result = await adapter.dequeue("thread1")
        assert result["message"] == {"id": "m3"}

    async def test_should_keep_queues_isolated_by_thread(self, adapter: MemoryStateAdapter) -> None:
        await adapter.enqueue(
            "thread-a",
            {"message": {"id": "a1"}, "enqueued_at": 1000, "expires_at": 90000},
            10,
        )
        await adapter.enqueue(
            "thread-b",
            {"message": {"id": "b1"}, "enqueued_at": 1000, "expires_at": 90000},
            10,
        )

        assert await adapter.queue_depth("thread-a") == 1
        assert await adapter.queue_depth("thread-b") == 1

        ra = await adapter.dequeue("thread-a")
        assert ra["message"] == {"id": "a1"}

        rb = await adapter.dequeue("thread-b")
        assert rb["message"] == {"id": "b1"}

    async def test_should_clear_queues_on_disconnect(self, adapter: MemoryStateAdapter) -> None:
        await adapter.enqueue(
            "thread1",
            {"message": {"id": "m1"}, "enqueued_at": 1000, "expires_at": 90000},
            10,
        )

        await adapter.disconnect()
        await adapter.connect()

        assert await adapter.queue_depth("thread1") == 0
        assert await adapter.dequeue("thread1") is None


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


class TestConnection:
    async def test_should_throw_when_not_connected(self) -> None:
        new_adapter = create_memory_state()
        with pytest.raises(RuntimeError, match="not connected"):
            await new_adapter.subscribe("test")

    async def test_should_clear_state_on_disconnect(self, adapter: MemoryStateAdapter) -> None:
        await adapter.subscribe("thread1")
        await adapter.acquire_lock("thread1", 5000)

        await adapter.disconnect()
        await adapter.connect()

        assert await adapter.is_subscribed("thread1") is False
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
