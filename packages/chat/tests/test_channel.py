"""Tests for :mod:`chat.channel` — port of ``channel.test.ts``.

JSX/Card and Thread-dependent tests are deferred to part B of the port.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from chat.channel import ChannelImpl, derive_channel_id
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat.markdown import paragraph, root, text
from chat.mock_adapter import create_mock_adapter, create_mock_state, create_test_message
from chat.types import Author, ScheduledMessage


class TestBasicProperties:
    def test_id_adapter_isDM_name(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        assert channel.id == "slack:C123"
        assert channel.adapter is adapter
        assert channel.is_dm is False
        assert channel.name is None

    def test_is_dm_config(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:D123", adapter=adapter, state_adapter=state, is_dm=True)
        assert channel.is_dm is True


@pytest.fixture
def channel() -> ChannelImpl[Any]:
    adapter = create_mock_adapter()
    state = create_mock_state()
    return ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)


class TestStateManagement:
    async def test_null_when_no_state(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.state
        assert result is None

    async def test_set_and_retrieve(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.set_state({"topic": "general"})
        assert await channel.state == {"topic": "general"}

    async def test_merge_by_default(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.set_state({"topic": "general"})
        await channel.set_state({"count": 5})
        assert await channel.state == {"topic": "general", "count": 5}

    async def test_replace_option(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.set_state({"topic": "general", "count": 5})
        await channel.set_state({"count": 10}, {"replace": True})
        assert await channel.state == {"count": 10}

    async def test_channel_state_key_prefix(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.set_state({"topic": "general"})
        # set was called with key="channel-state:slack:C123"
        call = state.set.call_args
        assert call.args[0] == "channel-state:slack:C123"
        assert call.args[1] == {"topic": "general"}


class TestMessagesIterator:
    async def test_uses_fetch_channel_messages(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        messages = [
            create_test_message("msg-1", "Oldest"),
            create_test_message("msg-2", "Middle"),
            create_test_message("msg-3", "Newest"),
        ]
        adapter.fetch_channel_messages = AsyncMock(
            return_value={"messages": messages, "nextCursor": None}
        )

        collected: list[Any] = [m async for m in channel.messages]
        assert len(collected) == 3
        assert collected[0].text == "Newest"
        assert collected[1].text == "Middle"
        assert collected[2].text == "Oldest"

        adapter.fetch_channel_messages.assert_called_with("slack:C123", {"direction": "backward"})

    async def test_falls_back_to_fetch_messages(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.fetch_channel_messages = None
        messages = [
            create_test_message("msg-1", "First"),
            create_test_message("msg-2", "Second"),
        ]
        adapter.fetch_messages = AsyncMock(return_value={"messages": messages, "nextCursor": None})
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        collected = [m async for m in channel.messages]
        assert [m.text for m in collected] == ["Second", "First"]
        adapter.fetch_messages.assert_called_with("slack:C123", {"direction": "backward"})

    async def test_auto_paginates(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()

        call_count = 0

        async def fake(_channel_id: str, _opts: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "messages": [
                        create_test_message("msg-3", "Page 1 Newest"),
                        create_test_message("msg-4", "Page 1 Older"),
                    ],
                    "nextCursor": "cursor-1",
                }
            return {
                "messages": [
                    create_test_message("msg-1", "Page 2 Newest"),
                    create_test_message("msg-2", "Page 2 Older"),
                ],
                "nextCursor": None,
            }

        adapter.fetch_channel_messages = AsyncMock(side_effect=fake)
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        collected = [m async for m in channel.messages]
        assert len(collected) == 4
        assert collected[0].text == "Page 1 Older"
        assert collected[1].text == "Page 1 Newest"
        assert collected[2].text == "Page 2 Older"
        assert collected[3].text == "Page 2 Newest"
        assert adapter.fetch_channel_messages.call_count == 2

    async def test_break_early(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.fetch_channel_messages = AsyncMock(
            return_value={
                "messages": [
                    create_test_message("msg-1", "First"),
                    create_test_message("msg-2", "Second"),
                    create_test_message("msg-3", "Third"),
                ],
                "nextCursor": "more",
            }
        )
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        collected: list[Any] = []
        async for msg in channel.messages:
            collected.append(msg)
            if len(collected) >= 2:
                break

        assert len(collected) == 2
        assert adapter.fetch_channel_messages.call_count == 1

    async def test_empty_channel(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.fetch_channel_messages = AsyncMock(
            return_value={"messages": [], "nextCursor": None}
        )
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        collected = [m async for m in channel.messages]
        assert collected == []


class TestThreadsIterator:
    async def test_iterate_threads(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        summaries = [
            {
                "id": "slack:C123:1234.5678",
                "rootMessage": create_test_message("msg-1", "Thread 1"),
                "replyCount": 5,
            },
            {
                "id": "slack:C123:2345.6789",
                "rootMessage": create_test_message("msg-2", "Thread 2"),
                "replyCount": 3,
            },
        ]
        adapter.list_threads = AsyncMock(return_value={"threads": summaries, "nextCursor": None})
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        collected = [t async for t in channel.threads()]
        assert len(collected) == 2
        assert collected[0]["id"] == "slack:C123:1234.5678"
        assert collected[0]["replyCount"] == 5
        assert collected[1]["id"] == "slack:C123:2345.6789"

    async def test_empty_iterable_without_list_threads(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.list_threads = None
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        collected = [t async for t in channel.threads()]
        assert collected == []

    async def test_auto_paginate_threads(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        call_count = 0

        async def fake(_channel_id: str, _opts: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "threads": [
                        {
                            "id": "slack:C123:1111",
                            "rootMessage": create_test_message("msg-1", "T1"),
                            "replyCount": 2,
                        }
                    ],
                    "nextCursor": "cursor-1",
                }
            return {
                "threads": [
                    {
                        "id": "slack:C123:2222",
                        "rootMessage": create_test_message("msg-2", "T2"),
                        "replyCount": 1,
                    }
                ],
                "nextCursor": None,
            }

        adapter.list_threads = AsyncMock(side_effect=fake)
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        collected = [t async for t in channel.threads()]
        assert len(collected) == 2
        assert adapter.list_threads.call_count == 2


class TestFetchMetadata:
    async def test_fetches_and_sets_name(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        assert channel.name is None
        info = await channel.fetch_metadata()

        assert info["id"] == "slack:C123"
        assert info["name"] == "#slack:C123"
        assert channel.name == "#slack:C123"

    async def test_basic_info_when_no_fetch_channel_info(self) -> None:
        adapter = create_mock_adapter()
        adapter.fetch_channel_info = None
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        info = await channel.fetch_metadata()
        assert info["id"] == "slack:C123"
        assert info["isDM"] is False
        assert info["metadata"] == {}


class TestPost:
    async def test_uses_post_channel_message(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post("Hello channel!")
        adapter.post_channel_message.assert_called_with("slack:C123", "Hello channel!")
        assert result.text == "Hello channel!"

    async def test_falls_back_to_post_message(self) -> None:
        adapter = create_mock_adapter()
        adapter.post_channel_message = None
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.post("Hello!")
        adapter.post_message.assert_called_with("slack:C123", "Hello!")

    async def test_streaming_accumulates(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        async def stream() -> Any:
            yield "Hello"
            yield " "
            yield "World"

        result = await channel.post(stream())
        adapter.post_channel_message.assert_called_with("slack:C123", {"markdown": "Hello World"})
        assert result.text == "Hello World"


class TestPostMessageFormats:
    async def test_raw(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post({"raw": "raw text message"})
        adapter.post_channel_message.assert_called_with("slack:C123", {"raw": "raw text message"})
        assert result.text == "raw text message"

    async def test_markdown(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post({"markdown": "**bold** text"})
        assert result.text == "bold text"

    async def test_ast(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        ast = root([paragraph([text("from ast")])])
        result = await channel.post({"ast": ast})
        assert result.text == "from ast"

    async def test_raw_with_attachments(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post(
            {
                "raw": "text with attachment",
                "attachments": [{"type": "image", "url": "https://example.com/img.png"}],
            }
        )
        assert len(result.attachments) == 1
        assert result.attachments[0].type == "image"


class TestSerialization:
    def test_to_json(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state, is_dm=False)

        json = channel.to_json()
        assert json == {
            "_type": "chat:Channel",
            "id": "slack:C123",
            "adapterName": "slack",
            "channelVisibility": "unknown",
            "isDM": False,
        }

    def test_from_json(self) -> None:
        data = {
            "_type": "chat:Channel",
            "id": "slack:C123",
            "adapterName": "slack",
            "isDM": False,
        }
        adapter = create_mock_adapter()
        channel = ChannelImpl.from_json(data, adapter)

        assert channel.id == "slack:C123"
        assert channel.is_dm is False
        assert channel.adapter is adapter


class TestDeriveChannelId:
    def test_uses_channel_id_from_thread_id(self) -> None:
        adapter = create_mock_adapter()
        result = derive_channel_id(adapter, "slack:C123:1234.5678")
        assert result == "slack:C123"
        adapter.channel_id_from_thread_id.assert_called_with("slack:C123:1234.5678")

    def test_different_adapters(self) -> None:
        adapter = create_mock_adapter("gchat")
        result = derive_channel_id(adapter, "gchat:spaces/ABC123:dGhyZWFk")
        assert result == "gchat:spaces/ABC123"
        adapter.channel_id_from_thread_id.assert_called_with("gchat:spaces/ABC123:dGhyZWFk")


class TestPostEphemeral:
    async def test_uses_adapter_post_ephemeral(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.post_ephemeral = AsyncMock(
            return_value={
                "id": "eph-1",
                "threadId": "slack:C123",
                "usedFallback": False,
                "raw": {},
            }
        )
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post_ephemeral("U456", "Secret!", {"fallbackToDM": True})
        adapter.post_ephemeral.assert_called_with("slack:C123", "U456", "Secret!")
        assert result is not None
        assert result.id == "eph-1"
        assert result.thread_id == "slack:C123"
        assert result.used_fallback is False
        assert result.raw == {}

    async def test_extracts_user_id_from_author(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.post_ephemeral = AsyncMock(
            return_value={
                "id": "eph-1",
                "threadId": "slack:C123",
                "usedFallback": False,
                "raw": {},
            }
        )
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        author = Author(
            user_id="U789",
            user_name="testuser",
            full_name="Test User",
            is_bot=False,
            is_me=False,
        )
        await channel.post_ephemeral(author, "Hello!", {"fallbackToDM": False})
        adapter.post_ephemeral.assert_called_with("slack:C123", "U789", "Hello!")

    async def test_returns_none_when_no_post_ephemeral_no_fallback(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.post_ephemeral = None
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post_ephemeral("U456", "Secret!", {"fallbackToDM": False})
        assert result is None

    async def test_falls_back_to_dm(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.post_ephemeral = None
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post_ephemeral("U456", "Secret!", {"fallbackToDM": True})
        adapter.open_dm.assert_called_with("U456")
        adapter.post_message.assert_called_with("slack:DU456:", "Secret!")
        assert result is not None
        assert result.id == "msg-1"
        assert result.thread_id == "slack:DU456:"
        assert result.used_fallback is True

    async def test_returns_none_when_no_post_ephemeral_no_open_dm(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.post_ephemeral = None
        adapter.open_dm = None
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post_ephemeral("U456", "Secret!", {"fallbackToDM": True})
        assert result is None


class TestStartTyping:
    async def test_default(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.start_typing()
        adapter.start_typing.assert_called_with("slack:C123", None)

    async def test_with_status(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.start_typing("thinking...")
        adapter.start_typing.assert_called_with("slack:C123", "thinking...")


class TestMentionUser:
    def test_basic(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        assert channel.mention_user("U456") == "<@U456>"

    def test_different_formats(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        assert channel.mention_user("UABC123DEF") == "<@UABC123DEF>"
        assert channel.mention_user("bot-user") == "<@bot-user>"


class TestPostErrorAndSentMessage:
    async def test_thread_id_override(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.post_channel_message = AsyncMock(
            return_value={"id": "msg-2", "threadId": "slack:C123:new-thread", "raw": {}}
        )
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post("Hello!")
        assert result.thread_id == "slack:C123:new-thread"

    async def test_sent_message_has_methods(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post("Hello!")
        assert callable(result.edit)
        assert callable(result.delete)
        assert callable(result.add_reaction)
        assert callable(result.remove_reaction)

    async def test_edit(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post("Hello!")
        await result.edit("Updated!")
        adapter.edit_message.assert_called_with("slack:C123", "msg-1", "Updated!")

    async def test_delete(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post("Hello!")
        await result.delete()
        adapter.delete_message.assert_called_with("slack:C123", "msg-1")

    async def test_add_reaction(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post("Hello!")
        await result.add_reaction("thumbsup")
        adapter.add_reaction.assert_called_with("slack:C123", "msg-1", "thumbsup")

    async def test_remove_reaction(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.post("Hello!")
        await result.remove_reaction("thumbsup")
        adapter.remove_reaction.assert_called_with("slack:C123", "msg-1", "thumbsup")


class TestSchedule:
    _future = datetime(2030, 1, 1, tzinfo=UTC)

    def _sched(self) -> ScheduledMessage:
        return ScheduledMessage(
            scheduled_message_id="Q123",
            channel_id="C123",
            post_at=self._future,
            raw={"ok": True},
            cancel=AsyncMock(return_value=None),
        )

    async def test_not_implemented_without_schedule_message(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        with pytest.raises(ChatNotImplementedError):
            await channel.schedule("Hello", {"postAt": self._future})

    async def test_feature_scheduling(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        try:
            await channel.schedule("Hello", {"postAt": self._future})
            pytest.fail("should have thrown")
        except ChatNotImplementedError as err:
            assert err.feature == "scheduling"

    async def test_delegates_to_adapter(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.schedule_message = AsyncMock(return_value=self._sched())
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.schedule("Hello", {"postAt": self._future})
        adapter.schedule_message.assert_called_with("slack:C123", "Hello", {"postAt": self._future})

    async def test_returns_adapter_result(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        expected = self._sched()
        adapter.schedule_message = AsyncMock(return_value=expected)
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        result = await channel.schedule("Hello", {"postAt": self._future})
        assert result is expected

    async def test_propagates_errors(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.schedule_message = AsyncMock(side_effect=RuntimeError("API failure"))
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        with pytest.raises(RuntimeError, match="API failure"):
            await channel.schedule("Hello", {"postAt": self._future})

    async def test_does_not_call_post(self) -> None:
        adapter = create_mock_adapter()
        state = create_mock_state()
        adapter.schedule_message = AsyncMock(return_value=self._sched())
        channel = ChannelImpl(id="slack:C123", adapter=adapter, state_adapter=state)

        await channel.schedule("Hello", {"postAt": self._future})
        adapter.post_message.assert_not_called()
        adapter.post_channel_message.assert_not_called()
