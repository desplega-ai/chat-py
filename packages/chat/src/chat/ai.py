"""AI SDK conversion — Python port of ``packages/chat/src/ai.ts``.

:func:`to_ai_messages` converts a list of :class:`~chat.message.Message` into
the ``AiMessage`` shape the Vercel AI SDK consumes. Output is a JSON-compatible
``list[dict]`` with camelCase keys so it plugs directly into ``agent.stream``.

Message parts (``AiTextPart`` / ``AiImagePart`` / ``AiFilePart``) are
:class:`TypedDict` with an explicit ``type`` discriminator — this keeps the
wire shape identical to upstream and leaves them structurally assignable to the
AI SDK's ``ModelMessage`` parts.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, Required, TypedDict, cast

if TYPE_CHECKING:
    from chat.message import Message
    from chat.types import Attachment

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Part types
# ---------------------------------------------------------------------------


class AiTextPart(TypedDict):
    """Plain text content part."""

    type: Literal["text"]
    text: str


class AiImagePart(TypedDict, total=False):
    """Image content part — ``image`` is a ``data:`` URL or raw URL string."""

    type: Required[Literal["image"]]
    image: Required[str]
    mediaType: str


class AiFilePart(TypedDict, total=False):
    """File content part — ``data`` is a ``data:`` URL string for inlined files."""

    type: Required[Literal["file"]]
    data: Required[str]
    mediaType: Required[str]
    filename: str


AiMessagePart = AiTextPart | AiImagePart | AiFilePart


# ---------------------------------------------------------------------------
# Message shapes
# ---------------------------------------------------------------------------


class AiUserMessage(TypedDict):
    """User message — ``content`` may be text or a multi-part list."""

    role: Literal["user"]
    content: str | list[AiMessagePart]


class AiAssistantMessage(TypedDict):
    """Assistant message — ``content`` is always a string."""

    role: Literal["assistant"]
    content: str


AiMessage = AiUserMessage | AiAssistantMessage


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


TransformResult = AiMessage | None
TransformCallback = Callable[[AiMessage, "Message"], TransformResult | Awaitable[TransformResult]]
UnsupportedCallback = Callable[["Attachment", "Message"], None]


class ToAiMessagesOptions(TypedDict, total=False):
    """Options for :func:`to_ai_messages`."""

    includeNames: bool
    onUnsupportedAttachment: UnsupportedCallback
    transformMessage: TransformCallback


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


#: MIME prefixes treated as text files and inlined as ``file`` parts.
TEXT_MIME_PREFIXES: tuple[str, ...] = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/typescript",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
)


def _is_text_mime_type(mime_type: str) -> bool:
    return any(mime_type == p or mime_type.startswith(p) for p in TEXT_MIME_PREFIXES)


def _b64(buffer: bytes) -> str:
    return base64.b64encode(buffer).decode("ascii")


async def _attachment_to_part(att: Attachment) -> AiMessagePart | None:
    """Build an :data:`AiMessagePart` from *att* — or ``None`` if unsupported."""
    if att.type == "image":
        if att.fetch_data is None:
            return None
        try:
            buffer = await att.fetch_data()
        except BaseException:
            _log.exception("to_ai_messages: failed to fetch image data")
            return None
        mime_type = att.mime_type or "image/png"
        part: AiFilePart = {
            "type": "file",
            "data": f"data:{mime_type};base64,{_b64(buffer)}",
            "mediaType": mime_type,
        }
        if att.name is not None:
            part["filename"] = att.name
        return part

    if att.type == "file" and att.mime_type and _is_text_mime_type(att.mime_type):
        if att.fetch_data is None:
            return None
        try:
            buffer = await att.fetch_data()
        except BaseException:
            _log.exception("to_ai_messages: failed to fetch file data")
            return None
        file_part: AiFilePart = {
            "type": "file",
            "data": f"data:{att.mime_type};base64,{_b64(buffer)}",
            "mediaType": att.mime_type,
        }
        if att.name is not None:
            file_part["filename"] = att.name
        return file_part

    return None


def _default_on_unsupported(att: Attachment, _msg: Message) -> None:
    name_suffix = f" ({att.name})" if att.name else ""
    _log.warning(
        'to_ai_messages: unsupported attachment type "%s"%s — skipped',
        att.type,
        name_suffix,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def to_ai_messages(
    messages: list[Message],
    options: ToAiMessagesOptions | None = None,
) -> list[AiMessage]:
    """Convert chat SDK messages to AI SDK conversation format.

    - Filters out messages with empty / whitespace-only text.
    - Maps ``author.is_me is True`` to ``"assistant"``, otherwise ``"user"``.
    - Uses :attr:`Message.text` for content.
    - Appends link metadata when available.
    - Includes image attachments and text files as ``file`` parts.
    - Uses :meth:`Attachment.fetch_data` when available to inline attachment
      data as base64.
    - Warns on unsupported attachment types (video, audio).
    """
    opts: ToAiMessagesOptions = options or {}
    include_names = opts.get("includeNames", False)
    transform = opts.get("transformMessage")
    on_unsupported = opts.get("onUnsupportedAttachment", _default_on_unsupported)

    def _sort_key(m: Message) -> float:
        ts = m.metadata.date_sent
        return ts.timestamp() if ts is not None else 0.0

    sorted_msgs = sorted(messages, key=_sort_key)
    filtered = [m for m in sorted_msgs if m.text.strip()]

    async def process(msg: Message) -> tuple[AiMessage, Message] | None:
        role: Literal["user", "assistant"] = "assistant" if msg.author.is_me else "user"
        text_content = (
            f"[{msg.author.user_name}]: {msg.text}"
            if include_names and role == "user"
            else msg.text
        )

        if msg.links:
            link_blocks: list[str] = []
            for link in msg.links:
                parts: list[str] = []
                if link.fetch_message is not None:
                    parts.append(f"[Embedded message: {link.url}]")
                else:
                    parts.append(link.url)
                if link.title:
                    parts.append(f"Title: {link.title}")
                if link.description:
                    parts.append(f"Description: {link.description}")
                if link.site_name:
                    parts.append(f"Site: {link.site_name}")
                link_blocks.append("\n".join(parts))
            text_content += "\n\nLinks:\n" + "\n\n".join(link_blocks)

        ai_message: AiMessage
        if role == "user":
            attachment_parts: list[AiMessagePart] = []
            for att in msg.attachments or []:
                part = await _attachment_to_part(att)
                if part is not None:
                    attachment_parts.append(part)
                elif att.type in ("video", "audio"):
                    on_unsupported(att, msg)

            if attachment_parts:
                user_msg: AiUserMessage = {
                    "role": "user",
                    "content": [
                        cast(AiMessagePart, {"type": "text", "text": text_content}),
                        *attachment_parts,
                    ],
                }
                ai_message = user_msg
            else:
                ai_message = {"role": "user", "content": text_content}
        else:
            ai_message = {"role": "assistant", "content": text_content}

        if transform is not None:
            result = transform(ai_message, msg)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                return None
            return (result, msg)

        return (ai_message, msg)

    results = await asyncio.gather(*(process(m) for m in filtered))
    return [r[0] for r in results if r is not None]


__all__ = [
    "TEXT_MIME_PREFIXES",
    "AiAssistantMessage",
    "AiFilePart",
    "AiImagePart",
    "AiMessage",
    "AiMessagePart",
    "AiTextPart",
    "AiUserMessage",
    "ToAiMessagesOptions",
    "to_ai_messages",
]
