"""Shared test helpers for cross-package integration suites.

These helpers assemble a :class:`~chat.chat.Chat` against a caller-supplied
:class:`~chat.types.StateAdapter` plus the duck-typed mock adapter from
:mod:`chat.mock_adapter`. Every integration test in this package composes
the same primitives — differing only in *which* state backend they wire up.

Keeping the helpers in the package ``src/`` tree means they survive
``uv build`` and can be reused by downstream consumers who want to write
their own adapter regression tests against the published wheel.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from chat.chat import Chat
from chat.chat_singleton import clear_chat_singleton
from chat.logger import Logger
from chat.message import Message
from chat.mock_adapter import create_mock_adapter, create_test_message, mock_logger
from chat.types import Author, MessageMetadata


@dataclass(slots=True)
class HandlerSpy:
    """Simple async-callable spy — records each call and optionally replies.

    The default :meth:`__call__` appends ``(thread, message)`` to
    :attr:`calls` and, if :attr:`reply` is truthy, posts it back to the
    thread. Tests assert against :attr:`calls` and against the adapter's
    ``post_message`` mock to verify both dispatch and reply round-trip
    through the state adapter's lock/dedupe/cache paths.
    """

    reply: str | None = "ack"
    calls: list[tuple[Any, Any]] = field(default_factory=list)
    raise_on_call: BaseException | None = None

    async def __call__(self, thread: Any, message: Any, ctx: Any = None) -> None:
        self.calls.append((thread, message))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self.reply is not None:
            await thread.post(self.reply)


def build_chat(
    *,
    state: Any,
    adapter_name: str = "slack",
    logger: Logger | None = None,
    **chat_kwargs: Any,
) -> tuple[Chat, Any]:
    """Build a :class:`Chat` + mock adapter pair for an integration test.

    Clears any pre-existing singleton registration so tests can run in any
    order without leaking global state.
    """

    clear_chat_singleton()
    adapter = create_mock_adapter(adapter_name)
    chat = Chat(
        user_name="testbot",
        adapters={adapter_name: adapter},
        state=state,
        logger=logger or mock_logger,  # type: ignore[arg-type]
        **chat_kwargs,
    )
    return chat, adapter


def build_multi_adapter_chat(
    *,
    state: Any,
    adapter_names: list[str],
    logger: Logger | None = None,
    **chat_kwargs: Any,
) -> tuple[Chat, dict[str, Any]]:
    """Build a :class:`Chat` with several duck-typed mock adapters.

    Mirrors the upstream multi-platform wiring used by apps that run in
    front of Slack + Teams + Discord simultaneously.
    """

    clear_chat_singleton()
    adapters = {name: create_mock_adapter(name) for name in adapter_names}
    chat = Chat(
        user_name="testbot",
        adapters=adapters,
        state=state,
        logger=logger or mock_logger,  # type: ignore[arg-type]
        **chat_kwargs,
    )
    return chat, adapters


def make_incoming_message(
    *,
    thread_id: str,
    text: str,
    message_id: str = "msg-1",
    user_id: str = "U42",
    user_name: str = "alice",
    is_bot: bool = False,
    is_me: bool = False,
) -> Message[Any]:
    """Construct a :class:`Message` shaped like an inbound platform payload.

    ``create_test_message`` from :mod:`chat.mock_adapter` is the upstream
    equivalent; we re-wrap it here so all integration helpers share a
    single import path.
    """

    msg = create_test_message(
        message_id,
        text,
        thread_id=thread_id,
        author=Author(
            user_id=user_id,
            user_name=user_name,
            full_name=user_name.title(),
            is_bot=is_bot,
            is_me=is_me,
        ),
        metadata=MessageMetadata(date_sent=datetime.now(UTC), edited=False),
    )
    return msg


async def run_with_chat_lifecycle(chat: Chat, coro: Any) -> Any:
    """Run ``coro`` inside ``chat.initialize()`` / ``chat.shutdown()``.

    Exists to keep teardown consistent across tests — missing
    ``shutdown()`` would leave state adapters connected and Redis/pg tests
    would leak client resources across parametrised runs.
    """

    await chat.initialize()
    try:
        return await coro
    finally:
        await chat.shutdown()


def patch_post_message_raw(adapter: Any, raw: dict[str, Any]) -> AsyncMock:
    """Replace ``adapter.post_message`` with an :class:`AsyncMock` returning ``raw``.

    Callers can reach into the returned mock to assert call args. Matches
    the upstream ``vi.fn().mockResolvedValue(...)`` pattern.
    """

    mock = AsyncMock(return_value=dict(raw))
    adapter.post_message = mock
    return mock


async def await_all(*coros: Any) -> list[Any]:
    """Await several coroutines in parallel, returning the results in order."""

    return list(await asyncio.gather(*coros))


__all__ = [
    "HandlerSpy",
    "await_all",
    "build_chat",
    "build_multi_adapter_chat",
    "make_incoming_message",
    "patch_post_message_raw",
    "run_with_chat_lifecycle",
]
