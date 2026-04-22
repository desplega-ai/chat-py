"""End-to-end dispatch tests against :class:`IoRedisStateAdapter`.

``chat-adapter-state-ioredis`` is a thin shim over
:class:`RedisStateAdapter`. Our job here is to verify that the shim's custom
lock-token prefix (``ioredis_*``) round-trips correctly through the full
Chat pipeline and that the shared Redis machinery otherwise behaves
identically.

Live tests reuse ``REDIS_URL`` because the ioredis shim points at the same
server.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import fakeredis.aioredis as fakeredis_async
import pytest
from chat.mock_adapter import mock_logger
from chat_adapter_state_ioredis import IoRedisStateAdapter, create_ioredis_state
from chat_integration_tests._env import require_backend
from chat_integration_tests._helpers import (
    HandlerSpy,
    build_chat,
    make_incoming_message,
    patch_post_message_raw,
)

THREAD_ID = "slack:C-IO:1700000000.000321"


@pytest.fixture(autouse=True)
def _reset_mock_logger() -> None:
    mock_logger.reset()


@pytest.fixture
async def fake_ioredis_state() -> AsyncIterator[IoRedisStateAdapter]:
    client = fakeredis_async.FakeRedis()
    adapter = IoRedisStateAdapter(client=client, key_prefix="chat-sdk-itest-io")
    await adapter.connect()
    try:
        yield adapter
    finally:
        await adapter.disconnect()
        await client.aclose()  # type: ignore[attr-defined]


@pytest.fixture
async def live_ioredis_state() -> AsyncIterator[IoRedisStateAdapter]:
    url = require_backend("ioredis")
    prefix = f"chat-sdk-itest-io-{os.getpid()}"
    adapter = create_ioredis_state(url=url, key_prefix=prefix)
    await adapter.connect()
    try:
        yield adapter
    finally:
        await adapter.disconnect()


class TestIoRedisHappyPath:
    async def test_mention_dispatch_round_trip(
        self, fake_ioredis_state: IoRedisStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=fake_ioredis_state)
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        patch_post_message_raw(adapter, {"id": "r1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot ioredis hi")
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        assert len(spy.calls) == 1
        await chat.shutdown()

    async def test_lock_token_uses_ioredis_prefix(
        self, fake_ioredis_state: IoRedisStateAdapter
    ) -> None:
        lock = await fake_ioredis_state.acquire_lock(THREAD_ID, ttl_ms=5_000)
        assert lock is not None
        # The shim's one observable difference from the base Redis adapter.
        token = lock["token"] if isinstance(lock, dict) else lock.token
        assert token.startswith("ioredis_"), f"expected ioredis_ prefix, got {token!r}"
        await fake_ioredis_state.release_lock(lock)


class TestIoRedisLive:
    async def test_live_ioredis_dispatch_round_trip(
        self, live_ioredis_state: IoRedisStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=live_ioredis_state)
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        patch_post_message_raw(adapter, {"id": "live-io-1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        unique_thread = f"slack:LIVEIO:{os.getpid()}-{id(self)}"
        msg = make_incoming_message(
            thread_id=unique_thread,
            text="@slack-bot live ioredis",
            message_id=f"live-io-msg-{os.getpid()}",
        )
        await chat.handle_incoming_message(adapter, unique_thread, msg)
        assert len(spy.calls) == 1
        await chat.shutdown()
