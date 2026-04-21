"""Tests for :mod:`chat.from_full_stream` — mirrors upstream ``from-full-stream.test.ts``."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import pytest
from chat.from_full_stream import from_full_stream

pytestmark = pytest.mark.asyncio


async def _collect(stream: AsyncIterator[Any]) -> str:
    out = ""
    async for chunk in stream:
        if isinstance(chunk, str):
            out += chunk
    return out


async def _events(items: list[Any]) -> AsyncIterable[Any]:
    async def _gen() -> AsyncIterator[Any]:
        for item in items:
            yield item

    return _gen()


# ---------------------------------------------------------------------------
# fullStream (object events)
# ---------------------------------------------------------------------------


class TestFullStreamObjects:
    async def test_extracts_text_delta_values(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "textDelta": "hello"},
                {"type": "text-delta", "textDelta": " world"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "hello world"

    async def test_injects_separator_between_steps(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "textDelta": "hello."},
                {"type": "finish-step"},
                {"type": "text-delta", "textDelta": "how are you?"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "hello.\n\nhow are you?"

    async def test_no_trailing_separator(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "textDelta": "done."},
                {"type": "finish-step"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "done."

    async def test_handles_multiple_steps(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "textDelta": "step 1"},
                {"type": "finish-step"},
                {"type": "text-delta", "textDelta": "step 2"},
                {"type": "finish-step"},
                {"type": "text-delta", "textDelta": "step 3"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "step 1\n\nstep 2\n\nstep 3"

    async def test_skips_tool_call_events(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "textDelta": "before"},
                {"type": "tool-call", "toolName": "search", "args": {}},
                {"type": "tool-result", "toolName": "search", "result": "data"},
                {"type": "finish-step"},
                {"type": "tool-call-streaming-start", "toolName": "lookup"},
                {"type": "text-delta", "textDelta": " after"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "before\n\n after"

    async def test_consecutive_finish_step_events(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "textDelta": "a"},
                {"type": "finish-step"},
                {"type": "finish-step"},
                {"type": "text-delta", "textDelta": "b"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "a\n\nb"

    async def test_no_separator_before_first_text(self) -> None:
        stream = await _events(
            [
                {"type": "finish-step"},
                {"type": "text-delta", "textDelta": "first text"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "first text"

    async def test_ignores_non_string_text_delta(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "textDelta": 123},
                {"type": "text-delta", "textDelta": None},
                {"type": "text-delta"},
                {"type": "text-delta", "textDelta": "ok"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "ok"


# ---------------------------------------------------------------------------
# textStream (plain strings)
# ---------------------------------------------------------------------------


class TestTextStream:
    async def test_passes_through_string_chunks(self) -> None:
        stream = await _events(["hello", " ", "world"])
        assert await _collect(from_full_stream(stream)) == "hello world"

    async def test_single_string_chunk(self) -> None:
        stream = await _events(["complete message"])
        assert await _collect(from_full_stream(stream)) == "complete message"


# ---------------------------------------------------------------------------
# fullStream v6 (`text` key)
# ---------------------------------------------------------------------------


class TestFullStreamV6:
    async def test_extracts_text_key(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "id": "0", "text": "hello"},
                {"type": "text-delta", "id": "0", "text": " world"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "hello world"

    async def test_separator_with_text_key(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "id": "0", "text": "step 1."},
                {"type": "finish-step"},
                {"type": "text-delta", "id": "0", "text": "step 2."},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "step 1.\n\nstep 2."

    async def test_prefers_text_over_text_delta(self) -> None:
        stream = await _events(
            [
                {"type": "text-delta", "text": "v6", "textDelta": "v5"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "v6"


# ---------------------------------------------------------------------------
# StreamChunk passthrough
# ---------------------------------------------------------------------------


class TestStreamChunkPassthrough:
    async def test_passes_through_markdown_text(self) -> None:
        chunk = {"type": "markdown_text", "text": "## heading"}
        stream = await _events([chunk])
        out: list[Any] = []
        async for item in from_full_stream(stream):
            out.append(item)
        assert out == [chunk]

    async def test_passes_through_task_update(self) -> None:
        chunk = {
            "type": "task_update",
            "id": "t1",
            "title": "search",
            "status": "in_progress",
        }
        stream = await _events([chunk])
        out: list[Any] = []
        async for item in from_full_stream(stream):
            out.append(item)
        assert out == [chunk]

    async def test_passes_through_plan_update(self) -> None:
        chunk = {"type": "plan_update", "title": "New Plan"}
        stream = await _events([chunk])
        out: list[Any] = []
        async for item in from_full_stream(stream):
            out.append(item)
        assert out == [chunk]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_empty_stream_returns_empty(self) -> None:
        stream = await _events([])
        assert await _collect(from_full_stream(stream)) == ""

    async def test_ignores_invalid_events(self) -> None:
        stream = await _events(
            [
                None,
                42,
                {"noType": True},
                {"type": "text-delta", "textDelta": "valid"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "valid"

    async def test_mixed_strings_and_objects(self) -> None:
        stream = await _events(
            [
                "hello",
                {"type": "text-delta", "textDelta": " world"},
            ]
        )
        assert await _collect(from_full_stream(stream)) == "hello world"
