"""Standalone JSON reviver for Chat SDK objects — Python port of ``reviver.ts``.

Restores serialized :class:`~chat.thread.ThreadImpl`,
:class:`~chat.channel.ChannelImpl`, and :class:`~chat.message.Message`
instances without requiring a :class:`~chat.chat.Chat` instance.

Upstream passes :func:`reviver` as the second arg to ``JSON.parse``. Python's
equivalent is :func:`json.loads` with ``object_hook``; this module exposes
both surfaces:

- :func:`reviver(key, value)` — two-arg (key, value) callable matching the
  TS signature, in case callers thread through shared utilities.
- :func:`object_hook(data)` — a ``json.loads(..., object_hook=...)``-style
  callable that reconstructs Thread/Channel/Message dicts automatically.

:class:`~chat.thread.ThreadImpl` instances revived here use lazy adapter
resolution — the adapter is looked up from the :class:`~chat.chat.Chat`
singleton when first accessed, so :meth:`chat.chat.Chat.register_singleton`
must have been called before using thread methods like
:meth:`~chat.thread.ThreadImpl.post`.
"""

from __future__ import annotations

from typing import Any

from chat.channel import ChannelImpl, SerializedChannel
from chat.message import Message
from chat.thread import SerializedThread, ThreadImpl
from chat.types import SerializedMessage

_THREAD_TAG = "chat:Thread"
_CHANNEL_TAG = "chat:Channel"
_MESSAGE_TAG = "chat:Message"


def _revive(value: Any) -> Any:
    """Reconstruct a chat-sdk object from a serialized dict, if tagged."""
    if not isinstance(value, dict):
        return value
    tag = value.get("_type")
    if tag == _THREAD_TAG:
        return ThreadImpl.from_json(value)  # type: ignore[arg-type]
    if tag == _CHANNEL_TAG:
        return ChannelImpl.from_json(value)  # type: ignore[arg-type]
    if tag == _MESSAGE_TAG:
        return Message.from_json(value)  # type: ignore[arg-type]
    return value


def reviver(_key: str, value: Any) -> Any:
    """Two-arg reviver matching ``JSON.parse`` semantics from upstream TS.

    Passed a ``(key, value)`` pair for every leaf node during parse; returns
    a revived object if *value* is a serialized chat-sdk entity, else returns
    *value* unchanged.
    """
    return _revive(value)


def object_hook(data: dict[str, Any]) -> Any:
    """``json.loads(..., object_hook=...)``-compatible reviver.

    ``object_hook`` is invoked only for dict values (never arrays or scalars),
    which is exactly the path where chat-sdk types live. Returns either a
    revived :class:`Message`, :class:`ChannelImpl`, :class:`ThreadImpl`, or
    the original dict when untagged.
    """
    return _revive(data)


__all__ = [
    "SerializedChannel",
    "SerializedMessage",
    "SerializedThread",
    "object_hook",
    "reviver",
]
