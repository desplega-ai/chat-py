"""Thread implementation — port of ``packages/chat/src/thread.ts``.

A :class:`ThreadImpl` represents a conversation thread within a channel. It
supports posting messages, iterating thread messages, subscribing /
unsubscribing, managing per-thread state, and serializing to JSON for
workflow engines.

Streaming uses :func:`~chat.from_full_stream.from_full_stream` for AI-SDK
normalization and :class:`~chat.streaming_markdown.StreamingMarkdownRenderer`
for throttled post→edit cycling when the adapter lacks native stream support.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from chat.channel import (
    ChannelImpl,
    SentMessage,
    _extract_message_content,
    _is_async_iterable,
    derive_channel_id,
)
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat.from_full_stream import from_full_stream
from chat.message import Message
from chat.postable_object import is_postable_object, post_postable_object
from chat.streaming_markdown import StreamingMarkdownRenderer
from chat.types import (
    THREAD_STATE_TTL_MS,
    AdapterPostableMessage,
    Author,
    ChannelVisibility,
    EphemeralMessage,
    MessageMetadata,
    ScheduledMessage,
    SerializedMessage,
)

if TYPE_CHECKING:
    from chat.logger import Logger
    from chat.message_history import MessageHistoryCache
    from chat.types import StateAdapter

# State key prefix for thread-scoped state.
THREAD_STATE_KEY_PREFIX = "thread-state:"


class SerializedThread(TypedDict, total=False):
    """Serialized thread data for external systems (e.g., workflow engines)."""

    _type: Literal["chat:Thread"]
    adapterName: str
    channelId: str
    channelVisibility: ChannelVisibility
    currentMessage: SerializedMessage
    id: str
    isDM: bool


class ThreadImpl[TState]:
    """Concrete :class:`~chat.types.Thread` implementation.

    Construct with either an explicit ``adapter`` / ``state_adapter`` pair or
    with ``adapter_name`` for lazy resolution via the chat singleton.
    """

    __slots__ = (
        "_adapter",
        "_adapter_name",
        "_channel",
        "_current_message",
        "_fallback_streaming_placeholder_text",
        "_is_subscribed_context",
        "_logger",
        "_message_history",
        "_recent_messages",
        "_state_adapter_instance",
        "_streaming_update_interval_ms",
        "channel_id",
        "channel_visibility",
        "id",
        "is_dm",
    )

    def __init__(
        self,
        *,
        id: str,
        channel_id: str,
        adapter: Any = None,
        adapter_name: str | None = None,
        state_adapter: StateAdapter | None = None,
        channel_visibility: ChannelVisibility = "unknown",
        current_message: Message[Any] | None = None,
        fallback_streaming_placeholder_text: str | None = "...",
        initial_message: Message[Any] | None = None,
        is_dm: bool = False,
        is_subscribed_context: bool = False,
        logger: Logger | None = None,
        message_history: MessageHistoryCache | None = None,
        streaming_update_interval_ms: int = 500,
    ) -> None:
        if adapter is None and adapter_name is None:
            raise ValueError("ThreadImpl requires either adapter or adapter_name")

        self.id = id
        self.channel_id = channel_id
        self.is_dm = is_dm
        self.channel_visibility = channel_visibility

        self._adapter: Any = adapter
        self._adapter_name: str | None = adapter_name
        self._state_adapter_instance: StateAdapter | None = state_adapter
        self._is_subscribed_context = is_subscribed_context
        self._current_message = current_message
        self._logger = logger
        self._streaming_update_interval_ms = streaming_update_interval_ms
        self._fallback_streaming_placeholder_text = fallback_streaming_placeholder_text
        self._message_history: MessageHistoryCache | None = message_history
        self._channel: ChannelImpl[TState] | None = None
        self._recent_messages: list[Message[Any]] = (
            [initial_message] if initial_message is not None else []
        )

    # ------------------------------------------------------------------
    # Adapter / state resolution
    # ------------------------------------------------------------------

    @property
    def adapter(self) -> Any:
        if self._adapter is not None:
            return self._adapter

        if self._adapter_name is None:
            raise RuntimeError("Thread has no adapter configured")

        from chat.chat_singleton import get_chat_singleton

        chat = get_chat_singleton()
        adapter = chat.get_adapter(self._adapter_name)
        if adapter is None:
            raise RuntimeError(f'Adapter "{self._adapter_name}" not found in Chat singleton')
        self._adapter = adapter
        return adapter

    @property
    def _state_adapter(self) -> StateAdapter:
        if self._state_adapter_instance is not None:
            return self._state_adapter_instance

        from chat.chat_singleton import get_chat_singleton

        chat = get_chat_singleton()
        self._state_adapter_instance = chat.get_state()
        return self._state_adapter_instance

    # ------------------------------------------------------------------
    # Recent messages
    # ------------------------------------------------------------------

    @property
    def recent_messages(self) -> list[Message[Any]]:
        return self._recent_messages

    @recent_messages.setter
    def recent_messages(self, messages: list[Message[Any]]) -> None:
        self._recent_messages = messages

    # ------------------------------------------------------------------
    # Thread state
    # ------------------------------------------------------------------

    @property
    def state(self) -> Any:
        """Read the stored thread state.

        Returns a coroutine — ``await thread.state`` to resolve.
        """
        return self._state_adapter.get(f"{THREAD_STATE_KEY_PREFIX}{self.id}")

    async def set_state(
        self,
        new_state: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> None:
        """Set thread state — merges by default, or replaces when
        ``options={"replace": True}``.
        """
        key = f"{THREAD_STATE_KEY_PREFIX}{self.id}"

        if options and options.get("replace"):
            await self._state_adapter.set(key, new_state, THREAD_STATE_TTL_MS)
            return

        existing = await self._state_adapter.get(key)
        merged = {**(existing or {}), **new_state}
        await self._state_adapter.set(key, merged, THREAD_STATE_TTL_MS)

    # ------------------------------------------------------------------
    # Channel (lazy + cached)
    # ------------------------------------------------------------------

    @property
    def channel(self) -> ChannelImpl[TState]:
        """Get the :class:`ChannelImpl` containing this thread. Lazy + cached."""
        if self._channel is None:
            channel_id = derive_channel_id(self.adapter, self.id)
            self._channel = ChannelImpl[TState](
                id=channel_id,
                adapter=self.adapter,
                state_adapter=self._state_adapter,
                channel_visibility=self.channel_visibility,
                is_dm=self.is_dm,
                message_history=self._message_history,
            )
        return self._channel

    # ------------------------------------------------------------------
    # Messages iterators
    # ------------------------------------------------------------------

    @property
    def messages(self) -> AsyncIterable[Message[Any]]:
        """Iterate messages newest first (backward from most recent)."""
        adapter = self.adapter
        thread_id = self.id
        message_history = self._message_history

        async def _iter() -> AsyncIterable[Message[Any]]:
            cursor: str | None = None
            yielded_any = False

            while True:
                options: dict[str, Any] = {"direction": "backward"}
                if cursor is not None:
                    options["cursor"] = cursor
                result = await adapter.fetch_messages(thread_id, options)

                messages = result.get("messages", [])
                for msg in reversed(messages):
                    yielded_any = True
                    yield msg

                next_cursor = result.get("nextCursor")
                if not next_cursor or len(messages) == 0:
                    break
                cursor = next_cursor

            if not yielded_any and message_history is not None:
                cached = await message_history.get_messages(thread_id)
                for msg in reversed(cached):
                    yield msg

        return _iter()

    @property
    def all_messages(self) -> AsyncIterable[Message[Any]]:
        """Iterate messages oldest first (chronological, forward pagination)."""
        adapter = self.adapter
        thread_id = self.id
        message_history = self._message_history

        async def _iter() -> AsyncIterable[Message[Any]]:
            cursor: str | None = None
            yielded_any = False

            while True:
                options: dict[str, Any] = {"limit": 100, "direction": "forward"}
                if cursor is not None:
                    options["cursor"] = cursor
                result = await adapter.fetch_messages(thread_id, options)

                messages = result.get("messages", [])
                for msg in messages:
                    yielded_any = True
                    yield msg

                next_cursor = result.get("nextCursor")
                if not next_cursor or len(messages) == 0:
                    break
                cursor = next_cursor

            if not yielded_any and message_history is not None:
                cached = await message_history.get_messages(thread_id)
                for msg in cached:
                    yield msg

        return _iter()

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def is_subscribed(self) -> bool:
        if self._is_subscribed_context:
            return True
        return await self._state_adapter.is_subscribed(self.id)

    async def subscribe(self) -> None:
        await self._state_adapter.subscribe(self.id)
        on_subscribe = getattr(self.adapter, "on_thread_subscribe", None)
        if on_subscribe is not None:
            await on_subscribe(self.id)

    async def unsubscribe(self) -> None:
        await self._state_adapter.unsubscribe(self.id)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Refresh :attr:`recent_messages` from the adapter (limit 50)."""
        result = await self.adapter.fetch_messages(self.id, {"limit": 50})
        messages = result.get("messages", [])
        if len(messages) > 0:
            self._recent_messages = messages
        elif self._message_history is not None:
            self._recent_messages = await self._message_history.get_messages(self.id, 50)
        else:
            self._recent_messages = []

    # ------------------------------------------------------------------
    # Post
    # ------------------------------------------------------------------

    async def post(self, message: Any) -> Any:
        """Post a message to this thread.

        Supports strings, ``PostableRaw`` / ``PostableMarkdown`` / ``PostableAst``
        dicts, :class:`~chat.postable_object.PostableObject`, and
        :class:`AsyncIterable` string streams.
        """
        if is_postable_object(message):
            await self._handle_postable_object(message)
            return message

        if _is_async_iterable(message):
            return await self._handle_stream(message)

        return await self._post_single_message(message)

    async def _handle_postable_object(self, obj: Any) -> None:
        async def post_fn(thread_id: str, msg: Any) -> Any:
            return await self.adapter.post_message(thread_id, msg)

        await post_postable_object(obj, self.adapter, self.id, post_fn, self._logger)

    async def _post_single_message(self, postable: AdapterPostableMessage) -> SentMessage:
        raw_message = await self.adapter.post_message(self.id, postable)

        sent = self._create_sent_message(
            raw_message["id"],
            postable,
            raw_message.get("threadId"),
        )

        if self._message_history is not None:
            await self._message_history.append(self.id, Message(**_sent_to_message_kwargs(sent)))

        return sent

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _handle_stream(self, raw_stream: AsyncIterable[Any]) -> SentMessage:
        """Handle an async-iterable stream.

        Normalizes the raw stream through :func:`from_full_stream`, then
        delegates to the adapter's native ``stream`` method when available or
        the throttled post→edit fallback otherwise.
        """
        options: dict[str, Any] = {}
        if self._current_message is not None:
            options["recipientUserId"] = self._current_message.author.user_id
            raw = self._current_message.raw
            if isinstance(raw, dict):
                team_id = raw.get("team_id") or raw.get("team")
                if team_id is not None:
                    options["recipientTeamId"] = team_id

        text_stream = from_full_stream(raw_stream)

        adapter_stream = getattr(self.adapter, "stream", None)
        if adapter_stream is not None:
            accumulated = ""

            async def _wrapped() -> AsyncIterator[Any]:
                nonlocal accumulated
                async for chunk in text_stream:
                    if isinstance(chunk, str):
                        accumulated += chunk
                    elif isinstance(chunk, dict) and chunk.get("type") == "markdown_text":
                        text = chunk.get("text", "")
                        if isinstance(text, str):
                            accumulated += text
                    yield chunk

            raw = await adapter_stream(self.id, _wrapped(), options)
            sent = self._create_sent_message(
                raw["id"],
                {"markdown": accumulated},
                raw.get("threadId"),
            )

            if self._message_history is not None:
                await self._message_history.append(
                    self.id, Message(**_sent_to_message_kwargs(sent))
                )

            return sent

        async def _text_only() -> AsyncIterator[str]:
            async for chunk in text_stream:
                if isinstance(chunk, str):
                    yield chunk
                elif isinstance(chunk, dict) and chunk.get("type") == "markdown_text":
                    text = chunk.get("text", "")
                    if isinstance(text, str):
                        yield text

        return await self._fallback_stream(_text_only(), options)

    async def _fallback_stream(
        self,
        stream: AsyncIterable[str],
        options: dict[str, Any] | None = None,
    ) -> SentMessage:
        """Post+edit streaming fallback with throttled intermediate edits.

        Posts an initial placeholder (or the first rendered chunk if
        ``fallback_streaming_placeholder_text`` is ``None``), then edits the
        message at ``streaming_update_interval_ms`` cadence as new text
        arrives. On stream completion, flushes the final rendered content.
        """
        interval_ms = (options or {}).get("updateIntervalMs") or self._streaming_update_interval_ms
        interval_s = interval_ms / 1000.0
        placeholder_text = self._fallback_streaming_placeholder_text

        renderer = StreamingMarkdownRenderer()
        msg: dict[str, Any] | None = None
        thread_id_for_edits = self.id
        last_edit_content = ""

        if placeholder_text is not None:
            msg = await self.adapter.post_message(self.id, placeholder_text)
            thread_id_for_edits = msg.get("threadId") or self.id
            last_edit_content = placeholder_text

        stopped = asyncio.Event()

        async def _edit_loop() -> None:
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=interval_s)
                    return
                except TimeoutError:
                    pass
                if msg is None:
                    continue
                content = renderer.render()
                nonlocal last_edit_content
                if content.strip() and content != last_edit_content:
                    try:
                        await self.adapter.edit_message(
                            thread_id_for_edits, msg["id"], {"markdown": content}
                        )
                        last_edit_content = content
                    except Exception as exc:
                        if self._logger is not None:
                            self._logger.warn("fallbackStream edit failed", exc)

        editor_task: asyncio.Task[None] | None = None
        if msg is not None:
            editor_task = asyncio.create_task(_edit_loop())

        try:
            async for chunk in stream:
                renderer.push(chunk)
                if msg is None:
                    content = renderer.render()
                    if content.strip():
                        msg = await self.adapter.post_message(self.id, {"markdown": content})
                        thread_id_for_edits = msg.get("threadId") or self.id
                        last_edit_content = content
                        editor_task = asyncio.create_task(_edit_loop())
        finally:
            stopped.set()
            if editor_task is not None:
                await editor_task

        accumulated = renderer.get_text()
        final_content = renderer.finish()

        if msg is None:
            content = accumulated if accumulated.strip() else " "
            msg = await self.adapter.post_message(self.id, {"markdown": content})
            thread_id_for_edits = msg.get("threadId") or self.id
            last_edit_content = accumulated

        if final_content.strip() and final_content != last_edit_content:
            await self.adapter.edit_message(
                thread_id_for_edits, msg["id"], {"markdown": accumulated}
            )

        sent = self._create_sent_message(
            msg["id"],
            {"markdown": accumulated},
            thread_id_for_edits,
        )

        if self._message_history is not None:
            await self._message_history.append(self.id, Message(**_sent_to_message_kwargs(sent)))

        return sent

    # ------------------------------------------------------------------
    # Ephemeral
    # ------------------------------------------------------------------

    async def post_ephemeral(
        self,
        user: str | Author,
        message: AdapterPostableMessage,
        options: dict[str, Any],
    ) -> EphemeralMessage | None:
        fallback_to_dm = options.get("fallbackToDM", False)
        user_id = user if isinstance(user, str) else user.user_id

        # JSX path deferred to part B.
        postable = message

        post_ephemeral = getattr(self.adapter, "post_ephemeral", None)
        if post_ephemeral is not None:
            result = await post_ephemeral(self.id, user_id, postable)
            if isinstance(result, EphemeralMessage):
                return result
            return EphemeralMessage(
                id=result["id"],
                thread_id=result.get("threadId", self.id),
                used_fallback=result.get("usedFallback", False),
                raw=result.get("raw"),
            )

        if not fallback_to_dm:
            return None

        open_dm = getattr(self.adapter, "open_dm", None)
        if open_dm is not None:
            dm_thread_id = await open_dm(user_id)
            result = await self.adapter.post_message(dm_thread_id, postable)
            return EphemeralMessage(
                id=result["id"],
                thread_id=dm_thread_id,
                used_fallback=True,
                raw=result.get("raw"),
            )

        return None

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    async def schedule(
        self,
        message: AdapterPostableMessage,
        options: dict[str, Any],
    ) -> ScheduledMessage:
        # JSX path deferred to part B.
        postable = message

        schedule_message = getattr(self.adapter, "schedule_message", None)
        if schedule_message is None:
            raise ChatNotImplementedError(
                "Scheduled messages are not supported by this adapter",
                "scheduling",
            )

        result: ScheduledMessage = await schedule_message(self.id, postable, options)
        return result

    # ------------------------------------------------------------------
    # Typing / mentions
    # ------------------------------------------------------------------

    async def start_typing(self, status: str | None = None) -> None:
        await self.adapter.start_typing(self.id, status)

    def mention_user(self, user_id: str) -> str:
        return f"<@{user_id}>"

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_json(self) -> SerializedThread:
        result: SerializedThread = {
            "_type": "chat:Thread",
            "id": self.id,
            "channelId": self.channel_id,
            "channelVisibility": self.channel_visibility,
            "isDM": self.is_dm,
            "adapterName": self._adapter_name or self.adapter.name,
        }
        if self._current_message is not None:
            result["currentMessage"] = self._current_message.to_json()
        return result

    @classmethod
    def from_json(
        cls,
        data: SerializedThread,
        adapter: Any = None,
    ) -> ThreadImpl[Any]:
        current_message = None
        raw_current = data.get("currentMessage")
        if raw_current is not None:
            current_message = Message.from_json(raw_current)

        thread: ThreadImpl[Any] = cls(
            id=data["id"],
            channel_id=data["channelId"],
            adapter_name=data["adapterName"],
            channel_visibility=data.get("channelVisibility", "unknown"),
            current_message=current_message,
            is_dm=data.get("isDM", False),
        )
        if adapter is not None:
            thread._adapter = adapter
        return thread

    def __chat_serialize__(self) -> SerializedThread:
        return self.to_json()

    @classmethod
    def __chat_deserialize__(cls, data: SerializedThread) -> ThreadImpl[Any]:
        return cls.from_json(data)

    # ------------------------------------------------------------------
    # Internal: SentMessage factory
    # ------------------------------------------------------------------

    def _create_sent_message(
        self,
        message_id: str,
        postable: AdapterPostableMessage,
        thread_id_override: str | None = None,
    ) -> SentMessage:
        """Create a :class:`SentMessage` bound to this thread's channel.

        :class:`SentMessage` in chat-py holds a reference to a
        :class:`ChannelImpl` (not :class:`ThreadImpl`) because adapter access
        goes through the channel. We piggyback on :attr:`channel` so edits
        share the same adapter resolution path.
        """
        adapter = self.adapter
        thread_id = thread_id_override or self.id
        plain_text, formatted, attachments = _extract_message_content(postable)

        return SentMessage(
            id=message_id,
            thread_id=thread_id,
            text=plain_text,
            formatted=formatted,
            raw=None,
            author=Author(
                user_id="self",
                user_name=adapter.user_name,
                full_name=adapter.user_name,
                is_bot=True,
                is_me=True,
            ),
            metadata=MessageMetadata(
                date_sent=datetime.now(UTC),
                edited=False,
            ),
            attachments=attachments,
            links=[],
            channel=self.channel,
        )

    def create_sent_message_from_message(self, message: Message[Any]) -> SentMessage:
        """Wrap an existing :class:`Message` as a :class:`SentMessage`."""
        return SentMessage(
            id=message.id,
            thread_id=message.thread_id,
            text=message.text,
            formatted=message.formatted,
            raw=message.raw,
            author=message.author,
            metadata=message.metadata,
            attachments=message.attachments,
            links=message.links,
            is_mention=message.is_mention,
            channel=self.channel,
        )


def _sent_to_message_kwargs(sent: SentMessage) -> dict[str, Any]:
    """Extract :class:`Message` constructor kwargs from a :class:`SentMessage`."""
    return {
        "id": sent.id,
        "thread_id": sent.thread_id,
        "text": sent.text,
        "formatted": sent.formatted,
        "raw": sent.raw,
        "author": sent.author,
        "metadata": sent.metadata,
        "attachments": sent.attachments,
        "links": sent.links,
    }
