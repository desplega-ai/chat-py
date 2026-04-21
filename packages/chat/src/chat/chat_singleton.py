"""Singleton holder for the :class:`Chat` instance.

Port of ``packages/chat/src/chat-singleton.ts``. Kept in its own module to
avoid a circular import between ``chat.py`` / ``thread.py`` / ``channel.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from chat.types import StateAdapter


@runtime_checkable
class ChatSingleton(Protocol):
    """Minimal surface of the :class:`Chat` instance needed by ``channel.py``
    and ``thread.py`` during lazy adapter resolution.
    """

    def get_adapter(self, name: str) -> object | None: ...
    def get_state(self) -> StateAdapter: ...


_singleton: ChatSingleton | None = None


def set_chat_singleton(chat: ChatSingleton) -> None:
    """Set the :class:`Chat` singleton instance. Internal — called by
    ``Chat.register_singleton()``.
    """
    global _singleton
    _singleton = chat


def get_chat_singleton() -> ChatSingleton:
    """Get the registered :class:`Chat` singleton instance.

    :raises RuntimeError: If no singleton has been registered.
    """
    if _singleton is None:
        raise RuntimeError("No Chat singleton registered. Call chat.register_singleton() first.")
    return _singleton


def has_chat_singleton() -> bool:
    """Return ``True`` if a :class:`Chat` singleton has been registered."""
    return _singleton is not None


def clear_chat_singleton() -> None:
    """Clear the :class:`Chat` singleton — testing helper."""
    global _singleton
    _singleton = None
