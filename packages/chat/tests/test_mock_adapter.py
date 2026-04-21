"""Tests for :mod:`chat.mock_adapter` — the shared test utilities."""

from __future__ import annotations

from chat.mock_adapter import (
    MockStateAdapter,
    create_mock_adapter,
    create_mock_state,
    create_test_message,
    mock_logger,
)
from chat.types import QueueEntry

# ----------------------------------------------------------------------------
# mock_logger
# ----------------------------------------------------------------------------


class TestMockLogger:
    def test_captures_info_calls(self) -> None:
        mock_logger.reset()
        mock_logger.info("hello")
        mock_logger.info.assert_called_once_with("hello")

    def test_child_returns_self(self) -> None:
        mock_logger.reset()
        child = mock_logger.child({"scope": "test"})
        assert child is mock_logger


# ----------------------------------------------------------------------------
# create_mock_adapter
# ----------------------------------------------------------------------------


class TestCreateMockAdapter:
    def test_uses_default_name(self) -> None:
        adapter = create_mock_adapter()
        assert adapter.name == "slack"
        assert adapter.user_name == "slack-bot"

    def test_accepts_custom_name(self) -> None:
        adapter = create_mock_adapter("teams")
        assert adapter.name == "teams"
        assert adapter.user_name == "teams-bot"

    def test_encode_thread_id(self) -> None:
        adapter = create_mock_adapter("slack")
        result = adapter.encode_thread_id({"channel": "C123", "thread": "1234.5"})
        assert result == "slack:C123:1234.5"

    def test_decode_thread_id(self) -> None:
        adapter = create_mock_adapter("slack")
        result = adapter.decode_thread_id("slack:C123:1234.5")
        assert result == {"channel": "C123", "thread": "1234.5"}

    def test_is_dm_detects_dm_thread(self) -> None:
        adapter = create_mock_adapter("slack")
        assert adapter.is_dm("slack:D123:") is True
        assert adapter.is_dm("slack:C123:1234") is False

    def test_channel_id_from_thread_id(self) -> None:
        adapter = create_mock_adapter("slack")
        assert adapter.channel_id_from_thread_id("slack:C123:1234") == "slack:C123"

    async def test_post_message_returns_default(self) -> None:
        adapter = create_mock_adapter("slack")
        result = await adapter.post_message("slack:C1:t1", "hello")
        assert result["id"] == "msg-1"


# ----------------------------------------------------------------------------
# create_mock_state
# ----------------------------------------------------------------------------


class TestCreateMockState:
    async def test_get_set_round_trip(self) -> None:
        state = create_mock_state()
        await state.set("k", "v")
        assert await state.get("k") == "v"

    async def test_subscribe_is_subscribed_unsubscribe(self) -> None:
        state = create_mock_state()
        assert await state.is_subscribed("t1") is False
        await state.subscribe("t1")
        assert await state.is_subscribed("t1") is True
        await state.unsubscribe("t1")
        assert await state.is_subscribed("t1") is False

    async def test_acquire_lock_blocks_second_caller(self) -> None:
        state = create_mock_state()
        lock1 = await state.acquire_lock("t1", 1000)
        assert lock1 is not None
        lock2 = await state.acquire_lock("t1", 1000)
        assert lock2 is None

    async def test_release_lock_allows_reacquire(self) -> None:
        state = create_mock_state()
        lock1 = await state.acquire_lock("t1", 1000)
        assert lock1 is not None
        await state.release_lock(lock1)
        lock2 = await state.acquire_lock("t1", 1000)
        assert lock2 is not None

    async def test_force_release_lock(self) -> None:
        state = create_mock_state()
        await state.acquire_lock("t1", 1000)
        await state.force_release_lock("t1")
        lock2 = await state.acquire_lock("t1", 1000)
        assert lock2 is not None

    async def test_set_if_not_exists_returns_false_on_existing(self) -> None:
        state = create_mock_state()
        assert await state.set_if_not_exists("k", "v") is True
        assert await state.set_if_not_exists("k", "v2") is False
        assert await state.get("k") == "v"

    async def test_append_to_list_and_trim(self) -> None:
        state = create_mock_state()
        for i in range(5):
            await state.append_to_list("key", i, {"maxLength": 3})
        assert await state.get_list("key") == [2, 3, 4]

    async def test_append_to_list_records_call(self) -> None:
        state = create_mock_state()
        await state.append_to_list("key", "value", {"maxLength": 10, "ttlMs": 1000})
        state.append_to_list.assert_called_once_with(
            "key", "value", {"maxLength": 10, "ttlMs": 1000}
        )

    async def test_get_list_returns_empty_on_missing(self) -> None:
        state = create_mock_state()
        assert await state.get_list("missing") == []

    async def test_enqueue_dequeue_and_depth(self) -> None:
        state = create_mock_state()
        entry = QueueEntry(enqueued_at=1, expires_at=2, message="m")
        depth = await state.enqueue("t1", entry, 10)
        assert depth == 1
        assert await state.queue_depth("t1") == 1
        out = await state.dequeue("t1")
        assert out is entry
        assert await state.queue_depth("t1") == 0
        assert await state.dequeue("t1") is None

    async def test_enqueue_drops_old_when_over_size(self) -> None:
        state = create_mock_state()
        for i in range(5):
            await state.enqueue("t1", QueueEntry(enqueued_at=i, expires_at=i, message=i), 3)
        assert await state.queue_depth("t1") == 3


# ----------------------------------------------------------------------------
# create_test_message
# ----------------------------------------------------------------------------


class TestCreateTestMessage:
    def test_basic_shape(self) -> None:
        msg = create_test_message("m1", "Hello")
        assert msg.id == "m1"
        assert msg.text == "Hello"
        assert msg.thread_id == "slack:C123:1234.5678"
        assert msg.author.user_name == "testuser"
        assert msg.attachments == []

    def test_overrides(self) -> None:
        msg = create_test_message("m1", "Hello", thread_id="custom:X:Y", is_mention=True)
        assert msg.thread_id == "custom:X:Y"
        assert msg.is_mention is True


# ----------------------------------------------------------------------------
# MockStateAdapter structural
# ----------------------------------------------------------------------------


class TestMockStateAdapterIsAStateAdapter:
    def test_has_all_expected_methods(self) -> None:
        state: MockStateAdapter = create_mock_state()
        for name in (
            "connect",
            "disconnect",
            "subscribe",
            "unsubscribe",
            "is_subscribed",
            "acquire_lock",
            "release_lock",
            "force_release_lock",
            "extend_lock",
            "get",
            "set",
            "set_if_not_exists",
            "delete",
            "append_to_list",
            "get_list",
            "enqueue",
            "dequeue",
            "queue_depth",
        ):
            assert callable(getattr(state, name)), f"missing method: {name}"
