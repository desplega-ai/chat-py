"""End-to-end dispatch integration tests against the in-memory state adapter.

Exercises the full ``chat.handle_incoming_message`` pipeline — dedupe → lock
→ subscription check → handler dispatch → reply → lock release — against
:class:`chat_adapter_state_memory.MemoryStateAdapter`. The memory backend is
the baseline: any behaviour that passes here should also pass against
redis / pg.

Run with ``uv run pytest packages/chat-integration-tests/tests/test_dispatch_memory.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from chat.errors import LockError
from chat.mock_adapter import mock_logger
from chat_adapter_state_memory import MemoryStateAdapter, create_memory_state
from chat_integration_tests._helpers import (
    HandlerSpy,
    build_chat,
    build_multi_adapter_chat,
    make_incoming_message,
    patch_post_message_raw,
)

THREAD_ID = "slack:C123:1700000000.000100"
CHANNEL_ID = "slack:C123"


@pytest.fixture(autouse=True)
def _reset_mock_logger() -> None:
    mock_logger.reset()


@pytest.fixture
async def state() -> AsyncIterator[MemoryStateAdapter]:
    backend = create_memory_state()
    await backend.connect()
    try:
        yield backend
    finally:
        await backend.disconnect()


# ---------------------------------------------------------------------------
# Happy path — webhook → dispatch → state → reply
# ---------------------------------------------------------------------------


class TestMemoryHappyPath:
    async def test_mention_dispatches_handler_and_replies(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        spy = HandlerSpy(reply="pong")
        chat.on_new_mention(spy)
        post_mock = patch_post_message_raw(
            adapter, {"id": "bot-reply-1", "threadId": THREAD_ID, "raw": {}}
        )
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="hey @slack-bot can you help?")
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        assert len(spy.calls) == 1
        thread_arg, msg_arg = spy.calls[0]
        assert thread_arg.id == THREAD_ID
        assert msg_arg.text == "hey @slack-bot can you help?"
        post_mock.assert_awaited_once()
        # Lock must be released after dispatch so another message can flow through.
        assert state._get_lock_count() == 0  # pyright: ignore[reportPrivateUsage]
        await chat.shutdown()

    async def test_dedupe_drops_second_delivery(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        patch_post_message_raw(adapter, {"id": "r1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot dedupe me")
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)  # duplicate id

        assert len(spy.calls) == 1, "Second delivery should be deduped"
        await chat.shutdown()

    async def test_subscribed_thread_routes_non_mention(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        mention_spy = HandlerSpy()
        subscribed_spy = HandlerSpy()
        chat.on_new_mention(mention_spy)
        chat.on_subscribed_message(subscribed_spy)
        patch_post_message_raw(adapter, {"id": "r1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        await state.subscribe(THREAD_ID)
        plain_msg = make_incoming_message(thread_id=THREAD_ID, text="plain chatter, no bot name")
        await chat.handle_incoming_message(adapter, THREAD_ID, plain_msg)

        assert len(subscribed_spy.calls) == 1
        assert len(mention_spy.calls) == 0
        await chat.shutdown()

    async def test_skips_message_authored_by_bot_itself(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        await chat.initialize()

        self_msg = make_incoming_message(
            thread_id=THREAD_ID,
            text="@slack-bot don't loop",
            is_me=True,
        )
        await chat.handle_incoming_message(adapter, THREAD_ID, self_msg)

        assert spy.calls == []
        await chat.shutdown()

    async def test_pattern_matching_dispatch(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        help_spy = HandlerSpy(reply="here is help")
        chat.on_new_message(r"^!help\b", help_spy)
        patch_post_message_raw(adapter, {"id": "r1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="!help me plz")
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)
        ignored = make_incoming_message(
            thread_id=THREAD_ID,
            text="nothing special",
            message_id="msg-2",
        )
        await chat.handle_incoming_message(adapter, THREAD_ID, ignored)

        assert len(help_spy.calls) == 1
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestMemoryErrorPaths:
    async def test_lock_conflict_raises_lock_error(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        chat.on_new_mention(HandlerSpy())
        await chat.initialize()

        # Pre-acquire the thread lock so the next handler can't grab it.
        held = await state.acquire_lock(THREAD_ID, ttl_ms=30_000)
        assert held is not None

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot take a lock please")
        with pytest.raises(LockError):
            await chat.handle_incoming_message(adapter, THREAD_ID, msg)
        await chat.shutdown()

    async def test_handler_exception_bubbles_but_releases_lock(
        self, state: MemoryStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=state)
        boom = HandlerSpy(raise_on_call=RuntimeError("handler failed"))
        chat.on_new_mention(boom)
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot boom")
        with pytest.raises(RuntimeError, match="handler failed"):
            await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        # Lock was released in the ``finally`` branch of ``_handle_drop``
        # even though the handler raised.
        assert state._get_lock_count() == 0  # pyright: ignore[reportPrivateUsage]
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Multi-adapter fan-out
# ---------------------------------------------------------------------------


class TestMemoryMultiAdapter:
    async def test_each_adapter_dispatches_independently(self, state: MemoryStateAdapter) -> None:
        chat, adapters = build_multi_adapter_chat(
            state=state, adapter_names=["slack", "discord", "github"]
        )
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        for adapter in adapters.values():
            patch_post_message_raw(adapter, {"id": "ack", "threadId": "stub", "raw": {}})
        await chat.initialize()

        for name, adapter in adapters.items():
            tid = f"{name}:C:{name}-1"
            msg = make_incoming_message(
                thread_id=tid,
                text=f"@{name}-bot hi",
                message_id=f"{name}-msg",
            )
            await chat.handle_incoming_message(adapter, tid, msg)

        assert len(spy.calls) == 3, "One mention per adapter should dispatch once"
        await chat.shutdown()
