"""Postable-object protocol — port of upstream ``packages/chat/src/postable-object.ts``.

A :class:`PostableObject` is any domain object that can be posted as a single
message: :class:`~chat.plan.Plan`, polls, etc. The object either leverages
native adapter support (via ``postObject``) or falls back to posting a text
representation.

Upstream identifies postable objects with a ``Symbol.for("chat.postable")``
``$$typeof`` tag. Python doesn't have registered symbols, but ``object()``
produces a unique sentinel with the same semantic guarantees as long as it is
imported from this module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:
    from chat.logger import Logger

POSTABLE_OBJECT: Any = object()
"""Module-level sentinel used to tag :class:`PostableObject` instances.

Assign this to an object's ``type_tag`` attribute (upstream's ``$$typeof``).
"""


class PostableObjectContext(Protocol):
    """Context handed to :class:`PostableObject.on_posted` after posting."""

    adapter: Any
    logger: Logger | None
    message_id: str
    thread_id: str


class SimplePostableObjectContext:
    """Concrete :class:`PostableObjectContext` — used by :func:`post_postable_object`."""

    __slots__ = ("adapter", "logger", "message_id", "thread_id")

    def __init__(
        self,
        adapter: Any,
        thread_id: str,
        message_id: str,
        logger: Logger | None = None,
    ) -> None:
        self.adapter = adapter
        self.thread_id = thread_id
        self.message_id = message_id
        self.logger = logger


@runtime_checkable
class PostableObject[TData](Protocol):
    """Base protocol for objects that can be posted to threads/channels.

    Implementations must set :attr:`type_tag` to :data:`POSTABLE_OBJECT` — this
    is what :func:`is_postable_object` checks.
    """

    type_tag: Any
    kind: str

    def get_fallback_text(self) -> str: ...

    def get_post_data(self) -> TData: ...

    def is_supported(self, adapter: Any) -> bool: ...

    def on_posted(self, context: PostableObjectContext) -> None: ...


def is_postable_object(value: Any) -> bool:
    """Type guard — ``True`` if ``value`` looks like a :class:`PostableObject`."""
    return getattr(value, "type_tag", None) is POSTABLE_OBJECT


PostFn = Callable[[str, str], Awaitable[dict[str, Any]]]
"""Async fallback post function. Receives ``(thread_id, text)``.

Must return a ``dict`` with at least ``id`` and optionally ``thread_id`` keys
(matching upstream's ``{id, threadId?}`` shape).
"""


async def post_postable_object(
    obj: PostableObject[Any],
    adapter: Any,
    thread_id: str,
    post_fn: PostFn,
    logger: Logger | None = None,
) -> None:
    """Post ``obj`` using the adapter's native support, or fall back to text.

    Mirrors upstream's ``postPostableObject`` exactly.
    """

    def context(raw: dict[str, Any]) -> SimplePostableObjectContext:
        return SimplePostableObjectContext(
            adapter=adapter,
            thread_id=raw.get("thread_id") or raw.get("threadId") or thread_id,
            message_id=raw["id"],
            logger=logger,
        )

    post_object = getattr(adapter, "post_object", None)
    if obj.is_supported(adapter) and post_object is not None:
        raw = await post_object(thread_id, obj.kind, obj.get_post_data())
        obj.on_posted(context(raw))
    else:
        raw = await post_fn(thread_id, obj.get_fallback_text())
        obj.on_posted(context(raw))
