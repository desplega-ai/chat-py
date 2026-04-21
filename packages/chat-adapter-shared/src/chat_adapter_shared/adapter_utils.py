"""Shared utility functions for chat adapters.

Python port of upstream ``packages/adapter-shared/src/adapter-utils.ts``.

These utilities are used across all adapter implementations (Slack, Teams,
Google Chat, Discord, ...) to reduce code duplication and ensure consistent
behavior.

We accept any value duck-typed as a postable message and inspect it for the
``card`` / ``files`` keys, mirroring the TypeScript ``AdapterPostableMessage``
union (string | PostableRaw | PostableMarkdown | PostableAst | PostableCard |
CardElement).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chat import AdapterPostableMessage, CardElement, FileUpload


def _is_card_dict(value: Any) -> bool:
    """Local fallback for ``chat.is_card_element`` — duck-types the dict shape.

    A ``CardElement`` is a dict (or attr-bag) with ``type == "card"``.
    """

    if value is None:
        return False
    if isinstance(value, dict):
        return value.get("type") == "card"
    return getattr(value, "type", None) == "card"


def extract_card(message: AdapterPostableMessage) -> CardElement | None:
    """Extract a ``CardElement`` from an ``AdapterPostableMessage`` if present.

    Handles two cases:

    1. The message *is* a ``CardElement`` (``type == "card"``).
    2. The message is a postable wrapper with a ``card`` property.

    Returns ``None`` for plain strings, raw/markdown/ast messages, or any
    object without a ``card`` field.
    """

    if message is None:
        return None
    if _is_card_dict(message):
        return message  # type: ignore[return-value]
    if isinstance(message, dict) and "card" in message:
        return message["card"]
    if hasattr(message, "card"):
        return getattr(message, "card", None)
    return None


def extract_files(message: AdapterPostableMessage) -> list[FileUpload]:
    """Extract a ``FileUpload`` list from a postable message if present.

    Files can be attached to PostableRaw, PostableMarkdown, PostableAst, or
    PostableCard messages via the ``files`` property. Returns an empty list
    when no files are attached or when the message is not an object.
    """

    if message is None:
        return []
    if isinstance(message, str):
        return []
    if isinstance(message, dict):
        files = message.get("files")
        return list(files) if files else []
    files = getattr(message, "files", None)
    return list(files) if files else []
