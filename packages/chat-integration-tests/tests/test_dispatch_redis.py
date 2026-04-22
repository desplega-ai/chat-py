"""End-to-end dispatch tests against :class:`RedisStateAdapter`.

Exercises the same ``receive → dispatch → state → reply`` flow as the memory
suite, but against a real :class:`RedisStateAdapter` backed by
:mod:`fakeredis`. Because ``fakeredis.aioredis.FakeRedis`` implements the
same async Redis Protocol the adapter writes against, this covers the real
Lua / SET NX PX / SADD code paths without needing a running server.

Live-Redis tests run only when ``REDIS_URL`` is set.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis as fakeredis_async
import pytest
from chat.mock_adapter import mock_logger
from chat_adapter_state_redis import RedisStateAdapter, create_redis_state
from chat_integration_tests._env import require_backend
from chat_integration_tests._helpers import (
    HandlerSpy,
    build_chat,
    make_incoming_message,
    patch_post_message_raw,
)

THREAD_ID = "slack:C999:1700000000.000999"


@pytest.fixture(autouse=True)
def _reset_mock_logger() -> None:
    mock_logger.reset()


@pytest.fixture
async def fake_redis_state() -> AsyncIterator[RedisStateAdapter]:
    client = fakeredis_async.FakeRedis()
    # Fresh key prefix per test so parametrised runs don't collide.
    adapter = RedisStateAdapter(client=client, key_prefix="chat-sdk-itest")
    await adapter.connect()
    try:
        yield adapter
    finally:
        await adapter.disconnect()
        await client.aclose()  # type: ignore[attr-defined]


@pytest.fixture
async def live_redis_state() -> AsyncIterator[RedisStateAdapter]:
    url = require_backend("redis")  # skips unless REDIS_URL is set
    # Use a throwaway prefix so we don't clobber other suites running in parallel.
    prefix = f"chat-sdk-itest-{os.getpid()}"
    adapter = create_redis_state(url=url, key_prefix=prefix)
    await adapter.connect()
    try:
        yield adapter
    finally:
        # Best-effort cleanup: we don't delete keys here because the live
        # server might be shared; the unique prefix keeps us isolated.
        await adapter.disconnect()


# ---------------------------------------------------------------------------
# Happy path (fakeredis — runs in CI)
# ---------------------------------------------------------------------------


class TestRedisHappyPath:
    async def test_mention_dispatches_and_persists_dedupe(
        self, fake_redis_state: RedisStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=fake_redis_state)
        spy = HandlerSpy(reply="ok")
        chat.on_new_mention(spy)
        post_mock = patch_post_message_raw(adapter, {"id": "r1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot hi")
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        assert len(spy.calls) == 1
        post_mock.assert_awaited_once()

        # Dedupe row must now be present (set_if_not_exists returns False).
        dedupe_key = f"dedupe:{adapter.name}:{msg.id}"
        was_first = await fake_redis_state.set_if_not_exists(dedupe_key, True, 5_000)
        assert was_first is False
        await chat.shutdown()

    async def test_subscribe_then_receive_routes_to_subscribed_handler(
        self, fake_redis_state: RedisStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=fake_redis_state)
        subscribed = HandlerSpy()
        chat.on_subscribed_message(subscribed)
        patch_post_message_raw(adapter, {"id": "r1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        await fake_redis_state.subscribe(THREAD_ID)
        msg = make_incoming_message(
            thread_id=THREAD_ID, text="some plain chatter", message_id="m-sub"
        )
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        assert len(subscribed.calls) == 1
        # Subscription set must still contain the thread (no side-effect cleanup).
        assert await fake_redis_state.is_subscribed(THREAD_ID) is True
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Concurrency — lock contention between two Chat instances
# ---------------------------------------------------------------------------


class TestRedisLockContention:
    async def test_second_chat_instance_is_blocked_by_held_lock(
        self, fake_redis_state: RedisStateAdapter
    ) -> None:
        chat_a, adapter_a = build_chat(state=fake_redis_state)
        spy_a = HandlerSpy()
        chat_a.on_new_mention(spy_a)
        await chat_a.initialize()

        # Hold the thread lock externally — simulating "another worker is busy".
        held = await fake_redis_state.acquire_lock(THREAD_ID, ttl_ms=10_000)
        assert held is not None

        # Second Chat instance (same Redis backend) must hit LockError.
        from chat.errors import LockError

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot queued")
        with pytest.raises(LockError):
            await chat_a.handle_incoming_message(adapter_a, THREAD_ID, msg)
        assert spy_a.calls == []
        await chat_a.shutdown()


# ---------------------------------------------------------------------------
# Live Redis — gated on REDIS_URL
# ---------------------------------------------------------------------------


class TestRedisLive:
    async def test_live_redis_dispatch_round_trip(
        self, live_redis_state: RedisStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=live_redis_state)
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        patch_post_message_raw(adapter, {"id": "live-1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        # Use a unique thread id so concurrent runs don't collide.
        unique_thread = f"slack:LIVE:{os.getpid()}-{id(self)}"
        msg = make_incoming_message(
            thread_id=unique_thread,
            text="@slack-bot live",
            message_id=f"live-msg-{os.getpid()}",
        )
        await chat.handle_incoming_message(adapter, unique_thread, msg)

        assert len(spy.calls) == 1
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Error path — backend failure is surfaced to the caller
# ---------------------------------------------------------------------------


class TestRedisErrorSurface:
    async def test_backend_connection_lost_propagates_error(
        self, fake_redis_state: RedisStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=fake_redis_state)
        chat.on_new_mention(HandlerSpy())
        await chat.initialize()

        # Simulate connection loss by swapping the client for one that rejects
        # every command. We expect ``handle_incoming_message`` to let the
        # resulting Redis error bubble up rather than silently drop.
        class ExplodingRedis:
            async def set(self, *a: Any, **kw: Any) -> Any:
                raise ConnectionError("connection reset")

            async def sismember(self, *a: Any, **kw: Any) -> Any:
                raise ConnectionError("connection reset")

            async def eval(self, *a: Any, **kw: Any) -> Any:
                raise ConnectionError("connection reset")

            async def evalsha(self, *a: Any, **kw: Any) -> Any:
                raise ConnectionError("connection reset")

            async def script_load(self, *a: Any, **kw: Any) -> Any:
                raise ConnectionError("connection reset")

        fake_redis_state._client = ExplodingRedis()  # pyright: ignore[reportPrivateUsage]

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot dead")
        with pytest.raises(ConnectionError, match="connection reset"):
            await chat.handle_incoming_message(adapter, THREAD_ID, msg)
        await chat.shutdown()
