"""Tests for :mod:`chat.chat` — the :class:`Chat` orchestrator."""

from __future__ import annotations

import re
from typing import Any

import pytest
from chat.channel import ChannelImpl
from chat.chat import Chat
from chat.chat_singleton import clear_chat_singleton
from chat.errors import ChatError, LockError
from chat.mock_adapter import (
    create_mock_adapter,
    create_mock_state,
    create_test_message,
    mock_logger,
)
from chat.thread import ThreadImpl
from chat.types import Author


@pytest.fixture(autouse=True)
def _clear_singleton() -> None:
    clear_chat_singleton()


def _make_chat(**overrides: Any) -> Chat:
    adapter = overrides.pop("adapter", None) or create_mock_adapter("slack")
    state = overrides.pop("state", None) or create_mock_state()
    adapters = overrides.pop("adapters", None) or {"slack": adapter}
    return Chat(
        user_name="testbot",
        adapters=adapters,
        state=state,
        logger=mock_logger,
        **overrides,
    )


class TestSingleton:
    def test_register_and_get(self) -> None:
        chat = _make_chat()
        assert not Chat.has_singleton()
        chat.register_singleton()
        assert Chat.has_singleton()
        assert Chat.get_singleton() is chat

    def test_get_without_register_raises(self) -> None:
        with pytest.raises(RuntimeError, match="No Chat singleton"):
            Chat.get_singleton()


class TestAccessors:
    def test_get_adapter(self) -> None:
        adapter = create_mock_adapter("slack")
        chat = _make_chat(adapters={"slack": adapter})
        assert chat.get_adapter("slack") is adapter
        assert chat.get_adapter("missing") is None

    def test_get_user_name(self) -> None:
        chat = _make_chat()
        assert chat.get_user_name() == "testbot"

    def test_get_state(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state)
        assert chat.get_state() is state

    def test_get_logger_with_prefix(self) -> None:
        chat = _make_chat()
        assert chat.get_logger() is mock_logger
        child = chat.get_logger("x")
        assert child is not None


class TestHandlerRegistration:
    def test_on_new_mention(self) -> None:
        chat = _make_chat()

        async def h(thread: Any, msg: Any, ctx: Any = None) -> None: ...

        chat.on_new_mention(h)
        assert len(chat._mention_handlers) == 1

    def test_on_new_message_compiles_string(self) -> None:
        chat = _make_chat()

        async def h(thread: Any, msg: Any, ctx: Any = None) -> None: ...

        chat.on_new_message(r"!help", h)
        assert len(chat._message_patterns) == 1
        assert isinstance(chat._message_patterns[0][0], re.Pattern)

    def test_on_reaction_with_filter(self) -> None:
        chat = _make_chat()

        async def h(event: Any) -> None: ...

        chat.on_reaction(["thumbs_up"], h)
        assert chat._reaction_handlers[0][0] == ["thumbs_up"]

    def test_on_reaction_no_filter(self) -> None:
        chat = _make_chat()

        async def h(event: Any) -> None: ...

        chat.on_reaction(h)
        assert chat._reaction_handlers[0][0] == []

    def test_on_action_with_single_id(self) -> None:
        chat = _make_chat()

        async def h(event: Any) -> None: ...

        chat.on_action("approve", h)
        assert chat._action_handlers[0][0] == ["approve"]

    def test_on_action_with_list(self) -> None:
        chat = _make_chat()

        async def h(event: Any) -> None: ...

        chat.on_action(["a", "b"], h)
        assert chat._action_handlers[0][0] == ["a", "b"]

    def test_on_slash_command_normalizes_prefix(self) -> None:
        chat = _make_chat()

        async def h(event: Any) -> None: ...

        chat.on_slash_command("help", h)
        assert chat._slash_command_handlers[0][0] == ["/help"]

        chat.on_slash_command("/already", h)
        assert chat._slash_command_handlers[1][0] == ["/already"]


class TestChannelFactory:
    def test_channel(self) -> None:
        chat = _make_chat()
        ch = chat.channel("slack:C123")
        assert isinstance(ch, ChannelImpl)
        assert ch.id == "slack:C123"

    def test_channel_unknown_adapter(self) -> None:
        chat = _make_chat()
        with pytest.raises(ChatError) as exc:
            chat.channel("unknown:C123")
        assert exc.value.code == "ADAPTER_NOT_FOUND"


class TestOpenDM:
    @pytest.mark.asyncio
    async def test_open_dm_slack(self) -> None:
        chat = _make_chat()
        thread = await chat.open_dm("U123")
        assert isinstance(thread, ThreadImpl)
        assert thread.id.startswith("slack:")

    @pytest.mark.asyncio
    async def test_open_dm_unknown_format(self) -> None:
        chat = _make_chat()
        with pytest.raises(ChatError) as exc:
            await chat.open_dm("totally-invalid")
        assert exc.value.code == "UNKNOWN_USER_ID_FORMAT"


class TestDetectMention:
    def test_detects_username_mention(self) -> None:
        adapter = create_mock_adapter("slack")
        adapter.user_name = "slack-bot"
        chat = _make_chat(adapters={"slack": adapter})
        msg = create_test_message("m1", "Hey @slack-bot, help!")
        assert chat._detect_mention(adapter, msg) is True

    def test_no_mention(self) -> None:
        adapter = create_mock_adapter("slack")
        adapter.user_name = "slack-bot"
        chat = _make_chat(adapters={"slack": adapter})
        msg = create_test_message("m1", "Just a regular message")
        assert chat._detect_mention(adapter, msg) is False

    def test_discord_format(self) -> None:
        adapter = create_mock_adapter("discord")
        adapter.user_name = "bot"
        adapter.bot_user_id = "U123"
        chat = _make_chat(adapters={"discord": adapter})
        msg = create_test_message("m1", "<@U123> hello")
        assert chat._detect_mention(adapter, msg) is True


class TestHandleIncomingMessage:
    @pytest.mark.asyncio
    async def test_skips_message_from_self(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state)
        msg = create_test_message(
            "m1",
            "hi",
            author=Author(
                user_id="UBOT",
                user_name="testbot",
                full_name="Test Bot",
                is_bot=True,
                is_me=True,
            ),
        )
        adapter = chat.get_adapter("slack")

        called = False

        async def handler(*args: Any) -> None:
            nonlocal called
            called = True

        chat.on_new_mention(handler)
        await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)
        assert not called

    @pytest.mark.asyncio
    async def test_dedupe(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state)
        msg = create_test_message("m1", "@slack-bot hi")
        adapter = chat.get_adapter("slack")

        calls = 0

        async def handler(thread: Any, message: Any, ctx: Any = None) -> None:
            nonlocal calls
            calls += 1

        chat.on_new_mention(handler)
        await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)
        await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)
        assert calls == 1

    @pytest.mark.asyncio
    async def test_mention_dispatch(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state)
        adapter = chat.get_adapter("slack")

        received: list[Any] = []

        async def handler(thread: Any, message: Any, ctx: Any = None) -> None:
            received.append(message)

        chat.on_new_mention(handler)
        msg = create_test_message("m1", "@slack-bot hi")
        await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)
        assert len(received) == 1
        assert received[0].is_mention is True

    @pytest.mark.asyncio
    async def test_pattern_dispatch(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state)
        adapter = chat.get_adapter("slack")

        matched: list[Any] = []

        async def handler(thread: Any, message: Any, ctx: Any = None) -> None:
            matched.append(message.text)

        chat.on_new_message(r"^!help", handler)
        msg = create_test_message("m1", "!help me")
        await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)
        assert matched == ["!help me"]

    @pytest.mark.asyncio
    async def test_subscribed_dispatch(self) -> None:
        state = create_mock_state()
        await state.subscribe("slack:C1:T1")
        chat = _make_chat(state=state)
        adapter = chat.get_adapter("slack")

        calls: list[Any] = []

        async def handler(thread: Any, message: Any, ctx: Any = None) -> None:
            calls.append(message)

        chat.on_subscribed_message(handler)
        msg = create_test_message("m1", "follow-up")
        await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_dm_dispatch(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state)
        adapter = chat.get_adapter("slack")

        dm_thread_id = "slack:DU1:"
        calls: list[Any] = []

        async def dm_handler(
            thread: Any, message: Any, channel: Any = None, ctx: Any = None
        ) -> None:
            calls.append((thread, message))

        chat.on_direct_message(dm_handler)
        msg = create_test_message("m1", "dm me")
        await chat.handle_incoming_message(adapter, dm_thread_id, msg)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_lock_contention_raises(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state)
        adapter = chat.get_adapter("slack")

        # Pre-acquire lock on channel (default scope is "thread")
        await state.acquire_lock("slack:C1:T1", 30_000)

        msg = create_test_message("m1", "@slack-bot hi")
        with pytest.raises(LockError):
            await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)


class TestConcurrent:
    @pytest.mark.asyncio
    async def test_concurrent_no_lock(self) -> None:
        state = create_mock_state()
        chat = _make_chat(state=state, concurrency="concurrent")
        adapter = chat.get_adapter("slack")

        calls = 0

        async def handler(thread: Any, message: Any, ctx: Any = None) -> None:
            nonlocal calls
            calls += 1

        chat.on_new_mention(handler)
        msg = create_test_message("m1", "@slack-bot hi")
        await chat.handle_incoming_message(adapter, "slack:C1:T1", msg)
        assert calls == 1
        # Lock must not have been acquired
        assert "slack:C1:T1" not in state.locks


class TestReviver:
    def test_registers_singleton(self) -> None:
        chat = _make_chat()
        assert not Chat.has_singleton()
        fn = chat.reviver()
        assert Chat.has_singleton()
        assert callable(fn)


class TestWebhookHandlers:
    @pytest.mark.asyncio
    async def test_webhook_dispatches_to_adapter(self) -> None:
        adapter = create_mock_adapter("slack")
        chat = _make_chat(adapters={"slack": adapter})
        assert "slack" in chat.webhooks
        result = await chat.webhooks["slack"]({"body": "x"})
        assert result == (200, {}, b"ok")
        adapter.handle_webhook.assert_called_once()

    @pytest.mark.asyncio
    async def test_webhook_unknown_adapter_raises(self) -> None:
        chat = _make_chat()
        with pytest.raises(ChatError) as exc:
            await chat.handle_webhook("missing", {"body": "x"})
        assert exc.value.code == "UNKNOWN_ADAPTER"
