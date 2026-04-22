"""End-to-end dispatch tests against :class:`PostgresStateAdapter`.

The in-memory ``_FakePgPool`` below emulates just enough of the asyncpg
query surface (``INSERT … ON CONFLICT`` / ``DELETE`` / ``SELECT``) to let us
run the real adapter against it. That means the same ``handle_incoming_
message`` path that runs against Redis gets exercised here too, with the
Postgres SQL codec doing its actual work (string-encoded JSON values,
``RETURNING``-based upsert semantics, …).

Live Postgres tests are gated on ``POSTGRES_URL``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from chat.errors import LockError
from chat.mock_adapter import mock_logger
from chat_adapter_state_pg import PostgresStateAdapter, create_postgres_state
from chat_integration_tests._env import require_backend
from chat_integration_tests._helpers import (
    HandlerSpy,
    build_chat,
    make_incoming_message,
    patch_post_message_raw,
)

THREAD_ID = "slack:C-PG:1700000000.000777"


@pytest.fixture(autouse=True)
def _reset_mock_logger() -> None:
    mock_logger.reset()


# ---------------------------------------------------------------------------
# Fake pg pool
# ---------------------------------------------------------------------------


class _FakePgPool:
    """Asyncpg-pool-shaped in-memory emulator for integration tests.

    Honours just enough SQL to make ``PostgresStateAdapter`` happy:

    - ``SELECT 1`` (connect probe) returns a single row.
    - subscriptions table: INSERT (noop on conflict), DELETE, SELECT count.
    - locks table: INSERT … ON CONFLICT DO UPDATE with the expires_at check,
      DELETE-with-token (release), DELETE by thread (force release).
    - cache table: INSERT … ON CONFLICT DO NOTHING for ``set_if_not_exists``.

    Queries that don't match a recognised pattern return ``[]`` — enough for
    most SELECTs where the dispatch path doesn't need data back.
    """

    def __init__(self) -> None:
        self._subscriptions: set[tuple[str, str]] = set()
        self._locks: dict[tuple[str, str], dict[str, Any]] = {}
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def query(self, text: str, *params: Any) -> list[dict[str, Any]]:
        self.calls.append((text, params))
        now_ms = int(time.time() * 1000)

        # Connect probe
        if text.strip() == "SELECT 1":
            return [{"?column?": 1}]

        # Subscriptions
        if text.startswith("INSERT INTO chat_state_subscriptions"):
            prefix, thread_id = params[:2]
            self._subscriptions.add((prefix, thread_id))
            return []
        if text.startswith("DELETE FROM chat_state_subscriptions"):
            prefix, thread_id = params[:2]
            self._subscriptions.discard((prefix, thread_id))
            return []
        if text.startswith("SELECT 1 FROM chat_state_subscriptions"):
            prefix, thread_id = params[:2]
            return [{"?column?": 1}] if (prefix, thread_id) in self._subscriptions else []

        # Locks
        if text.startswith("INSERT INTO chat_state_locks"):
            prefix, thread_id, token, ttl_ms = params[:4]
            existing = self._locks.get((prefix, thread_id))
            if existing is not None and existing["expires_at"] > now_ms:
                return []  # lock held — insert conflicted and ``WHERE`` blocked update
            record = {
                "thread_id": thread_id,
                "token": token,
                "expires_at": now_ms + int(ttl_ms),
            }
            self._locks[(prefix, thread_id)] = record
            return [dict(record)]
        if "DELETE FROM chat_state_locks" in text and "AND token = $3" in text:
            prefix, thread_id, token = params[:3]
            existing = self._locks.get((prefix, thread_id))
            if existing is not None and existing["token"] == token:
                self._locks.pop((prefix, thread_id), None)
            return []
        if text.startswith("DELETE FROM chat_state_locks"):
            prefix, thread_id = params[:2]
            self._locks.pop((prefix, thread_id), None)
            return []

        # set_if_not_exists on the cache table → backs ``dedupe:`` keys
        if "ON CONFLICT (key_prefix, cache_key) DO NOTHING" in text:
            prefix, cache_key, value, ttl = params[:4]
            key = (prefix, cache_key)
            if key in self._cache:
                return []
            self._cache[key] = {
                "value": value,
                "expires_at": (now_ms + int(ttl)) if ttl is not None else None,
            }
            return [{"cache_key": cache_key}]
        # regular set via upsert
        if "ON CONFLICT (key_prefix, cache_key) DO UPDATE" in text:
            prefix, cache_key, value, ttl = params[:4]
            self._cache[(prefix, cache_key)] = {
                "value": value,
                "expires_at": (now_ms + int(ttl)) if ttl is not None else None,
            }
            return []
        if text.startswith("DELETE FROM chat_state_cache"):
            prefix, cache_key = params[:2]
            self._cache.pop((prefix, cache_key), None)
            return []

        # Fallback: return empty. Adapter methods that return lists treat this
        # as "no rows found" / "nothing happened", which is a safe default.
        return []

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
async def fake_pg_state() -> AsyncIterator[PostgresStateAdapter]:
    pool = _FakePgPool()
    adapter = PostgresStateAdapter(client=pool, key_prefix="chat-sdk-itest-pg")
    await adapter.connect()
    try:
        yield adapter
    finally:
        await adapter.disconnect()


@pytest.fixture
async def live_pg_state() -> AsyncIterator[PostgresStateAdapter]:
    url = require_backend("postgres")
    prefix = f"chat-sdk-itest-pg-{os.getpid()}"
    adapter = create_postgres_state(url=url, key_prefix=prefix)
    await adapter.connect()
    try:
        yield adapter
    finally:
        await adapter.disconnect()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPgHappyPath:
    async def test_mention_dispatches_through_pg_state(
        self, fake_pg_state: PostgresStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=fake_pg_state)
        spy = HandlerSpy(reply="ok")
        chat.on_new_mention(spy)
        patch_post_message_raw(adapter, {"id": "r1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot pg hi")
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        assert len(spy.calls) == 1
        await chat.shutdown()

    async def test_dedupe_stored_in_cache_table(self, fake_pg_state: PostgresStateAdapter) -> None:
        chat, adapter = build_chat(state=fake_pg_state)
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        await chat.initialize()

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot pg dedupe")
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        assert len(spy.calls) == 1
        await chat.shutdown()

    async def test_subscribed_thread_routes_non_mention(
        self, fake_pg_state: PostgresStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=fake_pg_state)
        subscribed = HandlerSpy()
        chat.on_subscribed_message(subscribed)
        await chat.initialize()

        await fake_pg_state.subscribe(THREAD_ID)
        msg = make_incoming_message(
            thread_id=THREAD_ID, text="just a status update", message_id="m-pg-sub"
        )
        await chat.handle_incoming_message(adapter, THREAD_ID, msg)

        assert len(subscribed.calls) == 1
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


class TestPgErrorPaths:
    async def test_lock_contention_surfaces_lock_error(
        self, fake_pg_state: PostgresStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=fake_pg_state)
        chat.on_new_mention(HandlerSpy())
        await chat.initialize()

        # Pre-claim the lock via the adapter itself so the ``INSERT … ON
        # CONFLICT`` path in the fake pool sees the expires_at check fail.
        pre = await fake_pg_state.acquire_lock(THREAD_ID, ttl_ms=30_000)
        assert pre is not None

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot block")
        with pytest.raises(LockError):
            await chat.handle_incoming_message(adapter, THREAD_ID, msg)
        await chat.shutdown()

    async def test_pool_exception_bubbles_up(self, fake_pg_state: PostgresStateAdapter) -> None:
        # Swap the underlying pool for one that rejects every query after
        # the adapter is already connected — simulating mid-flight loss of
        # the DB connection.
        class ExplodingPool:
            async def query(self, text: str, *params: Any) -> list[dict[str, Any]]:
                raise RuntimeError("db connection lost")

            async def close(self) -> None:
                pass

        fake_pg_state._pool = ExplodingPool()  # pyright: ignore[reportPrivateUsage]

        chat, adapter = build_chat(state=fake_pg_state)
        chat.on_new_mention(HandlerSpy())
        # Mark initialized so ``handle_incoming_message`` doesn't try to
        # ``connect`` again (which would also hit the exploding pool).
        chat._initialized = True  # pyright: ignore[reportPrivateUsage]

        msg = make_incoming_message(thread_id=THREAD_ID, text="@slack-bot die")
        with pytest.raises(RuntimeError, match="db connection lost"):
            await chat.handle_incoming_message(adapter, THREAD_ID, msg)


# ---------------------------------------------------------------------------
# Live Postgres — gated
# ---------------------------------------------------------------------------


class TestPgLive:
    async def test_live_pg_dispatch_round_trip(self, live_pg_state: PostgresStateAdapter) -> None:
        chat, adapter = build_chat(state=live_pg_state)
        spy = HandlerSpy()
        chat.on_new_mention(spy)
        patch_post_message_raw(adapter, {"id": "live-pg-1", "threadId": THREAD_ID, "raw": {}})
        await chat.initialize()

        unique_thread = f"slack:LIVEPG:{os.getpid()}-{id(self)}"
        msg = make_incoming_message(
            thread_id=unique_thread,
            text="@slack-bot live pg",
            message_id=f"live-pg-msg-{os.getpid()}",
        )
        await chat.handle_incoming_message(adapter, unique_thread, msg)
        assert len(spy.calls) == 1
        await chat.shutdown()


# ---------------------------------------------------------------------------
# JSON serialization — set_if_not_exists stores a JSON string
# ---------------------------------------------------------------------------


class TestPgSerialization:
    async def test_set_if_not_exists_serializes_value_as_json(
        self, fake_pg_state: PostgresStateAdapter
    ) -> None:
        key = "cache:structured"
        await fake_pg_state.set_if_not_exists(key, {"count": 3, "flag": True}, 60_000)

        pool = fake_pg_state._pool  # pyright: ignore[reportPrivateUsage]
        assert isinstance(pool, _FakePgPool)
        stored = pool._cache.get(("chat-sdk-itest-pg", key))
        assert stored is not None
        assert json.loads(stored["value"]) == {"count": 3, "flag": True}
