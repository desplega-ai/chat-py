"""Phase 3 dispatch tests for :class:`GoogleChatAdapter`.

Mirrors upstream ``packages/adapter-gchat/src/index.test.ts`` dispatch
branches (HTTP webhook, Pub/Sub push, outbound REST calls, pagination).

All tests use monkeypatched JWT verifiers and a ``SimpleNamespace``-shaped
REST client with ``AsyncMock`` method stubs. Real Google API clients are
never constructed.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state
from chat_adapter_gchat import create_google_chat_adapter
from chat_adapter_gchat.adapter import GoogleChatAdapter

FIXTURE_DIR = Path(__file__).parent / "__fixtures__"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> GoogleChatAdapter:
    monkeypatch.setenv("GOOGLE_CHAT_USE_ADC", "true")
    adp = create_google_chat_adapter()
    adp.bot_user_id = "users/bot"
    # Always bypass JWT verification in unit tests.

    async def _verify_true(_: str | None) -> bool:
        return True

    adp.verify_webhook_bearer = _verify_true  # type: ignore[method-assign]
    adp.verify_pubsub_bearer = _verify_true  # type: ignore[method-assign]
    return adp


def _rest_stub(**method_responses: Any) -> SimpleNamespace:
    """Build a REST client stub supporting attribute-walk dispatch.

    ``method_responses`` is a mapping of dotted path → ``AsyncMock``.
    Example: ``{"spaces.messages.create": AsyncMock(return_value={...})}``.
    """

    # Build a nested SimpleNamespace tree so ``client.spaces.messages.create``
    # reaches the AsyncMock.
    root: Any = SimpleNamespace()
    for dotted, mock in method_responses.items():
        parts = dotted.split(".")
        node = root
        for part in parts[:-1]:
            existing = getattr(node, part, None)
            if existing is None:
                existing = SimpleNamespace()
                setattr(node, part, existing)
            node = existing
        setattr(node, parts[-1], mock)
    return root


def _make_bot(adapter: GoogleChatAdapter) -> Chat:
    return Chat(user_name="bot", adapters={"gchat": adapter}, state=create_mock_state())


# ---------------------------------------------------------------------------
# Cycle 3.2 — MESSAGE event via HTTP dispatches on_new_mention
# ---------------------------------------------------------------------------


async def test_http_message_event_fires_mention_handler(
    adapter: GoogleChatAdapter,
) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(thread: Any, message: Any, context: Any = None) -> None:
        captured["text"] = message.text
        captured["thread_id"] = thread.id
        captured["is_mention"] = message.is_mention
        seen.set()

    bot.on_new_mention(handler)

    body = json.dumps(
        {
            "type": "MESSAGE",
            "space": {"name": "spaces/ABC", "type": "ROOM"},
            "message": {
                "name": "spaces/ABC/messages/m1",
                "text": "@bot hello",
                "argumentText": " hello",  # leading space = mention
                "thread": {"name": "spaces/ABC/threads/T1"},
                "sender": {
                    "name": "users/user1",
                    "displayName": "Alice",
                    "type": "HUMAN",
                },
                "createTime": "2026-04-22T10:00:00Z",
            },
        }
    )
    status, headers, _resp = await bot.handle_webhook(
        "gchat", body.encode(), {"authorization": "Bearer ignored"}
    )
    assert status == 200
    assert headers.get("content-type") == "application/json"
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["text"] == "@bot hello"
    assert captured["is_mention"] is True
    assert captured["thread_id"].startswith("gchat:spaces/ABC:")


async def test_initialize_stores_chat_reference(adapter: GoogleChatAdapter) -> None:
    sentinel = object()
    await adapter.initialize(sentinel)
    assert adapter._chat is sentinel


async def test_webhook_rejects_invalid_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CHAT_USE_ADC", "true")
    adp = create_google_chat_adapter()

    async def _reject(_: str | None) -> bool:
        return False

    adp.verify_webhook_bearer = _reject  # type: ignore[method-assign]

    body = json.dumps({"type": "MESSAGE", "space": {"name": "spaces/X"}, "message": {}})
    status, _h, _b = await adp.handle_webhook(body.encode(), {"authorization": "Bearer bad"})
    assert status == 401


# ---------------------------------------------------------------------------
# Cycle 3.3 — ADDED_TO_SPACE / REMOVED_FROM_SPACE
# ---------------------------------------------------------------------------


async def test_added_to_space_without_pubsub_topic_is_noop(
    adapter: GoogleChatAdapter,
) -> None:
    body = json.dumps(
        {
            "type": "ADDED_TO_SPACE",
            "space": {"name": "spaces/ABC", "type": "ROOM"},
        }
    )
    status, _h, _b = await adapter.handle_webhook(body.encode(), {"authorization": "Bearer x"})
    assert status == 200


async def test_added_to_space_with_pubsub_creates_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CHAT_USE_ADC", "true")
    monkeypatch.setenv("GOOGLE_CHAT_PUBSUB_TOPIC", "projects/p/topics/t")
    adp = create_google_chat_adapter()

    async def _verify_true(_: str | None) -> bool:
        return True

    adp.verify_webhook_bearer = _verify_true  # type: ignore[method-assign]

    # Patch the subscription call so we don't hit the real Workspace Events API.
    create_calls: list[Any] = []

    async def fake_create(options: Any, auth: Any) -> Any:
        create_calls.append({"options": options, "auth": auth})
        return {"name": "subscriptions/sub-1", "expireTime": "2026-04-23T00:00:00Z"}

    monkeypatch.setattr(
        "chat_adapter_gchat.workspace_events.create_space_subscription", fake_create
    )

    body = json.dumps(
        {
            "type": "ADDED_TO_SPACE",
            "space": {"name": "spaces/ABC", "type": "ROOM"},
        }
    )
    status, _h, _b = await adp.handle_webhook(body.encode(), {"authorization": "Bearer x"})
    assert status == 200
    assert len(create_calls) == 1
    assert create_calls[0]["options"]["spaceName"] == "spaces/ABC"
    assert create_calls[0]["options"]["pubsubTopic"] == "projects/p/topics/t"


async def test_removed_from_space_event_is_acked(
    adapter: GoogleChatAdapter,
) -> None:
    bot = _make_bot(adapter)
    # Trigger initialization so adapter._chat is wired.
    await bot.initialize()

    body = json.dumps(
        {
            "type": "REMOVED_FROM_SPACE",
            "space": {"name": "spaces/ABC", "type": "ROOM"},
        }
    )
    status, _h, _b = await bot.handle_webhook("gchat", body.encode(), {"authorization": "Bearer x"})
    assert status == 200


# ---------------------------------------------------------------------------
# Cycle 3.4 — Pub/Sub envelope routes same payload
# ---------------------------------------------------------------------------


async def test_pubsub_envelope_routes_message_to_same_dispatch(
    adapter: GoogleChatAdapter,
) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(thread: Any, message: Any, context: Any = None) -> None:
        captured["text"] = message.text
        captured["thread_id"] = thread.id
        seen.set()

    bot.on_new_mention(handler)

    inner_event = {
        "message": {
            "name": "spaces/ABC/messages/m1",
            "text": "@bot hi",
            "argumentText": " hi",
            "thread": {"name": "spaces/ABC/threads/T1"},
            "space": {"name": "spaces/ABC", "type": "ROOM"},
            "sender": {
                "name": "users/user1",
                "displayName": "Alice",
                "type": "HUMAN",
            },
            "createTime": "2026-04-22T10:00:00Z",
        }
    }
    encoded = base64.b64encode(json.dumps(inner_event).encode("utf-8")).decode("ascii")

    envelope = json.dumps(
        {
            "message": {
                "data": encoded,
                "attributes": {
                    "ce-type": "google.workspace.chat.message.v1.created",
                    "ce-subject": "//chat.googleapis.com/spaces/ABC",
                    "ce-time": "2026-04-22T10:00:00Z",
                },
                "messageId": "pub-1",
            },
            "subscription": "projects/p/subscriptions/s",
        }
    )

    status, _h, _b = await bot.handle_webhook(
        "gchat",
        envelope.encode(),
        {"authorization": "Bearer pubsub", "content-type": "application/json"},
    )
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["text"] == "@bot hi"
    assert captured["thread_id"].startswith("gchat:spaces/ABC:")


async def test_pubsub_envelope_is_detected_vs_http(
    adapter: GoogleChatAdapter,
) -> None:
    """Direct HTTP payloads don't carry ``{"message": {"data": ...}}`` shape."""

    from chat_adapter_gchat.pubsub import is_pubsub_envelope

    assert is_pubsub_envelope({"message": {"data": "xyz", "attributes": {}}}) is True
    assert is_pubsub_envelope({"message": {"attributes": {"ce-type": "x"}}}) is True
    assert is_pubsub_envelope({"type": "MESSAGE", "message": {"text": "x"}}) is False
    assert is_pubsub_envelope({"type": "ADDED_TO_SPACE"}) is False


# ---------------------------------------------------------------------------
# Cycle 3.5 — post_message posts a Card v2 via REST
# ---------------------------------------------------------------------------


async def test_post_message_sends_card_v2_snapshot(
    adapter: GoogleChatAdapter,
) -> None:
    from chat import Card, Section, Text

    create_mock = AsyncMock(
        return_value={
            "name": "spaces/ABC/messages/m-new",
            "thread": {"name": "spaces/ABC/threads/T1"},
        }
    )
    adapter._rest_client = _rest_stub(**{"spaces.messages.create": create_mock})

    thread_id = adapter.encode_thread_id(
        {"spaceName": "spaces/ABC", "threadName": "spaces/ABC/threads/T1"}
    )
    card = {
        "type": "card",
        "title": "Echo",
        "subtitle": "from chat-py",
        "children": [Section(children=[Text("Hello from **chat-py**")])],
    }
    # The ``Card()`` builder returns a dict — we mimic it inline so the test
    # doesn't depend on CardElement's concrete repr.
    card = Card(
        title="Echo",
        subtitle="from chat-py",
        children=[Section(children=[Text("Hello from **chat-py**")])],
    )

    result = await adapter.post_message(thread_id, card)

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    assert call_kwargs["parent"] == "spaces/ABC"
    body = call_kwargs["body"]
    assert "cardsV2" in body
    assert body["thread"]["name"] == "spaces/ABC/threads/T1"

    expected = json.loads((FIXTURE_DIR / "card_v2_snapshot.json").read_text())
    # The endpointUrl is empty in the stub path; strip cardId divergence by
    # feeding the fixture's cardId through as the expected value.
    actual_card = body["cardsV2"][0]
    # Build the expected shape — the snapshot doesn't include a cardId field
    # when the adapter didn't pass one, so compare the ``card`` portion only.
    assert actual_card["card"]["header"] == expected["card"]["header"]
    assert actual_card["card"]["sections"] == expected["card"]["sections"]
    assert result["id"] == "spaces/ABC/messages/m-new"


async def test_post_message_sends_plain_text(adapter: GoogleChatAdapter) -> None:
    create_mock = AsyncMock(
        return_value={"name": "spaces/ABC/messages/m2", "thread": {"name": "spaces/ABC/threads/T1"}}
    )
    adapter._rest_client = _rest_stub(**{"spaces.messages.create": create_mock})

    thread_id = adapter.encode_thread_id({"spaceName": "spaces/ABC"})
    result = await adapter.post_message(thread_id, "hello world")
    create_mock.assert_awaited_once()
    body = create_mock.await_args.kwargs["body"]
    assert body["text"] == "hello world"
    assert result["id"] == "spaces/ABC/messages/m2"


# ---------------------------------------------------------------------------
# Cycle 3.6 — edit_message, delete_message
# ---------------------------------------------------------------------------


async def test_edit_message_uses_update_mask(adapter: GoogleChatAdapter) -> None:
    update_mock = AsyncMock(return_value={"name": "spaces/ABC/messages/m1"})
    adapter._rest_client = _rest_stub(**{"spaces.messages.update": update_mock})

    thread_id = adapter.encode_thread_id({"spaceName": "spaces/ABC"})
    result = await adapter.edit_message(
        thread_id, "spaces/ABC/messages/m1", {"markdown": "new text"}
    )
    update_mock.assert_awaited_once()
    call_kwargs = update_mock.await_args.kwargs
    assert call_kwargs["name"] == "spaces/ABC/messages/m1"
    assert call_kwargs["updateMask"] in ("text", "text,cards_v2")
    assert call_kwargs["body"]["text"] == "new text"
    assert result["id"] == "spaces/ABC/messages/m1"


async def test_delete_message_calls_delete(adapter: GoogleChatAdapter) -> None:
    delete_mock = AsyncMock(return_value=None)
    adapter._rest_client = _rest_stub(**{"spaces.messages.delete": delete_mock})

    thread_id = adapter.encode_thread_id({"spaceName": "spaces/ABC"})
    await adapter.delete_message(thread_id, "spaces/ABC/messages/m1")
    delete_mock.assert_awaited_once_with(name="spaces/ABC/messages/m1")


# ---------------------------------------------------------------------------
# Cycle 3.7 — add_reaction / remove_reaction
# ---------------------------------------------------------------------------


async def test_add_reaction_maps_well_known_emoji(adapter: GoogleChatAdapter) -> None:
    create_mock = AsyncMock(return_value={"name": "spaces/ABC/messages/m1/reactions/r1"})
    adapter._rest_client = _rest_stub(**{"spaces.messages.reactions.create": create_mock})

    thread_id = adapter.encode_thread_id({"spaceName": "spaces/ABC"})
    await adapter.add_reaction(thread_id, "spaces/ABC/messages/m1", "thumbs_up")
    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    assert call_kwargs["parent"] == "spaces/ABC/messages/m1"
    # thumbs_up → 👍 via DEFAULT_EMOJI_MAP
    assert call_kwargs["body"]["emoji"]["unicode"] == "\U0001f44d"


async def test_remove_reaction_lists_then_deletes(
    adapter: GoogleChatAdapter,
) -> None:
    list_mock = AsyncMock(
        return_value={
            "reactions": [
                {
                    "name": "spaces/ABC/messages/m1/reactions/r1",
                    "emoji": {"unicode": "\U0001f44d"},
                }
            ]
        }
    )
    delete_mock = AsyncMock(return_value=None)
    adapter._rest_client = _rest_stub(
        **{
            "spaces.messages.reactions.list": list_mock,
            "spaces.messages.reactions.delete": delete_mock,
        }
    )

    thread_id = adapter.encode_thread_id({"spaceName": "spaces/ABC"})
    await adapter.remove_reaction(thread_id, "spaces/ABC/messages/m1", "thumbs_up")
    list_mock.assert_awaited_once()
    delete_mock.assert_awaited_once_with(name="spaces/ABC/messages/m1/reactions/r1")


# ---------------------------------------------------------------------------
# Cycle 3.8 — fetch_messages pagination
# ---------------------------------------------------------------------------


async def test_fetch_messages_honors_cursor(adapter: GoogleChatAdapter) -> None:
    list_mock = AsyncMock(
        return_value={
            "messages": [
                {"name": "spaces/ABC/messages/m1", "text": "one"},
                {"name": "spaces/ABC/messages/m2", "text": "two"},
            ],
            "nextPageToken": "next-42",
        }
    )
    adapter._rest_client = _rest_stub(**{"spaces.messages.list": list_mock})

    thread_id = adapter.encode_thread_id(
        {"spaceName": "spaces/ABC", "threadName": "spaces/ABC/threads/T1"}
    )
    result = await adapter.fetch_messages(thread_id, {"cursor": "prev", "limit": 50})
    list_mock.assert_awaited_once()
    kwargs = list_mock.await_args.kwargs
    assert kwargs["parent"] == "spaces/ABC"
    assert kwargs["pageToken"] == "prev"
    assert kwargs["pageSize"] == 50
    assert kwargs["filter"] == 'thread.name = "spaces/ABC/threads/T1"'
    assert len(result["messages"]) == 2
    assert result["nextCursor"] == "next-42"


async def test_fetch_messages_no_next_cursor_returns_none(
    adapter: GoogleChatAdapter,
) -> None:
    list_mock = AsyncMock(return_value={"messages": [], "nextPageToken": ""})
    adapter._rest_client = _rest_stub(**{"spaces.messages.list": list_mock})

    thread_id = adapter.encode_thread_id({"spaceName": "spaces/ABC"})
    result = await adapter.fetch_messages(thread_id)
    assert result["nextCursor"] is None
    assert result["messages"] == []
