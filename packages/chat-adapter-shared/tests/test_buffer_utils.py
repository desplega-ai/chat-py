"""Tests for buffer conversion utilities.

Python port of upstream ``packages/adapter-shared/src/buffer-utils.test.ts``.
The Python port maps Node ``Buffer``/``ArrayBuffer``/``Blob`` to ``bytes`` /
``bytearray`` / ``io.BytesIO`` (file-like).
"""

from __future__ import annotations

import io
import re

import pytest
from chat_adapter_shared.buffer_utils import (
    buffer_to_data_uri,
    to_buffer,
    to_buffer_sync,
)
from chat_adapter_shared.errors import ValidationError

DATA_URI_PNG_PREFIX = re.compile(r"^data:image/png;base64,")


# ============================================================================
# to_buffer Tests
# ============================================================================


class TestToBuffer:
    async def test_returns_bytes_unchanged(self) -> None:
        input_data = b"hello"
        result = await to_buffer(input_data, {"platform": "slack"})
        assert result is input_data

    async def test_converts_bytearray_to_bytes(self) -> None:
        input_data = bytearray(b"hello")
        result = await to_buffer(input_data, {"platform": "slack"})
        assert isinstance(result, bytes)
        assert result == b"hello"

    async def test_converts_memoryview_to_bytes(self) -> None:
        input_data = memoryview(b"hello")
        result = await to_buffer(input_data, {"platform": "slack"})
        assert isinstance(result, bytes)
        assert result == b"hello"

    async def test_converts_blob_like_file_to_bytes(self) -> None:
        # io.BytesIO is the Python equivalent of a Blob (file-like object).
        input_data = io.BytesIO(b"hello")
        result = await to_buffer(input_data, {"platform": "slack"})
        assert isinstance(result, bytes)
        assert result == b"hello"

    async def test_throws_validation_error_for_unsupported_type_by_default(self) -> None:
        with pytest.raises(ValidationError):
            await to_buffer("string", {"platform": "slack"})  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await to_buffer(123, {"platform": "slack"})  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await to_buffer({}, {"platform": "slack"})  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await to_buffer(None, {"platform": "slack"})  # type: ignore[arg-type]

    async def test_returns_none_for_unsupported_type_when_throw_is_false(self) -> None:
        result = await to_buffer(
            "string",  # type: ignore[arg-type]
            {"platform": "teams", "throw_on_unsupported": False},
        )
        assert result is None

    async def test_includes_platform_in_error_message(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await to_buffer("invalid", {"platform": "slack"})  # type: ignore[arg-type]
        assert exc_info.value.adapter == "slack"


# ============================================================================
# to_buffer_sync Tests
# ============================================================================


class TestToBufferSync:
    def test_returns_bytes_unchanged(self) -> None:
        input_data = b"hello"
        result = to_buffer_sync(input_data, {"platform": "slack"})
        assert result is input_data

    def test_converts_bytearray_to_bytes(self) -> None:
        input_data = bytearray(b"hello")
        result = to_buffer_sync(input_data, {"platform": "slack"})
        assert isinstance(result, bytes)
        assert result == b"hello"

    def test_throws_validation_error_for_blob_like_by_default(self) -> None:
        input_data = io.BytesIO(b"hello")
        with pytest.raises(ValidationError):
            to_buffer_sync(input_data, {"platform": "slack"})

    def test_returns_none_for_blob_like_when_throw_is_false(self) -> None:
        input_data = io.BytesIO(b"hello")
        result = to_buffer_sync(
            input_data,
            {"platform": "slack", "throw_on_unsupported": False},
        )
        assert result is None

    def test_throws_validation_error_for_unsupported_type_by_default(self) -> None:
        with pytest.raises(ValidationError):
            to_buffer_sync("string", {"platform": "slack"})  # type: ignore[arg-type]

    def test_returns_none_for_unsupported_type_when_throw_is_false(self) -> None:
        result = to_buffer_sync(
            "string",  # type: ignore[arg-type]
            {"platform": "teams", "throw_on_unsupported": False},
        )
        assert result is None


# ============================================================================
# buffer_to_data_uri Tests
# ============================================================================


class TestBufferToDataUri:
    def test_converts_buffer_to_data_uri_with_default_mime_type(self) -> None:
        result = buffer_to_data_uri(b"hello")
        assert result == "data:application/octet-stream;base64,aGVsbG8="

    def test_converts_buffer_to_data_uri_with_custom_mime_type(self) -> None:
        result = buffer_to_data_uri(b"hello", "text/plain")
        assert result == "data:text/plain;base64,aGVsbG8="

    def test_handles_image_mime_types(self) -> None:
        # PNG magic bytes
        result = buffer_to_data_uri(bytes([0x89, 0x50, 0x4E, 0x47]), "image/png")
        assert DATA_URI_PNG_PREFIX.match(result)

    def test_handles_empty_buffer(self) -> None:
        result = buffer_to_data_uri(b"")
        assert result == "data:application/octet-stream;base64,"
