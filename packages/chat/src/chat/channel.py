"""Channel implementation — port of ``packages/chat/src/channel.ts``.

A :class:`ChannelImpl` represents a channel/conversation container. It
supports posting messages, iterating top-level channel messages, listing
threads, and serializing to JSON for workflow engines.

JSX/Card handling (``PostableCard``, :class:`ChatElement`) is deferred to
part B of the port — any input message that doesn't match ``string`` / raw
/ markdown / ast / :class:`~chat.postable_object.PostableObject` currently
raises :class:`ValueError`.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

from chat.errors import NotImplementedError as ChatNotImplementedError
from chat.markdown import paragraph, parse_markdown, root, text, to_plain_text
from chat.message import Message
from chat.postable_object import is_postable_object, post_postable_object
from chat.types import (
    THREAD_STATE_TTL_MS,
    AdapterPostableMessage,
    Attachment,
    Author,
    ChannelInfo,
    ChannelVisibility,
    EphemeralMessage,
    FormattedContent,
    MessageMetadata,
    ScheduledMessage,
    ThreadSummary,
)

if TYPE_CHECKING:
    from chat.message_history import MessageHistoryCache
    from chat.types import StateAdapter

# State key prefix for channel-scoped state.
CHANNEL_STATE_KEY_PREFIX = "channel-state:"


class SerializedChannel(TypedDict, total=False):
    """Serialized channel data for external systems (e.g., workflow engines)."""

    _type: Literal["chat:Channel"]
    adapterName: str
    channelVisibility: ChannelVisibility
    id: str
    isDM: bool


def _is_async_iterable(value: Any) -> bool:
    """Duck-type check for ``AsyncIterable[str]`` (AI SDK ``textStream``)."""
    return hasattr(value, "__aiter__") and not isinstance(value, (str, bytes))


def _to_attachment(a: Any) -> Attachment:
    """Normalize an attachment dict or :class:`Attachment` into an :class:`Attachment`."""
    if isinstance(a, Attachment):
        return a
    if isinstance(a, dict):
        return Attachment(
            type=a["type"],
            url=a.get("url"),
            name=a.get("name"),
            mime_type=a.get("mimeType", a.get("mime_type")),
            size=a.get("size"),
            width=a.get("width"),
            height=a.get("height"),
            data=a.get("data"),
            fetch_data=a.get("fetchData", a.get("fetch_data")),
        )
    raise TypeError(f"Cannot coerce {type(a).__name__} to Attachment")


def _extract_message_content(
    message: AdapterPostableMessage,
) -> tuple[str, FormattedContent, list[Attachment]]:
    """Extract plain text, mdast AST, and attachments from a postable message.

    Mirrors upstream ``extractMessageContent`` — returns a tuple of
    ``(plain_text, formatted, attachments)``. JSX/Card branches are deferred
    to part B of the port.
    """
    if isinstance(message, str):
        return (message, root([paragraph([text(message)])]), [])

    if isinstance(message, dict):
        raw_attachments = message.get("attachments") or []
        attachments = [_to_attachment(a) for a in raw_attachments]

        if "raw" in message:
            return (
                message["raw"],
                root([paragraph([text(message["raw"])])]),
                attachments,
            )
        if "markdown" in message:
            ast = parse_markdown(message["markdown"])
            return (to_plain_text(ast), ast, attachments)
        if "ast" in message:
            ast = message["ast"]
            return (to_plain_text(ast), ast, attachments)
        if "card" in message or message.get("type") == "card":
            # Part B: card rendering requires ``cards.py``.
            raise ChatNotImplementedError(
                "Card / JSX postable messages are not yet supported in chat-py",
                "cards",
            )

    raise ValueError(f"Invalid PostableMessage format: {type(message).__name__}")


async def _collect_stream(iterable: AsyncIterable[Any]) -> str:
    """Consume an :class:`AsyncIterable` and concatenate its string chunks.

    Non-string chunks are ignored — a minimal stand-in for upstream
    ``fromFullStream`` until part B ports AI SDK stream handling.
    """
    accumulated = ""
    async for chunk in iterable:
        if isinstance(chunk, str):
            accumulated += chunk
    return accumulated


class SentMessage:
    """A message posted to a channel/thread, with ``edit`` / ``delete`` / reaction hooks.

    Matches upstream ``SentMessage`` — the object returned from
    :meth:`ChannelImpl.post`.
    """

    __slots__ = (
        "_channel",
        "attachments",
        "author",
        "formatted",
        "id",
        "links",
        "metadata",
        "raw",
        "text",
        "thread_id",
    )

    def __init__(
        self,
        *,
        id: str,
        thread_id: str,
        text: str,
        formatted: FormattedContent,
        raw: Any,
        author: Author,
        metadata: MessageMetadata,
        attachments: list[Attachment],
        links: list[Any] | None = None,
        channel: ChannelImpl[Any],
    ) -> None:
        self.id = id
        self.thread_id = thread_id
        self.text = text
        self.formatted = formatted
        self.raw = raw
        self.author = author
        self.metadata = metadata
        self.attachments = attachments
        self.links = links or []
        self._channel = channel

    def to_json(self) -> Any:
        return Message(
            id=self.id,
            thread_id=self.thread_id,
            text=self.text,
            formatted=self.formatted,
            raw=self.raw,
            author=self.author,
            metadata=self.metadata,
            attachments=self.attachments,
            links=self.links,
        ).to_json()

    async def edit(self, new_content: Any) -> SentMessage:
        # JSX paths are deferred to part B.
        postable = new_content
        await self._channel.adapter.edit_message(self.thread_id, self.id, postable)
        return self._channel._create_sent_message(self.id, postable)

    async def delete(self) -> None:
        await self._channel.adapter.delete_message(self.thread_id, self.id)

    async def add_reaction(self, emoji: Any) -> None:
        await self._channel.adapter.add_reaction(self.thread_id, self.id, emoji)

    async def remove_reaction(self, emoji: Any) -> None:
        await self._channel.adapter.remove_reaction(self.thread_id, self.id, emoji)


class ChannelImpl[TState]:
    """Concrete :class:`~chat.types.Channel` implementation.

    Construct with either an explicit ``adapter`` / ``state_adapter`` pair or
    with ``adapter_name`` for lazy resolution via the chat singleton
    (resolution will be wired up in part B alongside ``chat_singleton.py``).
    """

    __slots__ = (
        "_adapter",
        "_adapter_name",
        "_message_history",
        "_name",
        "_state_adapter_instance",
        "channel_visibility",
        "id",
        "is_dm",
    )

    def __init__(
        self,
        *,
        id: str,
        adapter: Any = None,
        adapter_name: str | None = None,
        state_adapter: StateAdapter | None = None,
        channel_visibility: ChannelVisibility = "unknown",
        is_dm: bool = False,
        message_history: MessageHistoryCache | None = None,
    ) -> None:
        self.id = id
        self.is_dm = is_dm
        self.channel_visibility = channel_visibility
        self._name: str | None = None

        if adapter is None and adapter_name is None:
            raise ValueError("ChannelImpl requires either adapter or adapter_name")

        self._adapter: Any = adapter
        self._adapter_name: str | None = adapter_name
        self._state_adapter_instance: StateAdapter | None = state_adapter
        self._message_history: MessageHistoryCache | None = message_history

    # ------------------------------------------------------------------
    # Adapter / state resolution
    # ------------------------------------------------------------------

    @property
    def adapter(self) -> Any:
        if self._adapter is not None:
            return self._adapter

        if self._adapter_name is None:
            raise RuntimeError("Channel has no adapter configured")

        # Lazy resolution via chat singleton — ported in part B.
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

    @property
    def name(self) -> str | None:
        return self._name

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    @property
    def state(self) -> Any:
        """Read the stored channel state.

        Returns a coroutine — ``await channel.state`` to resolve.
        """
        return self._state_adapter.get(f"{CHANNEL_STATE_KEY_PREFIX}{self.id}")

    async def set_state(
        self,
        new_state: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> None:
        """Set channel state — merges by default, or replaces when
        ``options={"replace": True}``.
        """
        key = f"{CHANNEL_STATE_KEY_PREFIX}{self.id}"

        if options and options.get("replace"):
            await self._state_adapter.set(key, new_state, THREAD_STATE_TTL_MS)
            return

        existing = await self._state_adapter.get(key)
        merged = {**(existing or {}), **new_state}
        await self._state_adapter.set(key, merged, THREAD_STATE_TTL_MS)

    # ------------------------------------------------------------------
    # Messages iterator (newest first)
    # ------------------------------------------------------------------

    @property
    def messages(self) -> AsyncIterable[Message[Any]]:
        """Iterate top-level channel messages, newest first.

        Uses :meth:`Adapter.fetch_channel_messages` when available, otherwise
        falls back to :meth:`Adapter.fetch_messages`. If no messages are
        returned and ``message_history`` is configured, yields cached
        history.
        """
        adapter = self.adapter
        channel_id = self.id
        message_history = self._message_history

        async def _iter() -> AsyncIterable[Message[Any]]:  # type: ignore[misc]
            cursor: str | None = None
            yielded_any = False

            while True:
                fetch_options: dict[str, Any] = {"direction": "backward"}
                if cursor is not None:
                    fetch_options["cursor"] = cursor

                fetch_channel = getattr(adapter, "fetch_channel_messages", None)
                if fetch_channel is not None:
                    result = await fetch_channel(channel_id, fetch_options)
                else:
                    result = await adapter.fetch_messages(channel_id, fetch_options)

                messages = result.get("messages", [])
                for msg in reversed(messages):
                    yielded_any = True
                    yield msg

                next_cursor = result.get("nextCursor")
                if not next_cursor or len(messages) == 0:
                    break
                cursor = next_cursor

            if not yielded_any and message_history is not None:
                cached = await message_history.get_messages(channel_id)
                for msg in reversed(cached):
                    yield msg

        return _iter()

    # ------------------------------------------------------------------
    # Threads iterator
    # ------------------------------------------------------------------

    def threads(self) -> AsyncIterable[ThreadSummary]:
        """Iterate threads in this channel, most recently active first."""
        adapter = self.adapter
        channel_id = self.id

        async def _iter() -> AsyncIterable[ThreadSummary]:  # type: ignore[misc]
            list_threads = getattr(adapter, "list_threads", None)
            if list_threads is None:
                return

            cursor: str | None = None
            while True:
                options: dict[str, Any] = {}
                if cursor is not None:
                    options["cursor"] = cursor
                result = await list_threads(channel_id, options)

                threads = result.get("threads", [])
                for thread in threads:
                    yield thread

                next_cursor = result.get("nextCursor")
                if not next_cursor or len(threads) == 0:
                    break
                cursor = next_cursor

        return _iter()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def fetch_metadata(self) -> ChannelInfo:
        """Fetch channel metadata via :meth:`Adapter.fetch_channel_info`.

        Returns a basic :class:`ChannelInfo` when the adapter doesn't
        implement ``fetch_channel_info``.
        """
        fetch_info = getattr(self.adapter, "fetch_channel_info", None)
        if fetch_info is not None:
            info = await fetch_info(self.id)
            self._name = info.get("name")
            return info

        return {"id": self.id, "isDM": self.is_dm, "metadata": {}}

    # ------------------------------------------------------------------
    # Post
    # ------------------------------------------------------------------

    async def post(self, message: Any) -> Any:
        """Post a message to this channel.

        Supports strings, ``PostableRaw`` / ``PostableMarkdown`` / ``PostableAst``
        dicts, :class:`~chat.postable_object.PostableObject`, and
        :class:`AsyncIterable` string streams (accumulated, then posted).
        """
        if is_postable_object(message):
            await self._handle_postable_object(message)
            return message

        if _is_async_iterable(message):
            accumulated = await _collect_stream(message)
            return await self._post_single_message({"markdown": accumulated})

        return await self._post_single_message(message)

    async def _handle_postable_object(self, obj: Any) -> None:
        async def post_fn(thread_id: str, msg: Any) -> Any:
            post_channel = getattr(self.adapter, "post_channel_message", None)
            if post_channel is not None:
                return await post_channel(thread_id, msg)
            return await self.adapter.post_message(thread_id, msg)

        await post_postable_object(obj, self.adapter, self.id, post_fn)

    async def _post_single_message(self, postable: AdapterPostableMessage) -> SentMessage:
        post_channel = getattr(self.adapter, "post_channel_message", None)
        if post_channel is not None:
            raw_message = await post_channel(self.id, postable)
        else:
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
            # Adapter returned a dict — coerce.
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

        return await schedule_message(self.id, postable, options)

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

    def to_json(self) -> SerializedChannel:
        return {
            "_type": "chat:Channel",
            "id": self.id,
            "adapterName": self._adapter_name or self.adapter.name,
            "channelVisibility": self.channel_visibility,
            "isDM": self.is_dm,
        }

    @classmethod
    def from_json(
        cls,
        data: SerializedChannel,
        adapter: Any = None,
    ) -> ChannelImpl[Any]:
        channel: ChannelImpl[Any] = cls(
            id=data["id"],
            adapter_name=data["adapterName"],
            channel_visibility=data.get("channelVisibility", "unknown"),
            is_dm=data.get("isDM", False),
        )
        if adapter is not None:
            channel._adapter = adapter
        return channel

    def __chat_serialize__(self) -> SerializedChannel:
        return self.to_json()

    @classmethod
    def __chat_deserialize__(cls, data: SerializedChannel) -> ChannelImpl[Any]:
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
            channel=self,
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


def derive_channel_id(adapter: Any, thread_id: str) -> str:
    """Derive the channel ID from a thread ID via the adapter."""
    return cast(str, adapter.channel_id_from_thread_id(thread_id))
