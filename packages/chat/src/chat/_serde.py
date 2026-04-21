"""Central serde dispatch — analogous to upstream's ``@workflow/serde`` integration.

Upstream registers Symbol-keyed statics ``[WORKFLOW_SERIALIZE]`` and
``[WORKFLOW_DESERIALIZE]`` on :class:`Message`, :class:`ChannelImpl`, and
:class:`ThreadImpl`. In the Python port these are plain methods named
``__chat_serialize__`` / ``__chat_deserialize__`` on each class.

This module wraps them behind :func:`chat_serialize` / :func:`chat_deserialize`
functions, plus a :data:`SERDE_REGISTRY` mapping of ``_type`` tags to
classes, so external workflow engines can centralize reconstruction without
knowing the individual class modules.
"""

from __future__ import annotations

from typing import Any

from chat.channel import ChannelImpl
from chat.message import Message
from chat.thread import ThreadImpl

#: Tag → class registry used by :func:`chat_deserialize`.
SERDE_REGISTRY: dict[str, type[Any]] = {
    "chat:Message": Message,
    "chat:Channel": ChannelImpl,
    "chat:Thread": ThreadImpl,
}


def chat_serialize(obj: Any) -> Any:
    """Serialize *obj* using its ``__chat_serialize__`` hook.

    Returns the raw input unchanged when *obj* has no hook.
    """
    fn = getattr(obj, "__chat_serialize__", None)
    if fn is None:
        return obj
    return fn()


def chat_deserialize(data: Any) -> Any:
    """Reconstruct a serialized chat-sdk object.

    *data* is expected to be a dict with a ``_type`` discriminator
    (``chat:Message``, ``chat:Channel``, or ``chat:Thread``). Unknown or
    untagged dicts are returned unchanged.
    """
    if not isinstance(data, dict):
        return data
    tag = data.get("_type")
    if tag is None:
        return data
    cls = SERDE_REGISTRY.get(tag)
    if cls is None:
        return data
    hook = getattr(cls, "__chat_deserialize__", None)
    if hook is None:
        return data
    return hook(data)


__all__ = ["SERDE_REGISTRY", "chat_deserialize", "chat_serialize"]
