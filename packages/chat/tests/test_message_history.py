"""Tests for :mod:`chat.message_history` — port of ``message-history.test.ts``."""

from __future__ import annotations

import pytest
from chat.message_history import MessageHistoryCache
from chat.mock_adapter import MockStateAdapter, create_mock_state, create_test_message


@pytest.fixture
def state() -> MockStateAdapter:
    return create_mock_state()


@pytest.fixture
def cache(state: MockStateAdapter) -> MessageHistoryCache:
    return MessageHistoryCache(state)


class TestMessageHistoryCache:
    async def test_append_and_retrieve_messages(self, cache: MessageHistoryCache) -> None:
        msg1 = create_test_message("m1", "Hello")
        msg2 = create_test_message("m2", "World")

        await cache.append("thread-1", msg1)
        await cache.append("thread-1", msg2)

        messages = await cache.get_messages("thread-1")
        assert len(messages) == 2
        assert messages[0].id == "m1"
        assert messages[0].text == "Hello"
        assert messages[1].id == "m2"
        assert messages[1].text == "World"

    async def test_uses_append_to_list_for_atomic_appends(
        self, cache: MessageHistoryCache, state: MockStateAdapter
    ) -> None:
        msg = create_test_message("m1", "Hello")
        await cache.append("thread-1", msg)

        assert state.append_to_list.call_count == 1
        args = state.append_to_list.call_args.args
        assert args[0] == "msg-history:thread-1"
        assert args[1]["id"] == "m1"
        assert args[2] == {"maxLength": 100, "ttlMs": 7 * 24 * 60 * 60 * 1000}

    async def test_trims_to_max_messages_keeping_newest(self, state: MockStateAdapter) -> None:
        small_cache = MessageHistoryCache(state, max_messages=3)

        for i in range(1, 6):
            await small_cache.append("thread-1", create_test_message(f"m{i}", f"Msg {i}"))

        messages = await small_cache.get_messages("thread-1")
        assert len(messages) == 3
        assert messages[0].id == "m3"
        assert messages[1].id == "m4"
        assert messages[2].id == "m5"

    async def test_strips_raw_field_on_storage(
        self, cache: MessageHistoryCache, state: MockStateAdapter
    ) -> None:
        msg = create_test_message("m1", "Hello")
        msg.raw = {"secret": "data", "nested": {"deep": True}}

        await cache.append("thread-1", msg)

        appended_value = state.append_to_list.call_args.args[1]
        assert appended_value["raw"] is None

    async def test_returns_empty_array_for_unknown_thread(self, cache: MessageHistoryCache) -> None:
        messages = await cache.get_messages("nonexistent")
        assert messages == []

    async def test_supports_limit_parameter_in_get_messages(
        self, cache: MessageHistoryCache
    ) -> None:
        for i in range(1, 11):
            await cache.append("thread-1", create_test_message(f"m{i}", f"Msg {i}"))

        messages = await cache.get_messages("thread-1", 3)
        assert len(messages) == 3
        assert messages[0].id == "m8"
        assert messages[1].id == "m9"
        assert messages[2].id == "m10"

    async def test_keeps_threads_isolated(self, cache: MessageHistoryCache) -> None:
        await cache.append("thread-1", create_test_message("m1", "Thread 1"))
        await cache.append("thread-2", create_test_message("m2", "Thread 2"))

        msgs1 = await cache.get_messages("thread-1")
        msgs2 = await cache.get_messages("thread-2")

        assert len(msgs1) == 1
        assert msgs1[0].text == "Thread 1"
        assert len(msgs2) == 1
        assert msgs2[0].text == "Thread 2"
