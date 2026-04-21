"""Stream normalization — Python port of ``packages/chat/src/from-full-stream.ts``.

:func:`from_full_stream` accepts any :class:`~collections.abc.AsyncIterable`
and yields plain text chunks or structured
:class:`~chat.types.StreamChunk` dicts, ready for
``await thread.post(...)``.

Handled input shapes:

- plain ``str`` — yielded unchanged.
- AI SDK ``fullStream`` events (dicts with ``type``) —
  ``text-delta`` events are unwrapped (``text`` / ``delta`` / ``textDelta``),
  ``finish-step`` triggers a ``"\\n\\n"`` separator before the next text.
- :class:`~chat.types.StreamChunk` dicts (``markdown_text`` / ``task_update``
  / ``plan_update``) — yielded unchanged.

Unrecognized objects are dropped silently so the stream can mix arbitrary
AI SDK event types (tool calls, reasoning, etc.) without corrupting the
text output.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from chat.types import StreamChunk

#: Structured chunk types that pass straight through to the adapter.
STREAM_CHUNK_TYPES: frozenset[str] = frozenset({"markdown_text", "task_update", "plan_update"})


async def from_full_stream(
    stream: AsyncIterable[Any],
) -> AsyncIterator[str | StreamChunk]:
    """Normalize *stream* into text + structured chunks.

    Yields ``str`` for text content and :class:`StreamChunk` dicts for
    structured updates. Non-text AI SDK events are dropped.
    """
    needs_separator = False
    has_emitted_text = False

    async for event in stream:
        if isinstance(event, str):
            yield event
            continue

        if not isinstance(event, dict) or "type" not in event:
            continue

        event_type = event.get("type")
        if not isinstance(event_type, str):
            continue

        if event_type in STREAM_CHUNK_TYPES:
            yield event  # type: ignore[misc]
            continue

        if event_type == "text-delta":
            text_content = event.get("text")
            if text_content is None:
                text_content = event.get("delta")
            if text_content is None:
                text_content = event.get("textDelta")

            if isinstance(text_content, str):
                if needs_separator and has_emitted_text:
                    yield "\n\n"
                needs_separator = False
                has_emitted_text = True
                yield text_content
        elif event_type == "finish-step":
            needs_separator = True


__all__ = ["STREAM_CHUNK_TYPES", "from_full_stream"]
