"""Tests for PostgresStateAdapter (mocked pool).

Python port of upstream ``packages/state-pg/src/index.test.ts``. Uses a
lightweight in-memory mock pool to exercise the SQL call sites without
touching a real database. For end-to-end tests against a live Postgres,
see ``test_postgres_state_integration.py``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from chat_adapter_state_pg import PostgresStateAdapter, create_postgres_state

# ---------------------------------------------------------------------------
# Mock pool
# ---------------------------------------------------------------------------


class MockPool:
    """Tiny mock mirroring the subset of :class:`_PoolLike` the adapter uses."""

    def __init__(
        self,
        query_fn: Any | None = None,
    ) -> None:
        self._query_fn = query_fn
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def query(self, text: str, *params: Any) -> list[dict[str, Any]]:
        self.calls.append((text, params))
        if self._query_fn is None:
            return []
        result = self._query_fn(text, params)
        return list(result)

    async def close(self) -> None:
        self.closed = True

    def find_call(self, substring: str) -> tuple[str, tuple[Any, ...]] | None:
        """Return the first recorded call whose SQL contains ``substring``."""

        for text, params in self.calls:
            if substring in text:
                return text, params
        return None


def _make_rows(rows: list[dict[str, Any]]) -> Any:
    """Build a query function that returns ``rows`` regardless of SQL text."""

    return lambda _text, _params: rows


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------


class TestExports:
    def test_exports_create_postgres_state(self) -> None:
        assert callable(create_postgres_state)

    def test_exports_postgres_state_adapter(self) -> None:
        assert callable(PostgresStateAdapter)


# ---------------------------------------------------------------------------
# create_postgres_state factory
# ---------------------------------------------------------------------------


class TestCreatePostgresState:
    def test_creates_adapter_with_url_option(self) -> None:
        adapter = create_postgres_state(url="postgres://postgres:postgres@localhost:5432/chat")
        assert isinstance(adapter, PostgresStateAdapter)

    def test_creates_adapter_with_custom_key_prefix(self) -> None:
        adapter = create_postgres_state(
            url="postgres://postgres:postgres@localhost:5432/chat",
            key_prefix="custom-prefix",
        )
        assert isinstance(adapter, PostgresStateAdapter)

    def test_creates_adapter_with_existing_client(self) -> None:
        client = MockPool()
        adapter = create_postgres_state(client=client)
        assert isinstance(adapter, PostgresStateAdapter)

    def test_creates_adapter_with_pool_alias(self) -> None:
        client = MockPool()
        adapter = create_postgres_state(pool=client)
        assert isinstance(adapter, PostgresStateAdapter)

    def test_raises_when_no_url_or_env_var_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ValueError, match="Postgres url is required"):
            create_postgres_state()

    def test_uses_postgres_url_env_var_as_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTGRES_URL", "postgres://postgres:postgres@localhost:5432/chat")
        adapter = create_postgres_state()
        assert isinstance(adapter, PostgresStateAdapter)

    def test_uses_database_url_env_var_as_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/chat")
        adapter = create_postgres_state()
        assert isinstance(adapter, PostgresStateAdapter)


# ---------------------------------------------------------------------------
# ensure_connected guard
# ---------------------------------------------------------------------------


class TestEnsureConnected:
    async def test_subscribe_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.subscribe("thread1")

    async def test_unsubscribe_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.unsubscribe("thread1")

    async def test_is_subscribed_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.is_subscribed("thread1")

    async def test_acquire_lock_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.acquire_lock("thread1", 5000)

    async def test_release_lock_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        lock = {"thread_id": "thread1", "token": "tok", "expires_at": 0}
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.release_lock(lock)

    async def test_extend_lock_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        lock = {"thread_id": "thread1", "token": "tok", "expires_at": 0}
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.extend_lock(lock, 5000)

    async def test_get_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.get("key")

    async def test_set_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.set("key", "value")

    async def test_set_if_not_exists_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.set_if_not_exists("key", "value")

    async def test_delete_raises_before_connect(self) -> None:
        adapter = PostgresStateAdapter(client=MockPool())
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.delete("key")


# ---------------------------------------------------------------------------
# Connect / disconnect
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    async def test_idempotent_on_connect(self) -> None:
        pool = MockPool(query_fn=_make_rows([]))
        adapter = PostgresStateAdapter(client=pool)
        await adapter.connect()
        await adapter.connect()

    async def test_idempotent_on_disconnect(self) -> None:
        pool = MockPool(query_fn=_make_rows([]))
        adapter = PostgresStateAdapter(client=pool)
        await adapter.connect()
        await adapter.disconnect()
        await adapter.disconnect()

    async def test_does_not_close_external_client(self) -> None:
        pool = MockPool(query_fn=_make_rows([]))
        adapter = PostgresStateAdapter(client=pool)
        await adapter.connect()
        await adapter.disconnect()
        assert pool.closed is False

    async def test_closes_owned_client_on_disconnect(self) -> None:
        # We construct an adapter with ``url=`` (owns client), then swap in a
        # mock pool to observe ``close()`` being called.
        adapter = PostgresStateAdapter(url="postgres://localhost:5432/test")
        fake = MockPool(query_fn=_make_rows([]))
        adapter._pool = fake  # swap the lazy asyncpg proxy with our mock
        await adapter.connect()
        await adapter.disconnect()
        assert fake.closed is True

    async def test_handles_connect_failure(self) -> None:
        def raise_once(_text: str, _params: tuple[Any, ...]) -> list[Any]:
            raise RuntimeError("connection refused")

        pool = MockPool(query_fn=raise_once)
        adapter = PostgresStateAdapter(client=pool)

        with pytest.raises(RuntimeError, match="connection refused"):
            await adapter.connect()

        # Retry should attempt again (connect did not record as connected).
        with pytest.raises(RuntimeError, match="connection refused"):
            await adapter.connect()


# ---------------------------------------------------------------------------
# Fixture: connected adapter with mutable mock rows
# ---------------------------------------------------------------------------


@pytest.fixture
async def connected_adapter() -> Any:
    """Return (adapter, state) where state.rows controls all mock rows."""

    state: dict[str, Any] = {"rows": []}

    def query_fn(_text: str, _params: tuple[Any, ...]) -> list[dict[str, Any]]:
        return list(state["rows"])

    pool = MockPool(query_fn=query_fn)
    adapter = PostgresStateAdapter(client=pool)
    await adapter.connect()
    try:
        yield adapter, pool, state
    finally:
        await adapter.disconnect()


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class TestSubscriptions:
    async def test_subscribe_does_not_throw(self, connected_adapter: Any) -> None:
        adapter, _pool, _state = connected_adapter
        await adapter.subscribe("slack:C123:1234.5678")

    async def test_unsubscribe_does_not_throw(self, connected_adapter: Any) -> None:
        adapter, _pool, _state = connected_adapter
        await adapter.unsubscribe("slack:C123:1234.5678")

    async def test_is_subscribed_returns_true_when_subscribed(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"?column?": 1}]
        result = await adapter.is_subscribed("slack:C123:1234.5678")
        assert result is True

    async def test_is_subscribed_returns_false_when_not_subscribed(
        self, connected_adapter: Any
    ) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = []
        result = await adapter.is_subscribed("slack:C123:1234.5678")
        assert result is False


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


class TestLocking:
    async def test_acquire_lock_returns_lock_when_row_is_returned(
        self, connected_adapter: Any
    ) -> None:
        adapter, _pool, state = connected_adapter
        expires_at = datetime.now(UTC) + timedelta(seconds=5)
        state["rows"] = [
            {
                "thread_id": "thread1",
                "token": "pg_test-token",
                "expires_at": expires_at,
            }
        ]

        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is not None
        assert lock["thread_id"] == "thread1"
        assert lock["token"] == "pg_test-token"
        assert lock["expires_at"] == int(expires_at.timestamp() * 1000)

    async def test_acquire_lock_returns_none_when_already_held(
        self, connected_adapter: Any
    ) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = []
        lock = await adapter.acquire_lock("thread1", 5000)
        assert lock is None

    async def test_release_lock_does_not_throw(self, connected_adapter: Any) -> None:
        adapter, _pool, _state = connected_adapter
        lock = {
            "thread_id": "thread1",
            "token": "pg_test-token",
            "expires_at": 0,
        }
        await adapter.release_lock(lock)

    async def test_extend_lock_returns_true_on_success(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"thread_id": "thread1"}]
        lock = {
            "thread_id": "thread1",
            "token": "pg_test-token",
            "expires_at": 0,
        }
        assert await adapter.extend_lock(lock, 5000) is True

    async def test_extend_lock_returns_false_on_failure(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = []
        lock = {
            "thread_id": "thread1",
            "token": "pg_test-token",
            "expires_at": 0,
        }
        assert await adapter.extend_lock(lock, 5000) is False

    async def test_force_release_lock_does_not_check_token(self, connected_adapter: Any) -> None:
        adapter, pool, _state = connected_adapter
        await adapter.force_release_lock("thread1")
        call = pool.find_call("DELETE FROM chat_state_locks")
        assert call is not None
        assert call[1] == ("chat-sdk", "thread1")

    async def test_force_release_lock_on_nonexistent_is_noop(self, connected_adapter: Any) -> None:
        adapter, _pool, _state = connected_adapter
        await adapter.force_release_lock("nonexistent")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class TestCache:
    async def test_get_returns_parsed_json_on_hit(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"value": '{"foo":"bar"}'}]
        result = await adapter.get("key")
        assert result == {"foo": "bar"}

    async def test_get_returns_raw_value_when_json_parse_fails(
        self, connected_adapter: Any
    ) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"value": "not-json"}]
        result = await adapter.get("key")
        assert result == "not-json"

    async def test_get_returns_none_on_miss_and_cleans_up(self, connected_adapter: Any) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = []
        result = await adapter.get("key")
        assert result is None
        # Opportunistic cleanup DELETE happens after a miss.
        assert any("DELETE FROM chat_state_cache" in text for text, _ in pool.calls)

    async def test_set_does_not_throw(self, connected_adapter: Any) -> None:
        adapter, _pool, _state = connected_adapter
        await adapter.set("key", {"foo": "bar"})

    async def test_set_with_ttl_does_not_throw(self, connected_adapter: Any) -> None:
        adapter, _pool, _state = connected_adapter
        await adapter.set("key", "value", 5000)

    async def test_set_if_not_exists_returns_true_on_insert(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"cache_key": "key"}]
        assert await adapter.set_if_not_exists("key", "value") is True

    async def test_set_if_not_exists_returns_false_on_existing_key(
        self, connected_adapter: Any
    ) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = []
        assert await adapter.set_if_not_exists("key", "value") is False

    async def test_set_if_not_exists_supports_ttl(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"cache_key": "key"}]
        assert await adapter.set_if_not_exists("key", "value", 5000) is True

    async def test_delete_does_not_throw(self, connected_adapter: Any) -> None:
        adapter, _pool, _state = connected_adapter
        await adapter.delete("key")


# ---------------------------------------------------------------------------
# append_to_list / get_list
# ---------------------------------------------------------------------------


class TestAppendToListGetList:
    async def test_calls_insert_for_append_to_list(self, connected_adapter: Any) -> None:
        adapter, pool, _state = connected_adapter
        await adapter.append_to_list("mylist", {"foo": "bar"})
        call = pool.find_call("INSERT INTO chat_state_lists")
        assert call is not None
        assert "chat-sdk" in call[1]
        assert "mylist" in call[1]
        assert '{"foo": "bar"}' in call[1]

    async def test_trims_overflow_when_max_length_is_specified(
        self, connected_adapter: Any
    ) -> None:
        adapter, pool, _state = connected_adapter
        await adapter.append_to_list("mylist", {"id": 1}, {"max_length": 10})
        assert pool.find_call("DELETE FROM chat_state_lists") is not None

    async def test_trims_overflow_with_camel_case_option_alias(
        self, connected_adapter: Any
    ) -> None:
        adapter, pool, _state = connected_adapter
        await adapter.append_to_list("mylist", {"id": 1}, {"maxLength": 10})
        assert pool.find_call("DELETE FROM chat_state_lists") is not None

    async def test_updates_ttl_when_ttl_ms_is_specified(self, connected_adapter: Any) -> None:
        adapter, pool, _state = connected_adapter
        await adapter.append_to_list("mylist", {"id": 1}, {"ttl_ms": 60000})
        assert pool.find_call("UPDATE chat_state_lists") is not None

    async def test_updates_ttl_with_camel_case_option_alias(self, connected_adapter: Any) -> None:
        adapter, pool, _state = connected_adapter
        await adapter.append_to_list("mylist", {"id": 1}, {"ttlMs": 60000})
        assert pool.find_call("UPDATE chat_state_lists") is not None

    async def test_get_list_returns_parsed_items(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"value": '{"id":1}'}, {"value": '{"id":2}'}]
        result = await adapter.get_list("mylist")
        assert result == [{"id": 1}, {"id": 2}]

    async def test_get_list_returns_empty_list_when_no_rows(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = []
        assert await adapter.get_list("mylist") == []


# ---------------------------------------------------------------------------
# enqueue / dequeue / queue_depth
# ---------------------------------------------------------------------------


class TestEnqueueDequeueQueueDepth:
    async def test_enqueue_purges_expired_before_inserting(self, connected_adapter: Any) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = [{"depth": 1}]
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        entry = {
            "message": {"id": "m1"},
            "enqueued_at": now_ms,
            "expires_at": now_ms + 90000,
        }
        await adapter.enqueue("thread1", entry, 10)

        purge = next(
            (
                c
                for c in pool.calls
                if "DELETE FROM chat_state_queues" in c[0] and "expires_at <= now()" in c[0]
            ),
            None,
        )
        assert purge is not None

    async def test_dequeue_purges_expired_before_selecting(self, connected_adapter: Any) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = []
        await adapter.dequeue("thread1")

        purge = next(
            (
                c
                for c in pool.calls
                if "DELETE FROM chat_state_queues" in c[0] and "expires_at <= now()" in c[0]
            ),
            None,
        )
        assert purge is not None

    async def test_queue_depth_counts_only_non_expired(self, connected_adapter: Any) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = [{"depth": 2}]
        await adapter.queue_depth("thread1")

        count = next(
            (c for c in pool.calls if "count(*)" in c[0] and "expires_at > now()" in c[0]),
            None,
        )
        assert count is not None

    async def test_enqueue_depth_counts_only_non_expired(self, connected_adapter: Any) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = [{"depth": 1}]
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        entry = {
            "message": {"id": "m1"},
            "enqueued_at": now_ms,
            "expires_at": now_ms + 90000,
        }
        await adapter.enqueue("thread1", entry, 10)

        count = next(
            (
                c
                for c in pool.calls
                if "count(*)" in c[0]
                and "chat_state_queues" in c[0]
                and "expires_at > now()" in c[0]
            ),
            None,
        )
        assert count is not None

    async def test_enqueue_calls_insert(self, connected_adapter: Any) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = [{"depth": 1}]
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        entry = {
            "message": {"id": "m1", "text": "hello"},
            "enqueued_at": now_ms,
            "expires_at": now_ms + 90000,
        }
        await adapter.enqueue("thread1", entry, 10)

        insert = pool.find_call("INSERT INTO chat_state_queues")
        assert insert is not None
        assert "chat-sdk" in insert[1]
        assert "thread1" in insert[1]

    async def test_enqueue_trims_overflow_when_max_size_is_positive(
        self, connected_adapter: Any
    ) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = [{"depth": 1}]
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        entry = {
            "message": {"id": "m1"},
            "enqueued_at": now_ms,
            "expires_at": now_ms + 90000,
        }
        await adapter.enqueue("thread1", entry, 5)

        assert pool.find_call("DELETE FROM chat_state_queues") is not None

    async def test_enqueue_returns_depth(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"depth": 3}]
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        entry = {
            "message": {"id": "m1"},
            "enqueued_at": now_ms,
            "expires_at": now_ms + 90000,
        }
        depth = await adapter.enqueue("thread1", entry, 10)
        assert depth == 3

    async def test_dequeue_returns_parsed_entry(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        entry = {
            "message": {"id": "m1", "text": "hello"},
            "enqueued_at": 1000,
            "expires_at": 91000,
        }
        import json as _json

        state["rows"] = [{"value": _json.dumps(entry)}]
        result = await adapter.dequeue("thread1")
        assert result == entry

    async def test_dequeue_returns_none_when_queue_empty(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = []
        result = await adapter.dequeue("thread1")
        assert result is None

    async def test_dequeue_uses_delete_returning(self, connected_adapter: Any) -> None:
        adapter, pool, state = connected_adapter
        state["rows"] = []
        await adapter.dequeue("thread1")

        delete = next(
            (
                c
                for c in pool.calls
                if "DELETE FROM chat_state_queues" in c[0] and "RETURNING value" in c[0]
            ),
            None,
        )
        assert delete is not None

    async def test_queue_depth_returns_parsed_count(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"depth": 5}]
        assert await adapter.queue_depth("thread1") == 5

    async def test_queue_depth_returns_zero_when_no_rows(self, connected_adapter: Any) -> None:
        adapter, _pool, state = connected_adapter
        state["rows"] = [{"depth": 0}]
        assert await adapter.queue_depth("thread1") == 0


# ---------------------------------------------------------------------------
# get_client
# ---------------------------------------------------------------------------


class TestGetClient:
    async def test_returns_underlying_client(self, connected_adapter: Any) -> None:
        adapter, pool, _state = connected_adapter
        assert adapter.get_client() is pool


# ---------------------------------------------------------------------------
# Integration tests — gated on PG_DSN / POSTGRES_URL env var
# ---------------------------------------------------------------------------


_PG_DSN = (
    os.environ.get("PG_DSN") or os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
)

pytestmark_integration = pytest.mark.skipif(
    _PG_DSN is None,
    reason="Set PG_DSN/POSTGRES_URL/DATABASE_URL to run integration tests",
)


@pytest.mark.integration
@pytestmark_integration
class TestIntegration:
    async def test_connects_to_postgres(self) -> None:
        adapter = create_postgres_state(url=_PG_DSN or "")
        await adapter.connect()
        await adapter.disconnect()
