"""Webhook-level error propagation tests.

These tests cover the adapter boundary: when an adapter's ``handle_webhook``
raises one of the :mod:`chat_adapter_shared.errors` types, the :class:`Chat`
orchestrator must propagate the error unchanged (the HTTP framework above
is responsible for mapping it onto a status code).

We use a single in-memory state backend throughout — the error paths don't
depend on which state backend is wired up, so re-running the matrix would
just be noise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from chat.errors import ChatError
from chat.mock_adapter import mock_logger
from chat_adapter_shared.errors import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    ValidationError,
)
from chat_adapter_state_memory import MemoryStateAdapter, create_memory_state
from chat_integration_tests._helpers import build_chat


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


def _raise_webhook(error: BaseException) -> AsyncMock:
    mock = AsyncMock(side_effect=error)
    return mock


# ---------------------------------------------------------------------------
# Authentication / rate-limit / malformed payload
# ---------------------------------------------------------------------------


class TestWebhookErrorPropagation:
    async def test_auth_error_propagates(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        adapter.handle_webhook = _raise_webhook(AuthenticationError("slack", "Signature mismatch"))
        await chat.initialize()

        with pytest.raises(AuthenticationError) as ei:
            await chat.handle_webhook("slack", {"fake": "request"})
        assert ei.value.adapter == "slack"
        assert ei.value.code == "AUTH_FAILED"
        await chat.shutdown()

    async def test_rate_limit_propagates_with_retry_after(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        adapter.handle_webhook = _raise_webhook(AdapterRateLimitError("slack", retry_after=42))
        await chat.initialize()

        with pytest.raises(AdapterRateLimitError) as ei:
            await chat.handle_webhook("slack", {"fake": "request"})
        assert ei.value.code == "RATE_LIMITED"
        assert ei.value.retry_after == 42
        await chat.shutdown()

    async def test_validation_error_propagates_for_malformed_payload(
        self, state: MemoryStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=state)
        adapter.handle_webhook = _raise_webhook(
            ValidationError("slack", "Malformed Slack event payload: missing 'event'")
        )
        await chat.initialize()

        with pytest.raises(ValidationError) as ei:
            await chat.handle_webhook("slack", {"broken": True})
        assert ei.value.code == "VALIDATION_ERROR"
        assert "Malformed" in str(ei.value)
        await chat.shutdown()

    async def test_network_error_propagates(self, state: MemoryStateAdapter) -> None:
        chat, adapter = build_chat(state=state)
        adapter.handle_webhook = _raise_webhook(NetworkError("slack", "connection reset"))
        await chat.initialize()

        with pytest.raises(NetworkError):
            await chat.handle_webhook("slack", {"fake": "request"})
        await chat.shutdown()

    async def test_unknown_adapter_raises_chat_error(self, state: MemoryStateAdapter) -> None:
        chat, _adapter = build_chat(state=state)
        await chat.initialize()

        with pytest.raises(ChatError) as ei:
            await chat.handle_webhook("unknown-platform", {"fake": "request"})
        assert ei.value.code == "UNKNOWN_ADAPTER"
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Happy path through handle_webhook
# ---------------------------------------------------------------------------


class TestWebhookHappyPath:
    async def test_webhook_return_value_is_adapter_tuple(self, state: MemoryStateAdapter) -> None:
        """Chat forwards the adapter's ``(status, headers, body)`` verbatim."""

        chat, adapter = build_chat(state=state)
        adapter.handle_webhook = AsyncMock(return_value=(200, {"x-test": "1"}, b"ok"))
        await chat.initialize()

        result = await chat.handle_webhook("slack", {"fake": "request"})
        assert result == (200, {"x-test": "1"}, b"ok")
        adapter.handle_webhook.assert_awaited_once()
        await chat.shutdown()

    async def test_webhook_triggers_lazy_initialization(self, state: MemoryStateAdapter) -> None:
        """First webhook call initialises the state adapter and adapters."""

        chat, adapter = build_chat(state=state)
        adapter.handle_webhook = AsyncMock(return_value=(200, {}, b""))
        # Do NOT call ``chat.initialize()`` — the webhook should trigger it.

        await chat.handle_webhook("slack", {"fake": "request"})
        adapter.initialize.assert_awaited()
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Per-adapter concurrent webhook traffic shares the same state backend
# ---------------------------------------------------------------------------


class TestConcurrentWebhookTraffic:
    async def test_webhooks_for_different_adapters_are_independent(
        self, state: MemoryStateAdapter
    ) -> None:
        chat, adapter = build_chat(state=state)

        recorded: list[str] = []

        async def slack_handler(req: Any, options: Any = None) -> Any:
            recorded.append("slack")
            return (200, {}, b"slack-ok")

        adapter.handle_webhook = slack_handler
        await chat.initialize()

        results = [await chat.handle_webhook("slack", {"i": i}) for i in range(5)]
        assert all(r == (200, {}, b"slack-ok") for r in results)
        assert recorded == ["slack"] * 5
        await chat.shutdown()
