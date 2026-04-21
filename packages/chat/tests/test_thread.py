"""Tests for :class:`chat.thread.ThreadImpl`.

Python port of ``packages/chat/src/thread.test.ts`` — covers state, post,
messages/all_messages iterators, subscriptions, refresh, serialization,
post_ephemeral, schedule, and mention utilities. Plan, streaming-detail,
and JSX tests are deferred to part B.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat.message import Message
from chat.mock_adapter import create_mock_adapter, create_mock_state, create_test_message
from chat.thread import ThreadImpl
from chat.types import Author, EphemeralMessage

# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------


@pytest.fixture
def mock_adapter() -> Any:
    return create_mock_adapter("slack")


@pytest.fixture
def mock_state() -> Any:
    return create_mock_state()


@pytest.fixture
def thread(mock_adapter: Any, mock_state: Any) -> ThreadImpl[Any]:
    return ThreadImpl[Any](
        id="slack:C123:1234.5678",
        channel_id="C123",
        adapter=mock_adapter,
        state_adapter=mock_state,
    )


# ----------------------------------------------------------------------------
# Per-thread state
# ----------------------------------------------------------------------------


class TestPerThreadState:
    async def test_returns_none_when_no_state_set(self, thread: ThreadImpl[Any]) -> None:
        assert await thread.state is None

    async def test_returns_stored_state(self, thread: ThreadImpl[Any], mock_state: Any) -> None:
        mock_state.cache["thread-state:slack:C123:1234.5678"] = {"ai_mode": True}
        assert await thread.state == {"ai_mode": True}

    async def test_sets_state_and_retrieves_it(self, thread: ThreadImpl[Any]) -> None:
        await thread.set_state({"ai_mode": True})
        assert await thread.state == {"ai_mode": True}

    async def test_merges_state_by_default(self, thread: ThreadImpl[Any]) -> None:
        await thread.set_state({"ai_mode": True})
        await thread.set_state({"counter": 5})
        assert await thread.state == {"ai_mode": True, "counter": 5}

    async def test_overwrites_existing_keys_when_merging(self, thread: ThreadImpl[Any]) -> None:
        await thread.set_state({"ai_mode": True, "counter": 1})
        await thread.set_state({"counter": 10})
        assert await thread.state == {"ai_mode": True, "counter": 10}

    async def test_replaces_entire_state_on_replace(self, thread: ThreadImpl[Any]) -> None:
        await thread.set_state({"ai_mode": True, "counter": 5})
        await thread.set_state({"counter": 10}, {"replace": True})
        state = await thread.state
        assert state == {"counter": 10}
        assert "ai_mode" not in state

    async def test_uses_correct_key_prefix(self, thread: ThreadImpl[Any], mock_state: Any) -> None:
        await thread.set_state({"ai_mode": True})
        mock_state.set.assert_called_with(
            "thread-state:slack:C123:1234.5678", {"ai_mode": True}, 30 * 24 * 60 * 60 * 1000
        )

    async def test_calls_get_with_correct_key(
        self, thread: ThreadImpl[Any], mock_state: Any
    ) -> None:
        await thread.state
        mock_state.get.assert_called_with("thread-state:slack:C123:1234.5678")


# ----------------------------------------------------------------------------
# post() with different message formats
# ----------------------------------------------------------------------------


class TestPostFormats:
    async def test_posts_string_message(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        result = await thread.post("Hello world")
        mock_adapter.post_message.assert_called_with("slack:C123:1234.5678", "Hello world")
        assert result.text == "Hello world"
        assert result.id == "msg-1"

    async def test_posts_raw_message(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        result = await thread.post({"raw": "raw text"})
        mock_adapter.post_message.assert_called_with("slack:C123:1234.5678", {"raw": "raw text"})
        assert result.text == "raw text"

    async def test_posts_markdown_message(self, thread: ThreadImpl[Any]) -> None:
        result = await thread.post({"markdown": "**bold** text"})
        assert result.text == "bold text"

    async def test_sets_correct_author(self, thread: ThreadImpl[Any]) -> None:
        result = await thread.post("Hello")
        assert result.author.is_bot is True
        assert result.author.is_me is True
        assert result.author.user_id == "self"
        assert result.author.user_name == "slack-bot"

    async def test_uses_thread_id_override(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        mock_adapter.post_message = AsyncMock(
            return_value={"id": "msg-2", "threadId": "slack:C123:new-thread-id", "raw": {}}
        )
        result = await thread.post("Hello")
        assert result.thread_id == "slack:C123:new-thread-id"


# ----------------------------------------------------------------------------
# all_messages iterator
# ----------------------------------------------------------------------------


class TestAllMessagesIterator:
    async def test_iterates_chronological(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        messages = [
            create_test_message("msg-1", "First"),
            create_test_message("msg-2", "Second"),
            create_test_message("msg-3", "Third"),
        ]
        mock_adapter.fetch_messages = AsyncMock(
            return_value={"messages": messages, "nextCursor": None}
        )

        collected = [m async for m in thread.all_messages]
        assert [m.text for m in collected] == ["First", "Second", "Third"]

    async def test_uses_forward_direction(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        mock_adapter.fetch_messages = AsyncMock(return_value={"messages": [], "nextCursor": None})
        async for _ in thread.all_messages:
            pass
        options = mock_adapter.fetch_messages.call_args.args[1]
        assert options["direction"] == "forward"
        assert options["limit"] == 100

    async def test_paginates(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        page1 = [create_test_message("m1", "P1-1"), create_test_message("m2", "P1-2")]
        page2 = [create_test_message("m3", "P2-1"), create_test_message("m4", "P2-2")]
        page3 = [create_test_message("m5", "P3-1")]

        call_count = 0

        async def fake(_tid: str, options: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                assert "cursor" not in options
                return {"messages": page1, "nextCursor": "cursor-1"}
            if call_count == 2:
                assert options["cursor"] == "cursor-1"
                return {"messages": page2, "nextCursor": "cursor-2"}
            assert options["cursor"] == "cursor-2"
            return {"messages": page3, "nextCursor": None}

        mock_adapter.fetch_messages = AsyncMock(side_effect=fake)

        collected = [m async for m in thread.all_messages]
        assert [m.text for m in collected] == [
            "P1-1",
            "P1-2",
            "P2-1",
            "P2-2",
            "P3-1",
        ]
        assert mock_adapter.fetch_messages.call_count == 3

    async def test_empty_thread(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        mock_adapter.fetch_messages = AsyncMock(return_value={"messages": [], "nextCursor": None})
        collected = [m async for m in thread.all_messages]
        assert collected == []
        assert mock_adapter.fetch_messages.call_count == 1

    async def test_stops_on_empty_with_cursor(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        mock_adapter.fetch_messages = AsyncMock(
            return_value={"messages": [], "nextCursor": "some-cursor"}
        )
        collected = [m async for m in thread.all_messages]
        assert collected == []
        assert mock_adapter.fetch_messages.call_count == 1

    async def test_early_break(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        page1 = [create_test_message("m1", "M1"), create_test_message("m2", "M2")]
        mock_adapter.fetch_messages = AsyncMock(
            return_value={"messages": page1, "nextCursor": "more-available"}
        )

        collected: list[Message[Any]] = []
        async for m in thread.all_messages:
            collected.append(m)
            if m.id == "m1":
                break

        assert len(collected) == 1
        assert mock_adapter.fetch_messages.call_count == 1

    async def test_reusable(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        messages = [create_test_message("m1", "Test")]
        mock_adapter.fetch_messages = AsyncMock(
            return_value={"messages": messages, "nextCursor": None}
        )

        first = [m async for m in thread.all_messages]
        second = [m async for m in thread.all_messages]
        assert len(first) == 1
        assert len(second) == 1


# ----------------------------------------------------------------------------
# messages iterator (backward)
# ----------------------------------------------------------------------------


class TestMessagesIterator:
    async def test_newest_first(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        messages = [
            create_test_message("m1", "First"),
            create_test_message("m2", "Second"),
            create_test_message("m3", "Third"),
        ]
        mock_adapter.fetch_messages = AsyncMock(
            return_value={"messages": messages, "nextCursor": None}
        )

        collected = [m async for m in thread.messages]
        assert [m.text for m in collected] == ["Third", "Second", "First"]

    async def test_uses_backward_direction(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        mock_adapter.fetch_messages = AsyncMock(return_value={"messages": [], "nextCursor": None})
        async for _ in thread.messages:
            pass
        options = mock_adapter.fetch_messages.call_args.args[1]
        assert options["direction"] == "backward"


# ----------------------------------------------------------------------------
# refresh
# ----------------------------------------------------------------------------


class TestRefresh:
    async def test_updates_recent_messages(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        messages = [create_test_message("m1", "Hi")]
        mock_adapter.fetch_messages = AsyncMock(
            return_value={"messages": messages, "nextCursor": None}
        )

        await thread.refresh()
        assert thread.recent_messages == messages

    async def test_fetches_with_limit_50(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        mock_adapter.fetch_messages = AsyncMock(return_value={"messages": [], "nextCursor": None})
        await thread.refresh()
        options = mock_adapter.fetch_messages.call_args.args[1]
        assert options["limit"] == 50

    async def test_empty_clears_recent_messages(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        thread.recent_messages = [create_test_message("stale", "stale")]
        mock_adapter.fetch_messages = AsyncMock(return_value={"messages": [], "nextCursor": None})
        await thread.refresh()
        assert thread.recent_messages == []


# ----------------------------------------------------------------------------
# subscribe / unsubscribe / is_subscribed
# ----------------------------------------------------------------------------


class TestSubscriptions:
    async def test_subscribe_via_state_adapter(
        self, thread: ThreadImpl[Any], mock_state: Any
    ) -> None:
        await thread.subscribe()
        mock_state.subscribe.assert_called_with("slack:C123:1234.5678")

    async def test_calls_on_thread_subscribe_when_available(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        mock_adapter.on_thread_subscribe = AsyncMock()
        await thread.subscribe()
        mock_adapter.on_thread_subscribe.assert_called_with("slack:C123:1234.5678")

    async def test_no_error_without_on_thread_subscribe(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        if hasattr(mock_adapter, "on_thread_subscribe"):
            delattr(mock_adapter, "on_thread_subscribe")
        await thread.subscribe()  # should not raise

    async def test_unsubscribe(self, thread: ThreadImpl[Any], mock_state: Any) -> None:
        await thread.unsubscribe()
        mock_state.unsubscribe.assert_called_with("slack:C123:1234.5678")

    async def test_is_subscribed_false_by_default(self, thread: ThreadImpl[Any]) -> None:
        assert await thread.is_subscribed() is False

    async def test_is_subscribed_true_after_subscribe(self, thread: ThreadImpl[Any]) -> None:
        await thread.subscribe()
        assert await thread.is_subscribed() is True

    async def test_is_subscribed_false_after_unsubscribe(self, thread: ThreadImpl[Any]) -> None:
        await thread.subscribe()
        await thread.unsubscribe()
        assert await thread.is_subscribed() is False

    async def test_is_subscribed_short_circuits_with_context(
        self, mock_adapter: Any, mock_state: Any
    ) -> None:
        thread = ThreadImpl[Any](
            id="t-ctx",
            channel_id="C123",
            adapter=mock_adapter,
            state_adapter=mock_state,
            is_subscribed_context=True,
        )
        assert await thread.is_subscribed() is True


# ----------------------------------------------------------------------------
# recent_messages getter/setter
# ----------------------------------------------------------------------------


class TestRecentMessages:
    def test_empty_by_default(self, thread: ThreadImpl[Any]) -> None:
        assert thread.recent_messages == []

    def test_initial_message(self, mock_adapter: Any, mock_state: Any) -> None:
        initial = create_test_message("init", "hi")
        thread = ThreadImpl[Any](
            id="t",
            channel_id="C123",
            adapter=mock_adapter,
            state_adapter=mock_state,
            initial_message=initial,
        )
        assert thread.recent_messages == [initial]

    def test_set_recent_messages(self, thread: ThreadImpl[Any]) -> None:
        new_list = [create_test_message("x", "x")]
        thread.recent_messages = new_list
        assert thread.recent_messages == new_list


# ----------------------------------------------------------------------------
# startTyping
# ----------------------------------------------------------------------------


class TestStartTyping:
    async def test_calls_adapter(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        await thread.start_typing()
        mock_adapter.start_typing.assert_called_with("slack:C123:1234.5678", None)

    async def test_passes_status(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        await thread.start_typing("thinking")
        mock_adapter.start_typing.assert_called_with("slack:C123:1234.5678", "thinking")


# ----------------------------------------------------------------------------
# mentionUser
# ----------------------------------------------------------------------------


class TestMentionUser:
    def test_formats_mention(self, thread: ThreadImpl[Any]) -> None:
        assert thread.mention_user("U123") == "<@U123>"

    def test_various_ids(self, thread: ThreadImpl[Any]) -> None:
        assert thread.mention_user("U456") == "<@U456>"
        assert thread.mention_user("UABCD") == "<@UABCD>"


# ----------------------------------------------------------------------------
# create_sent_message_from_message
# ----------------------------------------------------------------------------


class TestCreateSentMessageFromMessage:
    def test_wraps_fields(self, thread: ThreadImpl[Any]) -> None:
        msg = create_test_message("m1", "Hello")
        sent = thread.create_sent_message_from_message(msg)
        assert sent.id == msg.id
        assert sent.thread_id == msg.thread_id
        assert sent.text == msg.text
        assert sent.author == msg.author

    async def test_edit_delegates_to_adapter(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        msg = create_test_message("m1", "original")
        sent = thread.create_sent_message_from_message(msg)
        await sent.edit("edited")
        mock_adapter.edit_message.assert_called()

    async def test_delete_delegates_to_adapter(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        msg = create_test_message("m1", "x")
        sent = thread.create_sent_message_from_message(msg)
        await sent.delete()
        mock_adapter.delete_message.assert_called_with(msg.thread_id, "m1")

    async def test_add_reaction(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        msg = create_test_message("m1", "x")
        sent = thread.create_sent_message_from_message(msg)
        await sent.add_reaction("thumbs_up")
        mock_adapter.add_reaction.assert_called()

    async def test_remove_reaction(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        msg = create_test_message("m1", "x")
        sent = thread.create_sent_message_from_message(msg)
        await sent.remove_reaction("thumbs_up")
        mock_adapter.remove_reaction.assert_called()

    def test_preserves_is_mention(self, thread: ThreadImpl[Any]) -> None:
        msg = create_test_message("m1", "hi", is_mention=True)
        sent = thread.create_sent_message_from_message(msg)
        assert sent.is_mention is True

    def test_to_json_delegates(self, thread: ThreadImpl[Any]) -> None:
        msg = create_test_message("m1", "Hello")
        sent = thread.create_sent_message_from_message(msg)
        json1 = sent.to_json()
        json2 = msg.to_json()
        assert json1["text"] == json2["text"]
        assert json1["id"] == json2["id"]


# ----------------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------------


class TestSerialization:
    def test_to_json(self, mock_adapter: Any, mock_state: Any) -> None:
        thread = ThreadImpl[Any](
            id="slack:C123:1234.5678",
            channel_id="C123",
            adapter=mock_adapter,
            state_adapter=mock_state,
            is_dm=True,
        )
        json_data = thread.to_json()
        assert json_data == {
            "_type": "chat:Thread",
            "id": "slack:C123:1234.5678",
            "channelId": "C123",
            "channelVisibility": "unknown",
            "isDM": True,
            "adapterName": "slack",
        }

    def test_to_json_with_current_message(self, mock_adapter: Any, mock_state: Any) -> None:
        msg = create_test_message("m1", "Current")
        thread = ThreadImpl[Any](
            id="slack:C123:1234.5678",
            channel_id="C123",
            adapter=mock_adapter,
            state_adapter=mock_state,
            current_message=msg,
        )
        json_data = thread.to_json()
        current = json_data["currentMessage"]
        assert current["_type"] == "chat:Message"
        assert current["text"] == "Current"

    def test_from_json_with_explicit_adapter(self, mock_adapter: Any) -> None:
        data = {
            "_type": "chat:Thread",
            "id": "slack:C123:1234.5678",
            "channelId": "C123",
            "isDM": False,
            "adapterName": "slack",
        }
        thread = ThreadImpl.from_json(data, mock_adapter)
        assert thread.id == "slack:C123:1234.5678"
        assert thread.channel_id == "C123"
        assert thread.is_dm is False
        assert thread.adapter is mock_adapter

    def test_from_json_with_current_message(self, mock_adapter: Any) -> None:
        msg = create_test_message("m1", "Serialized")
        serialized_msg = msg.to_json()
        data = {
            "_type": "chat:Thread",
            "id": "slack:C123:1234.5678",
            "channelId": "C123",
            "currentMessage": serialized_msg,
            "isDM": False,
            "adapterName": "slack",
        }
        thread = ThreadImpl.from_json(data, mock_adapter)
        round_tripped = thread.to_json()
        assert round_tripped["currentMessage"]["text"] == "Serialized"


# ----------------------------------------------------------------------------
# SentMessage.toJSON from post
# ----------------------------------------------------------------------------


class TestSentMessageJson:
    async def test_serializes_sent_message(self, thread: ThreadImpl[Any]) -> None:
        result = await thread.post("Hello world")
        json_data = result.to_json()
        assert json_data["_type"] == "chat:Message"
        assert json_data["text"] == "Hello world"
        assert json_data["author"]["isBot"] is True
        assert json_data["author"]["isMe"] is True


# ----------------------------------------------------------------------------
# schedule()
# ----------------------------------------------------------------------------


class TestSchedule:
    async def test_raises_not_implemented_when_adapter_missing(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        if hasattr(mock_adapter, "schedule_message"):
            delattr(mock_adapter, "schedule_message")
        future = datetime(2030, 1, 1, tzinfo=UTC)
        with pytest.raises(ChatNotImplementedError) as exc:
            await thread.schedule("hi", {"postAt": future})
        assert exc.value.feature == "scheduling"

    async def test_delegates_to_adapter(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        future = datetime(2030, 1, 1, tzinfo=UTC)

        async def noop() -> None:
            return None

        sched = type(
            "Sched",
            (),
            {
                "scheduled_message_id": "Q123",
                "channel_id": "C123",
                "post_at": future,
                "raw": {"ok": True},
                "cancel": noop,
            },
        )()
        mock_adapter.schedule_message = AsyncMock(return_value=sched)

        result = await thread.schedule("Hello", {"postAt": future})
        mock_adapter.schedule_message.assert_called_with(
            "slack:C123:1234.5678", "Hello", {"postAt": future}
        )
        assert result is sched

    async def test_passes_through_string(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        mock_adapter.schedule_message = AsyncMock(return_value={})
        future = datetime(2030, 1, 1, tzinfo=UTC)
        await thread.schedule("hi", {"postAt": future})
        assert mock_adapter.schedule_message.call_args.args[1] == "hi"

    async def test_passes_markdown(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        mock_adapter.schedule_message = AsyncMock(return_value={})
        future = datetime(2030, 1, 1, tzinfo=UTC)
        await thread.schedule({"markdown": "**hi**"}, {"postAt": future})
        assert mock_adapter.schedule_message.call_args.args[1] == {"markdown": "**hi**"}


# ----------------------------------------------------------------------------
# post_ephemeral
# ----------------------------------------------------------------------------


class TestPostEphemeral:
    async def test_uses_adapter_post_ephemeral(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        mock_adapter.post_ephemeral = AsyncMock(
            return_value=EphemeralMessage(id="e1", thread_id=thread.id, used_fallback=False, raw={})
        )
        result = await thread.post_ephemeral("U123", "hey", {"fallbackToDM": False})
        mock_adapter.post_ephemeral.assert_called_with(thread.id, "U123", "hey")
        assert result is not None
        assert result.id == "e1"

    async def test_extracts_user_id_from_author(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        mock_adapter.post_ephemeral = AsyncMock(
            return_value=EphemeralMessage(id="e2", thread_id=thread.id, used_fallback=False, raw={})
        )
        author = Author(
            user_id="U-AUTHOR",
            user_name="a",
            full_name="A",
            is_bot=False,
            is_me=False,
        )
        await thread.post_ephemeral(author, "hey", {"fallbackToDM": False})
        mock_adapter.post_ephemeral.assert_called_with(thread.id, "U-AUTHOR", "hey")

    async def test_fallback_to_dm_when_no_ephemeral(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        if hasattr(mock_adapter, "post_ephemeral"):
            delattr(mock_adapter, "post_ephemeral")
        mock_adapter.open_dm = AsyncMock(return_value="slack:D1:xxxx")
        mock_adapter.post_message = AsyncMock(return_value={"id": "dm-1", "raw": {"dm": True}})
        result = await thread.post_ephemeral("U123", "hey", {"fallbackToDM": True})
        assert result is not None
        assert result.used_fallback is True
        assert result.thread_id == "slack:D1:xxxx"
        mock_adapter.open_dm.assert_called_with("U123")

    async def test_returns_none_no_fallback_no_ephemeral(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        if hasattr(mock_adapter, "post_ephemeral"):
            delattr(mock_adapter, "post_ephemeral")
        result = await thread.post_ephemeral("U123", "hey", {"fallbackToDM": False})
        assert result is None

    async def test_returns_none_no_ephemeral_no_open_dm(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        if hasattr(mock_adapter, "post_ephemeral"):
            delattr(mock_adapter, "post_ephemeral")
        if hasattr(mock_adapter, "open_dm"):
            delattr(mock_adapter, "open_dm")
        result = await thread.post_ephemeral("U123", "hey", {"fallbackToDM": True})
        assert result is None


# ----------------------------------------------------------------------------
# Streaming (simplified: accumulate + post)
# ----------------------------------------------------------------------------


class TestStreaming:
    async def test_native_stream(self, thread: ThreadImpl[Any], mock_adapter: Any) -> None:
        async def fake_stream(_tid: str, stream: Any, _options: Any) -> Any:
            # Consume the wrapped stream so the outer code can accumulate text.
            async for _ in stream:
                pass
            return {"id": "stream-1", "threadId": thread.id, "raw": {}}

        mock_adapter.stream = AsyncMock(side_effect=fake_stream)

        async def gen() -> Any:
            for chunk in ["Hello ", "world"]:
                yield chunk

        sent = await thread.post(gen())
        assert sent.id == "stream-1"
        assert sent.text == "Hello world"

    async def test_fallback_stream_posts_on_completion(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        if hasattr(mock_adapter, "stream"):
            delattr(mock_adapter, "stream")
        mock_adapter.post_message = AsyncMock(
            return_value={"id": "msg-1", "threadId": thread.id, "raw": {}}
        )

        async def gen() -> Any:
            yield "Hello"
            yield " world"

        sent = await thread.post(gen())
        mock_adapter.post_message.assert_called_with(thread.id, {"markdown": "Hello world"})
        assert sent.text == "Hello world"


# ----------------------------------------------------------------------------
# Channel property
# ----------------------------------------------------------------------------


class TestChannelProperty:
    def test_channel_is_lazy_and_cached(self, thread: ThreadImpl[Any]) -> None:
        ch1 = thread.channel
        ch2 = thread.channel
        assert ch1 is ch2

    def test_channel_derives_id_via_adapter(
        self, thread: ThreadImpl[Any], mock_adapter: Any
    ) -> None:
        mock_adapter.channel_id_from_thread_id = lambda tid: "derived-id"
        ch = thread.channel
        assert ch.id == "derived-id"
