"""Main :class:`Chat` orchestrator — Python port of ``packages/chat/src/chat.ts``.

Holds the adapter registry, routes incoming messages/reactions/actions to
registered handlers, manages per-thread locks via the
:class:`~chat.types.StateAdapter`, and exposes factory methods for
:class:`~chat.channel.ChannelImpl` / :class:`~chat.thread.ThreadImpl`.

The Python port keeps the upstream semantics but trims two features that
are not yet on the critical path:

- ``debounce`` / ``queue`` concurrency strategies — stubbed to fall back to
  ``drop`` with an ``info`` log. ``concurrent`` and ``drop`` are fully
  implemented.
- Webhook request objects: upstream returns a ``Response`` from
  ``handle_webhook``; the Python port returns the adapter's raw result
  (typically a ``(status, headers, body)`` tuple). Adapters own the framework
  binding.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from chat.chat_singleton import (
    get_chat_singleton,
    has_chat_singleton,
    set_chat_singleton,
)
from chat.errors import ChatError, LockError
from chat.jsx_runtime import is_jsx, to_modal_element
from chat.logger import ConsoleLogger, Logger, LogLevel
from chat.message import Message
from chat.message_history import MessageHistoryCache
from chat.reviver import reviver as standalone_reviver
from chat.types import (
    Author,
    ChannelVisibility,
    FormattedContent,
    MessageMetadata,
    StateAdapter,
)

if TYPE_CHECKING:
    from chat.channel import ChannelImpl
    from chat.modals import ModalElement
    from chat.thread import ThreadImpl

DEFAULT_LOCK_TTL_MS: int = 30_000
"""Default TTL for per-thread locks (30s)."""

DEDUPE_TTL_MS: int = 5 * 60 * 1000
"""TTL for message deduplication entries (5 min)."""

MODAL_CONTEXT_TTL_MS: int = 24 * 60 * 60 * 1000
"""TTL for server-stored modal context (24h)."""

_SLACK_USER_ID_REGEX = re.compile(r"^U[A-Z0-9]+$", re.IGNORECASE)
_DISCORD_SNOWFLAKE_REGEX = re.compile(r"^\d{17,19}$")

ConcurrencyStrategy = Literal["drop", "queue", "debounce", "concurrent"]
LockScope = Literal["thread", "channel"]


class Chat:
    """Main chat orchestrator — adapter registry + handler dispatch.

    Mirrors upstream's ``Chat`` class. Generic params (TAdapters/TState) are
    dropped for pragmatic reasons — Python's structural typing lets handlers
    receive ``Any``-typed state without runtime overhead.
    """

    # ------------------------------------------------------------------
    # Singleton registration (class-level API matches upstream static methods)
    # ------------------------------------------------------------------

    def register_singleton(self) -> Chat:
        """Register this :class:`Chat` instance as the global singleton."""
        set_chat_singleton(self)
        return self

    @staticmethod
    def get_singleton() -> Chat:
        """Return the registered :class:`Chat` singleton.

        :raises RuntimeError: if no singleton has been registered.
        """
        return get_chat_singleton()  # type: ignore[return-value]

    @staticmethod
    def has_singleton() -> bool:
        return has_chat_singleton()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        user_name: str,
        adapters: dict[str, Any],
        state: StateAdapter,
        logger: Logger | LogLevel | None = None,
        streaming_update_interval_ms: int = 500,
        fallback_streaming_placeholder_text: str | None = "...",
        dedupe_ttl_ms: int | None = None,
        concurrency: ConcurrencyStrategy | dict[str, Any] | None = None,
        lock_scope: LockScope | Callable[[dict[str, Any]], Awaitable[LockScope]] | None = None,
        on_lock_conflict: Literal["drop", "force"]
        | Callable[[str, Message[Any]], Awaitable[Literal["drop", "force"]]]
        | None = None,
        message_history: dict[str, Any] | None = None,
    ) -> None:
        self._user_name = user_name
        self._state_adapter = state
        self._adapters: dict[str, Any] = dict(adapters)
        self._streaming_update_interval_ms = streaming_update_interval_ms
        self._fallback_streaming_placeholder_text = fallback_streaming_placeholder_text
        self._dedupe_ttl_ms = dedupe_ttl_ms if dedupe_ttl_ms is not None else DEDUPE_TTL_MS
        self._on_lock_conflict = on_lock_conflict
        self._lock_scope = lock_scope

        # Concurrency configuration — new ``concurrency`` option takes precedence.
        default_cfg: dict[str, Any] = {
            "debounce_ms": 1500,
            "max_concurrent": float("inf"),
            "max_queue_size": 10,
            "on_queue_full": "drop-oldest",
            "queue_entry_ttl_ms": 90_000,
        }
        if concurrency is None:
            self._concurrency_strategy: ConcurrencyStrategy = "drop"
            self._concurrency_config = default_cfg
        elif isinstance(concurrency, str):
            self._concurrency_strategy = concurrency
            self._concurrency_config = default_cfg
        else:
            self._concurrency_strategy = concurrency.get("strategy", "drop")
            self._concurrency_config = {**default_cfg, **concurrency}

        self._message_history = MessageHistoryCache(
            self._state_adapter,
            **(message_history or {}),
        )

        # Logger
        if isinstance(logger, str):
            self._logger: Logger = ConsoleLogger(logger)
        elif logger is None:
            self._logger = ConsoleLogger("info")
        else:
            self._logger = logger

        # Handler registries
        self._mention_handlers: list[Callable[..., Any]] = []
        self._direct_message_handlers: list[Callable[..., Any]] = []
        self._message_patterns: list[tuple[re.Pattern[str], Callable[..., Any]]] = []
        self._subscribed_message_handlers: list[Callable[..., Any]] = []
        self._reaction_handlers: list[tuple[list[Any], Callable[..., Any]]] = []
        self._action_handlers: list[tuple[list[str], Callable[..., Any]]] = []
        self._modal_submit_handlers: list[tuple[list[str], Callable[..., Any]]] = []
        self._modal_close_handlers: list[tuple[list[str], Callable[..., Any]]] = []
        self._slash_command_handlers: list[tuple[list[str], Callable[..., Any]]] = []
        self._assistant_thread_started_handlers: list[Callable[..., Any]] = []
        self._assistant_context_changed_handlers: list[Callable[..., Any]] = []
        self._app_home_opened_handlers: list[Callable[..., Any]] = []
        self._member_joined_channel_handlers: list[Callable[..., Any]] = []

        # Initialization state
        self._init_task: asyncio.Task[None] | None = None
        self._initialized = False

        # Webhook handlers — one per adapter
        self.webhooks: dict[str, Callable[..., Awaitable[Any]]] = {}
        for name in self._adapters:
            self.webhooks[name] = self._make_webhook_handler(name)

        self._logger.debug("Chat instance created", {"adapters": list(adapters.keys())})

    def _make_webhook_handler(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def handler(request: Any, options: dict[str, Any] | None = None) -> Any:
            return await self.handle_webhook(name, request, options)

        return handler

    # ------------------------------------------------------------------
    # Webhook / initialization
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        adapter_name: str,
        request: Any,
        options: dict[str, Any] | None = None,
    ) -> Any:
        """Handle a webhook request for a specific adapter."""
        await self._ensure_initialized()
        adapter = self._adapters.get(adapter_name)
        if adapter is None:
            raise ChatError(f"Unknown adapter: {adapter_name}", "UNKNOWN_ADAPTER")
        return await adapter.handle_webhook(request, options)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if self._init_task is None:
            self._init_task = asyncio.create_task(self._do_initialize())
        await self._init_task

    async def _do_initialize(self) -> None:
        self._logger.info("Initializing chat instance...")
        await self._state_adapter.connect()
        self._logger.debug("State connected")

        init_coros = []
        for adapter in self._adapters.values():

            async def _init(a: Any = adapter) -> None:
                self._logger.debug("Initializing adapter", a.name)
                await a.initialize(self)
                self._logger.debug("Adapter initialized", a.name)

            init_coros.append(_init())
        await asyncio.gather(*init_coros)

        self._initialized = True
        self._logger.info("Chat instance initialized", {"adapters": list(self._adapters)})

    async def initialize(self) -> None:
        """Explicit initialization (normally done lazily on first webhook)."""
        await self._ensure_initialized()

    async def shutdown(self) -> None:
        """Gracefully shut down adapters and state."""
        self._logger.info("Shutting down chat instance...")

        async def _disconnect(adapter: Any) -> None:
            disconnect = getattr(adapter, "disconnect", None)
            if disconnect is None:
                return
            self._logger.debug("Disconnecting adapter", adapter.name)
            await disconnect()
            self._logger.debug("Adapter disconnected", adapter.name)

        results = await asyncio.gather(
            *(_disconnect(a) for a in self._adapters.values()),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                self._logger.error("Adapter disconnect failed", r)
        await self._state_adapter.disconnect()
        self._initialized = False
        self._init_task = None
        self._logger.info("Chat instance shut down")

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def on_new_mention(self, handler: Callable[..., Any]) -> None:
        """Register a handler for new @-mentions in unsubscribed threads."""
        self._mention_handlers.append(handler)
        self._logger.debug("Registered mention handler")

    def on_direct_message(self, handler: Callable[..., Any]) -> None:
        """Register a handler for direct messages."""
        self._direct_message_handlers.append(handler)
        self._logger.debug("Registered direct message handler")

    def on_new_message(self, pattern: str | re.Pattern[str], handler: Callable[..., Any]) -> None:
        """Register a handler for messages matching a regex pattern."""
        compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
        self._message_patterns.append((compiled, handler))
        self._logger.debug("Registered message pattern handler", {"pattern": compiled.pattern})

    def on_subscribed_message(self, handler: Callable[..., Any]) -> None:
        """Register a handler for messages in subscribed threads."""
        self._subscribed_message_handlers.append(handler)
        self._logger.debug("Registered subscribed message handler")

    def on_reaction(
        self,
        emoji_or_handler: list[Any] | Callable[..., Any],
        handler: Callable[..., Any] | None = None,
    ) -> None:
        """Register a handler for reaction events.

        Two call signatures:
        - ``on_reaction(handler)`` — handle all reactions
        - ``on_reaction([emoji, ...], handler)`` — filter by emoji list
        """
        if callable(emoji_or_handler) and handler is None:
            self._reaction_handlers.append(([], emoji_or_handler))
            self._logger.debug("Registered reaction handler for all emoji")
        elif handler is not None:
            assert isinstance(emoji_or_handler, list)
            self._reaction_handlers.append((list(emoji_or_handler), handler))
            self._logger.debug("Registered reaction handler")

    def on_action(
        self,
        action_ids_or_handler: str | list[str] | Callable[..., Any],
        handler: Callable[..., Any] | None = None,
    ) -> None:
        """Register a handler for action events (button clicks)."""
        if callable(action_ids_or_handler) and handler is None:
            self._action_handlers.append(([], action_ids_or_handler))
            self._logger.debug("Registered action handler for all actions")
        elif handler is not None:
            if isinstance(action_ids_or_handler, str):
                ids = [action_ids_or_handler]
            else:
                assert isinstance(action_ids_or_handler, list)
                ids = list(action_ids_or_handler)
            self._action_handlers.append((ids, handler))
            self._logger.debug("Registered action handler", {"actionIds": ids})

    def on_modal_submit(
        self,
        callback_ids_or_handler: str | list[str] | Callable[..., Any],
        handler: Callable[..., Any] | None = None,
    ) -> None:
        """Register a handler for modal form submissions."""
        if callable(callback_ids_or_handler) and handler is None:
            self._modal_submit_handlers.append(([], callback_ids_or_handler))
            self._logger.debug("Registered modal submit handler for all modals")
        elif handler is not None:
            if isinstance(callback_ids_or_handler, str):
                ids = [callback_ids_or_handler]
            else:
                assert isinstance(callback_ids_or_handler, list)
                ids = list(callback_ids_or_handler)
            self._modal_submit_handlers.append((ids, handler))
            self._logger.debug("Registered modal submit handler", {"callbackIds": ids})

    def on_modal_close(
        self,
        callback_ids_or_handler: str | list[str] | Callable[..., Any],
        handler: Callable[..., Any] | None = None,
    ) -> None:
        """Register a handler for modal close/cancel events."""
        if callable(callback_ids_or_handler) and handler is None:
            self._modal_close_handlers.append(([], callback_ids_or_handler))
            self._logger.debug("Registered modal close handler for all modals")
        elif handler is not None:
            if isinstance(callback_ids_or_handler, str):
                ids = [callback_ids_or_handler]
            else:
                assert isinstance(callback_ids_or_handler, list)
                ids = list(callback_ids_or_handler)
            self._modal_close_handlers.append((ids, handler))
            self._logger.debug("Registered modal close handler", {"callbackIds": ids})

    def on_slash_command(
        self,
        commands_or_handler: str | list[str] | Callable[..., Any],
        handler: Callable[..., Any] | None = None,
    ) -> None:
        """Register a handler for slash command events."""
        if callable(commands_or_handler) and handler is None:
            self._slash_command_handlers.append(([], commands_or_handler))
            self._logger.debug("Registered slash command handler for all commands")
        elif handler is not None:
            if isinstance(commands_or_handler, str):
                cmds = [commands_or_handler]
            else:
                assert isinstance(commands_or_handler, list)
                cmds = list(commands_or_handler)
            normalized = [c if c.startswith("/") else f"/{c}" for c in cmds]
            self._slash_command_handlers.append((normalized, handler))
            self._logger.debug("Registered slash command handler", {"commands": normalized})

    def on_assistant_thread_started(self, handler: Callable[..., Any]) -> None:
        self._assistant_thread_started_handlers.append(handler)
        self._logger.debug("Registered assistant thread started handler")

    def on_assistant_context_changed(self, handler: Callable[..., Any]) -> None:
        self._assistant_context_changed_handlers.append(handler)
        self._logger.debug("Registered assistant context changed handler")

    def on_app_home_opened(self, handler: Callable[..., Any]) -> None:
        self._app_home_opened_handlers.append(handler)
        self._logger.debug("Registered app home opened handler")

    def on_member_joined_channel(self, handler: Callable[..., Any]) -> None:
        self._member_joined_channel_handlers.append(handler)
        self._logger.debug("Registered member joined channel handler")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_adapter(self, name: str) -> Any:
        """Return the adapter registered under *name*, or ``None``."""
        return self._adapters.get(name)

    def get_state(self) -> StateAdapter:
        return self._state_adapter

    def get_user_name(self) -> str:
        return self._user_name

    def get_logger(self, prefix: str | None = None) -> Logger:
        if prefix:
            return self._logger.child(prefix)
        return self._logger

    def reviver(self) -> Callable[[str, Any], Any]:
        """Return a JSON reviver that revives Thread/Channel/Message objects.

        Also registers this :class:`Chat` as the singleton so revived threads
        can lazily resolve their adapter.
        """
        self.register_singleton()
        return standalone_reviver

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    def channel(self, channel_id: str) -> ChannelImpl[Any]:
        """Get a :class:`ChannelImpl` by its platform-qualified ID."""
        from chat.channel import ChannelImpl

        adapter_name = channel_id.split(":", 1)[0]
        if not adapter_name:
            raise ChatError(f"Invalid channel ID: {channel_id}", "INVALID_CHANNEL_ID")
        adapter = self._adapters.get(adapter_name)
        if adapter is None:
            raise ChatError(
                f'Adapter "{adapter_name}" not found for channel ID "{channel_id}"',
                "ADAPTER_NOT_FOUND",
            )
        return ChannelImpl(
            id=channel_id,
            adapter=adapter,
            state_adapter=self._state_adapter,
        )

    async def open_dm(self, user: str | Author) -> ThreadImpl[Any]:
        """Open a DM conversation with the given user."""
        user_id = user if isinstance(user, str) else user.user_id
        adapter = self._infer_adapter_from_user_id(user_id)
        open_dm = getattr(adapter, "open_dm", None)
        if open_dm is None:
            raise ChatError(
                f'Adapter "{adapter.name}" does not support openDM',
                "NOT_SUPPORTED",
            )
        thread_id = await open_dm(user_id)
        return self._create_thread(adapter, thread_id, _empty_message(thread_id), False)

    def _infer_adapter_from_user_id(self, user_id: str) -> Any:
        # Google Chat: users/...
        if user_id.startswith("users/") and (a := self._adapters.get("gchat")):
            return a
        # Teams: 29:...
        if user_id.startswith("29:") and (a := self._adapters.get("teams")):
            return a
        # Slack: U...
        if _SLACK_USER_ID_REGEX.match(user_id) and (a := self._adapters.get("slack")):
            return a
        # Discord snowflake
        if _DISCORD_SNOWFLAKE_REGEX.match(user_id) and (a := self._adapters.get("discord")):
            return a
        raise ChatError(
            f'Cannot infer adapter from userId "{user_id}". '
            "Expected format: Slack (U...), Teams (29:...), Google Chat (users/...), "
            "or Discord (numeric snowflake).",
            "UNKNOWN_USER_ID_FORMAT",
        )

    def _create_thread(
        self,
        adapter: Any,
        thread_id: str,
        initial_message: Message[Any],
        is_subscribed_context: bool = False,
    ) -> ThreadImpl[Any]:
        from chat.thread import ThreadImpl

        channel_id = adapter.channel_id_from_thread_id(thread_id)
        is_dm = False
        if hasattr(adapter, "is_dm") and callable(adapter.is_dm):
            is_dm = adapter.is_dm(thread_id) or False
        channel_visibility: ChannelVisibility = "unknown"
        if hasattr(adapter, "get_channel_visibility") and callable(adapter.get_channel_visibility):
            channel_visibility = adapter.get_channel_visibility(thread_id) or "unknown"

        return ThreadImpl(
            id=thread_id,
            adapter=adapter,
            channel_id=channel_id,
            state_adapter=self._state_adapter,
            initial_message=initial_message,
            is_subscribed_context=is_subscribed_context,
            is_dm=is_dm,
            channel_visibility=channel_visibility,
            current_message=initial_message,
            logger=self._logger,
            streaming_update_interval_ms=self._streaming_update_interval_ms,
            fallback_streaming_placeholder_text=self._fallback_streaming_placeholder_text,
            message_history=self._message_history
            if getattr(adapter, "persist_message_history", False)
            else None,
        )

    # ------------------------------------------------------------------
    # Process* dispatchers — fire-and-forget wrappers for adapters
    # ------------------------------------------------------------------

    def process_message(
        self,
        adapter: Any,
        thread_id: str,
        message_or_factory: Message[Any] | Callable[[], Awaitable[Message[Any]]],
        options: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget dispatch from an adapter to :meth:`handle_incoming_message`."""

        async def _run() -> None:
            try:
                msg = (
                    await message_or_factory()
                    if callable(message_or_factory)
                    else message_or_factory
                )
                await self.handle_incoming_message(adapter, thread_id, msg)
            except Exception as err:
                self._logger.error(
                    "Message processing error", {"error": err, "threadId": thread_id}
                )

        task = asyncio.create_task(_run())
        if options and (wu := options.get("waitUntil")):
            wu(task)

    def process_reaction(
        self,
        event: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> None:
        async def _run() -> None:
            try:
                await self._handle_reaction_event(event)
            except Exception as err:
                self._logger.error(
                    "Reaction processing error",
                    {
                        "error": err,
                        "emoji": event.get("emoji"),
                        "messageId": event.get("messageId"),
                    },
                )

        task = asyncio.create_task(_run())
        if options and (wu := options.get("waitUntil")):
            wu(task)

    def process_action(
        self,
        event: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> asyncio.Task[None]:
        async def _run() -> None:
            try:
                await self._handle_action_event(event, options)
            except Exception as err:
                self._logger.error(
                    "Action processing error",
                    {
                        "error": err,
                        "actionId": event.get("actionId"),
                        "messageId": event.get("messageId"),
                    },
                )

        task = asyncio.create_task(_run())
        if options and (wu := options.get("waitUntil")):
            wu(task)
        return task

    async def process_modal_submit(
        self,
        event: dict[str, Any],
        context_id: str | None = None,
        _options: dict[str, Any] | None = None,
    ) -> Any:
        related = await self._retrieve_modal_context(event["adapter"].name, context_id)
        full_event = {**event, **related}
        for callback_ids, handler in self._modal_submit_handlers:
            if not callback_ids or event["callbackId"] in callback_ids:
                try:
                    response = await handler(full_event)
                    if response:
                        return response
                except Exception as err:
                    self._logger.error(
                        "Modal submit handler error",
                        {"error": err, "callbackId": event["callbackId"]},
                    )
        return None

    def process_modal_close(
        self,
        event: dict[str, Any],
        context_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        async def _run() -> None:
            try:
                related = await self._retrieve_modal_context(event["adapter"].name, context_id)
                full_event = {**event, **related}
                for callback_ids, handler in self._modal_close_handlers:
                    if not callback_ids or event["callbackId"] in callback_ids:
                        await handler(full_event)
            except Exception as err:
                self._logger.error(
                    "Modal close handler error",
                    {"error": err, "callbackId": event["callbackId"]},
                )

        task = asyncio.create_task(_run())
        if options and (wu := options.get("waitUntil")):
            wu(task)

    def process_slash_command(
        self,
        event: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> None:
        async def _run() -> None:
            try:
                await self._handle_slash_command_event(event, options)
            except Exception as err:
                self._logger.error(
                    "Slash command processing error",
                    {"error": err, "command": event.get("command"), "text": event.get("text")},
                )

        task = asyncio.create_task(_run())
        if options and (wu := options.get("waitUntil")):
            wu(task)

    def process_assistant_thread_started(
        self, event: dict[str, Any], options: dict[str, Any] | None = None
    ) -> None:
        self._fire_and_forget(
            self._run_simple_handlers(self._assistant_thread_started_handlers, event),
            "Assistant thread started handler error",
            {"threadId": event.get("threadId")},
            options,
        )

    def process_assistant_context_changed(
        self, event: dict[str, Any], options: dict[str, Any] | None = None
    ) -> None:
        self._fire_and_forget(
            self._run_simple_handlers(self._assistant_context_changed_handlers, event),
            "Assistant context changed handler error",
            {"threadId": event.get("threadId")},
            options,
        )

    def process_app_home_opened(
        self, event: dict[str, Any], options: dict[str, Any] | None = None
    ) -> None:
        self._fire_and_forget(
            self._run_simple_handlers(self._app_home_opened_handlers, event),
            "App home opened handler error",
            {"userId": event.get("userId")},
            options,
        )

    def process_member_joined_channel(
        self, event: dict[str, Any], options: dict[str, Any] | None = None
    ) -> None:
        self._fire_and_forget(
            self._run_simple_handlers(self._member_joined_channel_handlers, event),
            "Member joined channel handler error",
            {"channelId": event.get("channelId"), "userId": event.get("userId")},
            options,
        )

    async def _run_simple_handlers(self, handlers: list[Callable[..., Any]], event: Any) -> None:
        for h in handlers:
            await h(event)

    def _fire_and_forget(
        self,
        coro: Awaitable[None],
        error_msg: str,
        error_ctx: dict[str, Any],
        options: dict[str, Any] | None,
    ) -> None:
        async def _run() -> None:
            try:
                await coro
            except Exception as err:
                self._logger.error(error_msg, {"error": err, **error_ctx})

        task = asyncio.create_task(_run())
        if options and (wu := options.get("waitUntil")):
            wu(task)

    # ------------------------------------------------------------------
    # Handle* — internal event processing
    # ------------------------------------------------------------------

    async def _handle_slash_command_event(
        self,
        event: dict[str, Any],
        options: dict[str, Any] | None,
    ) -> None:
        from chat.channel import ChannelImpl

        adapter = event["adapter"]
        self._logger.debug(
            "Incoming slash command",
            {
                "adapter": adapter.name,
                "command": event["command"],
                "text": event.get("text"),
                "user": event["user"].user_name,
            },
        )
        if event["user"].is_me:
            self._logger.debug("Skipping slash command from self", {"command": event["command"]})
            return

        channel: ChannelImpl[Any] = ChannelImpl(
            id=event["channelId"],
            adapter=adapter,
            state_adapter=self._state_adapter,
        )

        full_event = {**event, "channel": channel}
        full_event["open_modal"] = self._build_open_modal(event, options, channel=channel)

        self._logger.debug(
            "Checking slash command handlers",
            {"handlerCount": len(self._slash_command_handlers), "command": event["command"]},
        )
        for commands, handler in self._slash_command_handlers:
            if not commands:
                self._logger.debug("Running catch-all slash command handler")
                await handler(full_event)
                continue
            if event["command"] in commands:
                self._logger.debug(
                    "Running matched slash command handler", {"command": event["command"]}
                )
                await handler(full_event)

    def _build_open_modal(
        self,
        event: dict[str, Any],
        options: dict[str, Any] | None,
        *,
        thread: ThreadImpl[Any] | None = None,
        channel: ChannelImpl[Any] | None = None,
    ) -> Callable[[Any], Awaitable[Any]]:
        adapter = event["adapter"]

        async def open_modal(modal: Any) -> Any:
            on_open = options.get("onOpenModal") if options else None
            trigger_id = event.get("triggerId")
            if not (trigger_id or on_open):
                self._logger.warn("Cannot open modal: no triggerId available")
                return None
            adapter_open = getattr(adapter, "open_modal", None)
            if not (on_open or adapter_open):
                self._logger.warn(f"Cannot open modal: {adapter.name} does not support modals")
                return None

            modal_element: ModalElement = modal
            if is_jsx(modal):
                converted = to_modal_element(modal)
                if converted is None:
                    raise ValueError("Invalid JSX element: must be a Modal element")
                modal_element = converted

            context_id = str(uuid.uuid4())
            await self._store_modal_context(
                adapter.name, context_id, thread=thread, message=None, channel=channel
            )
            if on_open:
                return await on_open(modal_element, context_id)
            if trigger_id and adapter_open:
                return await adapter_open(trigger_id, modal_element, context_id)
            return None

        return open_modal

    async def _handle_action_event(
        self,
        event: dict[str, Any],
        options: dict[str, Any] | None,
    ) -> None:
        adapter = event["adapter"]
        self._logger.debug(
            "Incoming action",
            {
                "adapter": adapter.name,
                "actionId": event["actionId"],
                "value": event.get("value"),
                "user": event["user"].user_name,
                "messageId": event.get("messageId"),
                "threadId": event.get("threadId"),
            },
        )
        if event["user"].is_me:
            self._logger.debug("Skipping action from self", {"actionId": event["actionId"]})
            return

        thread = None
        if event.get("threadId"):
            msg_for_thread = (
                Message(
                    id=event["messageId"],
                    thread_id=event["threadId"],
                    text="",
                    formatted={"type": "root", "children": []},
                    raw=event.get("raw"),
                    author=event["user"],
                    metadata=MessageMetadata(date_sent=datetime.now(UTC), edited=False),
                )
                if event.get("messageId")
                else _empty_message(event["threadId"])
            )
            thread = self._create_thread(adapter, event["threadId"], msg_for_thread, False)

        full_event = {**event, "thread": thread}
        full_event["open_modal"] = self._build_open_modal(event, options, thread=thread)

        self._logger.debug(
            "Checking action handlers",
            {"handlerCount": len(self._action_handlers), "actionId": event["actionId"]},
        )
        for action_ids, handler in self._action_handlers:
            if not action_ids:
                self._logger.debug("Running catch-all action handler")
                await handler(full_event)
                continue
            if event["actionId"] in action_ids:
                self._logger.debug(
                    "Running matched action handler", {"actionId": event["actionId"]}
                )
                await handler(full_event)

    async def _handle_reaction_event(self, event: dict[str, Any]) -> None:
        adapter = event.get("adapter")
        self._logger.debug(
            "Incoming reaction",
            {
                "adapter": adapter.name if adapter else None,
                "emoji": event.get("emoji"),
                "rawEmoji": event.get("rawEmoji"),
                "added": event.get("added"),
                "user": event["user"].user_name,
                "messageId": event.get("messageId"),
                "threadId": event.get("threadId"),
            },
        )
        if event["user"].is_me:
            self._logger.debug("Skipping reaction from self", {"emoji": event.get("emoji")})
            return
        if adapter is None:
            self._logger.error("Reaction event missing adapter")
            return

        is_subscribed = await self._state_adapter.is_subscribed(event["threadId"])
        thread = self._create_thread(
            adapter,
            event["threadId"],
            event.get("message") or _empty_message(event["threadId"]),
            is_subscribed,
        )
        full_event = {**event, "adapter": adapter, "thread": thread}

        self._logger.debug(
            "Checking reaction handlers",
            {
                "handlerCount": len(self._reaction_handlers),
                "emoji": getattr(event.get("emoji"), "name", None),
                "rawEmoji": event.get("rawEmoji"),
            },
        )
        for emoji_filter, handler in self._reaction_handlers:
            if not emoji_filter:
                self._logger.debug("Running catch-all reaction handler")
                await handler(full_event)
                continue

            def _matches(f: Any) -> bool:
                if f is event["emoji"]:
                    return True
                filter_name = f if isinstance(f, str) else getattr(f, "name", None)
                emoji_name = getattr(event["emoji"], "name", None)
                return filter_name == emoji_name or filter_name == event.get("rawEmoji")

            if any(_matches(f) for f in emoji_filter):
                self._logger.debug("Running matched reaction handler")
                await handler(full_event)

    # ------------------------------------------------------------------
    # Modal context storage
    # ------------------------------------------------------------------

    async def _store_modal_context(
        self,
        adapter_name: str,
        context_id: str,
        *,
        thread: ThreadImpl[Any] | None = None,
        message: Message[Any] | None = None,
        channel: ChannelImpl[Any] | None = None,
    ) -> None:
        key = f"modal-context:{adapter_name}:{context_id}"
        context: dict[str, Any] = {
            "thread": thread.to_json() if thread else None,
            "message": message.to_json() if message else None,
            "channel": channel.to_json() if channel else None,
        }
        try:
            await self._state_adapter.set(key, context, MODAL_CONTEXT_TTL_MS)
        except Exception as err:
            self._logger.error(
                "Failed to store modal context", {"contextId": context_id, "error": err}
            )

    async def _retrieve_modal_context(
        self,
        adapter_name: str,
        context_id: str | None,
    ) -> dict[str, Any]:
        if not context_id:
            return {"relatedThread": None, "relatedMessage": None, "relatedChannel": None}
        from chat.channel import ChannelImpl
        from chat.thread import ThreadImpl

        key = f"modal-context:{adapter_name}:{context_id}"
        stored = await self._state_adapter.get(key)
        if not stored:
            return {"relatedThread": None, "relatedMessage": None, "relatedChannel": None}

        await self._state_adapter.delete(key)
        adapter = self._adapters.get(adapter_name)

        related_thread: Any = None
        if stored.get("thread"):
            related_thread = ThreadImpl.from_json(stored["thread"])
            if adapter is not None:
                related_thread._adapter = adapter

        related_message: Any = None
        if stored.get("message"):
            related_message = Message.from_json(stored["message"])

        related_channel: Any = None
        if stored.get("channel"):
            related_channel = ChannelImpl.from_json(stored["channel"])
            if adapter is not None:
                related_channel._adapter = adapter

        return {
            "relatedThread": related_thread,
            "relatedMessage": related_message,
            "relatedChannel": related_channel,
        }

    # ------------------------------------------------------------------
    # handle_incoming_message — main entry point
    # ------------------------------------------------------------------

    async def handle_incoming_message(
        self,
        adapter: Any,
        thread_id: str,
        message: Message[Any],
    ) -> None:
        """Main entry point — dedupe, lock, dispatch.

        Adapters call this (or :meth:`process_message`) after parsing an
        incoming webhook.
        """
        self._logger.debug(
            "Incoming message",
            {
                "adapter": adapter.name,
                "threadId": thread_id,
                "messageId": message.id,
                "text": message.text,
                "author": message.author.user_name,
                "authorUserId": message.author.user_id,
                "isBot": message.author.is_bot,
                "isMe": message.author.is_me,
            },
        )

        if message.author.is_me:
            self._logger.debug(
                "Skipping message from self (isMe=true)",
                {
                    "adapter": adapter.name,
                    "threadId": thread_id,
                    "author": message.author.user_name,
                },
            )
            return

        # Dedupe
        dedupe_key = f"dedupe:{adapter.name}:{message.id}"
        is_first = await self._state_adapter.set_if_not_exists(
            dedupe_key, True, self._dedupe_ttl_ms
        )
        if not is_first:
            self._logger.debug(
                "Skipping duplicate message",
                {"adapter": adapter.name, "messageId": message.id},
            )
            return

        # Persist history before lock
        if getattr(adapter, "persist_message_history", False):
            channel_id = adapter.channel_id_from_thread_id(thread_id)
            appends = [self._message_history.append(thread_id, message)]
            if channel_id != thread_id:
                appends.append(self._message_history.append(channel_id, message))
            await asyncio.gather(*appends)

        lock_key = await self._get_lock_key(adapter, thread_id)
        strategy = self._concurrency_strategy

        if strategy == "concurrent":
            await self._dispatch_to_handlers(adapter, thread_id, message)
            return

        if strategy in ("queue", "debounce"):
            # Pragmatic fallback — full queue/debounce machinery can be ported later.
            self._logger.info(
                "Concurrency strategy falling back to drop",
                {"strategy": strategy, "threadId": thread_id},
            )

        await self._handle_drop(adapter, thread_id, lock_key, message)

    async def _handle_drop(
        self,
        adapter: Any,
        thread_id: str,
        lock_key: str,
        message: Message[Any],
    ) -> None:
        lock = await self._state_adapter.acquire_lock(lock_key, DEFAULT_LOCK_TTL_MS)
        if lock is None:
            # Legacy on_lock_conflict support
            resolution: Any = "drop"
            if callable(self._on_lock_conflict):
                resolution = await self._on_lock_conflict(thread_id, message)
            elif self._on_lock_conflict is not None:
                resolution = self._on_lock_conflict
            if resolution == "force":
                self._logger.info(
                    "Force-releasing lock on thread", {"threadId": thread_id, "lockKey": lock_key}
                )
                await self._state_adapter.force_release_lock(lock_key)
                lock = await self._state_adapter.acquire_lock(lock_key, DEFAULT_LOCK_TTL_MS)
            if lock is None:
                self._logger.warn(
                    "Could not acquire lock on thread",
                    {"threadId": thread_id, "lockKey": lock_key},
                )
                raise LockError(
                    f"Could not acquire lock on thread {thread_id}. "
                    "Another instance may be processing."
                )

        # State adapters may return either a ``Lock`` dataclass or a dict — we
        # accept both to keep the memory/redis/pg/ioredis backends pluggable.
        lock_token = lock["token"] if isinstance(lock, dict) else lock.token
        self._logger.debug(
            "Lock acquired", {"threadId": thread_id, "lockKey": lock_key, "token": lock_token}
        )
        try:
            await self._dispatch_to_handlers(adapter, thread_id, message)
        finally:
            await self._state_adapter.release_lock(lock)
            self._logger.debug("Lock released", {"threadId": thread_id, "lockKey": lock_key})

    async def _get_lock_key(self, adapter: Any, thread_id: str) -> str:
        channel_id = adapter.channel_id_from_thread_id(thread_id)
        scope: LockScope
        if callable(self._lock_scope):
            is_dm = False
            if hasattr(adapter, "is_dm") and callable(adapter.is_dm):
                is_dm = adapter.is_dm(thread_id) or False
            scope = await self._lock_scope(
                {"adapter": adapter, "channelId": channel_id, "isDM": is_dm, "threadId": thread_id}
            )
        else:
            scope = self._lock_scope or getattr(adapter, "lock_scope", None) or "thread"
        return channel_id if scope == "channel" else thread_id

    async def _dispatch_to_handlers(
        self,
        adapter: Any,
        thread_id: str,
        message: Message[Any],
        context: dict[str, Any] | None = None,
    ) -> None:
        # Set is_mention flag (preserve existing)
        if not message.is_mention:
            message.is_mention = self._detect_mention(adapter, message)

        is_subscribed = await self._state_adapter.is_subscribed(thread_id)
        self._logger.debug(
            "Subscription check",
            {
                "threadId": thread_id,
                "isSubscribed": is_subscribed,
                "subscribedHandlerCount": len(self._subscribed_message_handlers),
            },
        )
        thread = self._create_thread(adapter, thread_id, message, is_subscribed)

        # DM routing
        is_dm = False
        if hasattr(adapter, "is_dm") and callable(adapter.is_dm):
            is_dm = adapter.is_dm(thread_id) or False
        if is_dm and self._direct_message_handlers:
            self._logger.debug(
                "Direct message received - calling handlers",
                {"threadId": thread_id, "handlerCount": len(self._direct_message_handlers)},
            )
            channel = thread.channel
            for h in self._direct_message_handlers:
                await h(thread, message, channel, context)
            return
        if is_dm:
            message.is_mention = True

        # Subscribed thread
        if is_subscribed:
            self._logger.debug(
                "Message in subscribed thread - calling handlers",
                {"threadId": thread_id, "handlerCount": len(self._subscribed_message_handlers)},
            )
            for h in self._subscribed_message_handlers:
                await h(thread, message, context)
            return

        # @-mention
        if message.is_mention:
            self._logger.debug("Bot mentioned", {"threadId": thread_id, "text": message.text[:100]})
            for h in self._mention_handlers:
                await h(thread, message, context)
            return

        # Pattern matching
        self._logger.debug(
            "Checking message patterns",
            {
                "patternCount": len(self._message_patterns),
                "patterns": [p.pattern for p, _ in self._message_patterns],
                "messageText": message.text,
            },
        )
        matched = False
        for pattern, handler in self._message_patterns:
            if pattern.search(message.text):
                self._logger.debug(
                    "Message matched pattern - calling handler", {"pattern": pattern.pattern}
                )
                matched = True
                await handler(thread, message, context)
        if not matched:
            self._logger.debug(
                "No handlers matched message",
                {"threadId": thread_id, "text": message.text[:100]},
            )

    def _detect_mention(self, adapter: Any, message: Message[Any]) -> bool:
        bot_user_name = getattr(adapter, "user_name", None) or self._user_name
        bot_user_id = getattr(adapter, "bot_user_id", None)

        username_re = re.compile(rf"@{re.escape(bot_user_name)}\b", re.IGNORECASE)
        if username_re.search(message.text):
            return True

        if bot_user_id:
            user_id_re = re.compile(rf"@{re.escape(bot_user_id)}\b", re.IGNORECASE)
            if user_id_re.search(message.text):
                return True
            discord_re = re.compile(rf"<@!?{re.escape(bot_user_id)}>", re.IGNORECASE)
            if discord_re.search(message.text):
                return True

        return False


def _empty_message(thread_id: str) -> Message[Any]:
    """Placeholder :class:`Message` used when an event has no real message."""
    return Message(
        id="",
        thread_id=thread_id,
        text="",
        formatted=cast_formatted({"type": "root", "children": []}),
        raw=None,
        author=Author(user_id="", user_name="", full_name="", is_bot=False, is_me=False),
        metadata=MessageMetadata(date_sent=datetime.now(UTC), edited=False),
    )


def cast_formatted(d: dict[str, Any]) -> FormattedContent:
    """Tiny identity cast to satisfy mypy — :data:`FormattedContent` is ``dict[str, Any]``."""
    return d


__all__ = [
    "DEDUPE_TTL_MS",
    "DEFAULT_LOCK_TTL_MS",
    "MODAL_CONTEXT_TTL_MS",
    "Chat",
    "ConcurrencyStrategy",
    "LockScope",
]
