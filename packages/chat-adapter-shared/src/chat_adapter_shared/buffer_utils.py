"""Buffer conversion utilities for handling file uploads.

Python port of upstream ``packages/adapter-shared/src/buffer-utils.ts``. The
upstream module normalises ``Buffer`` / ``ArrayBuffer`` / ``Blob`` inputs to a
Node ``Buffer``. The Python equivalents are:

- Node ``Buffer``       → :class:`bytes`
- Node ``ArrayBuffer``  → :class:`bytearray` / :class:`memoryview`
- Web ``Blob``          → file-like object exposing ``read()`` (sync) or an
  ``async`` ``read()`` (async); :class:`io.IOBase` instances are the most
  common case.
"""

from __future__ import annotations

import base64
import inspect
import io
from typing import TYPE_CHECKING, Any, TypedDict

from chat_adapter_shared.errors import ValidationError

if TYPE_CHECKING:
    from chat_adapter_shared.card_utils import PlatformName


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


FileDataInput = bytes | bytearray | memoryview | io.IOBase
"""The supported input types for file data."""


class ToBufferOptions(TypedDict, total=False):
    """Options for buffer conversion."""

    platform: PlatformName
    """The platform name for error messages."""

    throw_on_unsupported: bool
    """If ``True``, raise :class:`ValidationError` for unsupported types.

    If ``False``, return ``None`` for unsupported types. Default: ``True``.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _options(options: ToBufferOptions) -> tuple[str, bool]:
    platform = options["platform"]  # required
    throw_on_unsupported = options.get("throw_on_unsupported", True)
    return platform, throw_on_unsupported


def _is_blob_like(value: Any) -> bool:
    """Detect Blob-like objects (file-like with read())."""
    if isinstance(value, io.IOBase):
        return True
    return hasattr(value, "read") and callable(value.read)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def to_buffer(
    data: Any,
    options: ToBufferOptions,
) -> bytes | None:
    """Convert various data types to :class:`bytes`.

    Handles:
    - :class:`bytes` / :class:`bytearray` / :class:`memoryview` returned as bytes.
    - File-like objects (Blob analog): read fully via ``read()`` (await if async).

    :param data: The file data to convert.
    :param options: Conversion options.
    :returns: The bytes payload, or ``None`` when conversion fails and
        ``throw_on_unsupported`` is ``False``.
    :raises ValidationError: When the input type is unsupported and
        ``throw_on_unsupported`` is ``True``.
    """

    platform, throw_on_unsupported = _options(options)

    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)

    if _is_blob_like(data):
        result = data.read()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, (bytes, bytearray, memoryview)):
            return bytes(result)
        if throw_on_unsupported:
            raise ValidationError(platform, "Unsupported file data type")
        return None

    if throw_on_unsupported:
        raise ValidationError(platform, "Unsupported file data type")

    return None


def to_buffer_sync(
    data: Any,
    options: ToBufferOptions,
) -> bytes | None:
    """Synchronous version of :func:`to_buffer` for non-Blob data.

    Use this when you know the data is not a Blob (e.g. already validated).

    :raises ValidationError: When ``data`` is a Blob (file-like) or
        unsupported type and ``throw_on_unsupported`` is ``True``.
    """

    platform, throw_on_unsupported = _options(options)

    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)

    if _is_blob_like(data):
        if throw_on_unsupported:
            raise ValidationError(
                platform,
                "Cannot convert Blob synchronously. Use to_buffer() for async conversion.",
            )
        return None

    if throw_on_unsupported:
        raise ValidationError(platform, "Unsupported file data type")

    return None


def buffer_to_data_uri(buffer: bytes, mime_type: str = "application/octet-stream") -> str:
    """Convert a bytes buffer to a ``data:`` URI string.

    :returns: A URI of the form ``data:{mime_type};base64,{base64_data}``.
    """

    encoded = base64.b64encode(buffer).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
