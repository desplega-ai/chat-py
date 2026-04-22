"""Cross-package serialization round-trip tests.

These tests exercise the JSON reviver — the wire-format contract that lets
a :class:`Message` / :class:`Thread` / :class:`Channel` travel through a
workflow engine or cross-language pipeline and come back typed. Integration
test because it reaches across ``chat``, ``chat-adapter-shared``, the mock
adapter, and a real state backend (subscriptions are exercised via the
state).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from chat.markdown import parse_markdown
from chat.message import Message
from chat.mock_adapter import create_mock_adapter, mock_logger
from chat.reviver import object_hook as reviver_object_hook
from chat.types import Author, MessageMetadata
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


def _make_message() -> Message[Any]:
    return Message(
        id="msg-serde-1",
        thread_id="slack:C1:t1",
        text="hello",
        formatted=parse_markdown("hello"),
        raw={"platform": "slack"},
        author=Author(
            user_id="U1",
            user_name="alice",
            full_name="Alice",
            is_bot=False,
            is_me=False,
        ),
        metadata=MessageMetadata(
            date_sent=datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC),
            edited=False,
        ),
    )


class TestMessageRoundTrip:
    def test_message_serializes_and_revives(self) -> None:
        msg = _make_message()
        serialized = msg.to_json()
        assert serialized["_type"] == "chat:Message"

        wire = json.dumps(serialized)
        restored_raw = json.loads(wire, object_hook=reviver_object_hook)
        assert isinstance(restored_raw, Message)
        assert restored_raw.id == msg.id
        assert restored_raw.text == msg.text
        assert restored_raw.author.user_id == "U1"
        assert restored_raw.metadata.date_sent == msg.metadata.date_sent

    async def test_message_survives_state_cache_round_trip(self, state: MemoryStateAdapter) -> None:
        """Serialize → stash in state → fetch → revive."""

        msg = _make_message()
        cache_key = f"history:{msg.thread_id}"
        await state.set(cache_key, msg.to_json())

        stored = await state.get(cache_key)
        assert stored is not None

        wire = json.dumps(stored)
        restored = json.loads(wire, object_hook=reviver_object_hook)
        assert isinstance(restored, Message)
        assert restored.id == msg.id

    async def test_revived_message_can_flow_through_chat_dispatch(
        self, state: MemoryStateAdapter
    ) -> None:
        """A message that came over the wire still dispatches normally."""

        chat, adapter = build_chat(state=state, adapter_name="slack")
        calls: list[Message[Any]] = []

        async def handler(thread: Any, message: Any, ctx: Any = None) -> None:
            calls.append(message)

        chat.on_new_mention(handler)
        await chat.initialize()

        # Round-trip the mention message through JSON.
        msg = _make_message()
        object.__setattr__(msg, "text", "@slack-bot revive me")
        payload = json.dumps(msg.to_json())
        restored = json.loads(payload, object_hook=reviver_object_hook)
        assert isinstance(restored, Message)

        await chat.handle_incoming_message(adapter, restored.thread_id, restored)
        assert len(calls) == 1
        assert calls[0].id == msg.id
        await chat.shutdown()


# ---------------------------------------------------------------------------
# Adapter conformance — all duck-typed adapters expose the same API surface
# ---------------------------------------------------------------------------


class TestAdapterConformance:
    @pytest.mark.parametrize(
        "name",
        ["slack", "teams", "gchat", "discord", "github", "linear", "telegram"],
    )
    def test_mock_adapter_exposes_minimum_api(self, name: str) -> None:
        a = create_mock_adapter(name)

        required_attrs = (
            "name",
            "user_name",
            "initialize",
            "handle_webhook",
            "post_message",
            "encode_thread_id",
            "decode_thread_id",
            "channel_id_from_thread_id",
        )
        for attr in required_attrs:
            assert hasattr(a, attr), f"{name} adapter missing {attr!r}"
