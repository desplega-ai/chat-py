"""Message class with serialization support — port of ``packages/chat/src/message.ts``.

The :class:`Message` class wraps a single inbound or sent chat message. It
serializes to a JSON-compatible :class:`~chat.types.SerializedMessage` dict
(with camelCase keys and ISO-8601 dates) so the same payload can round-trip
through a workflow engine or cross-language pipeline.

Upstream exposes ``Symbol``-keyed ``WORKFLOW_SERIALIZE`` / ``WORKFLOW_DESERIALIZE``
statics. Python has no registered symbols, so we expose equivalent
``__chat_serialize__`` / ``__chat_deserialize__`` hooks that the part-B
``_serde.py`` module will bind into ``@workflow/serde`` equivalents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from chat.types import (
    Attachment,
    Author,
    FormattedContent,
    LinkPreview,
    MessageData,
    MessageMetadata,
    SerializedAttachment,
    SerializedAuthor,
    SerializedMessage,
    SerializedMetadata,
)


class Message[TRawMessage]:
    """A chat message with serialization support.

    Mirrors the upstream ``Message`` class — attributes use ``snake_case``.
    Serialization produces camelCase keys to match the upstream wire format.
    """

    __slots__ = (
        "attachments",
        "author",
        "formatted",
        "id",
        "is_mention",
        "links",
        "metadata",
        "raw",
        "text",
        "thread_id",
    )

    id: str
    thread_id: str
    text: str
    formatted: FormattedContent
    raw: Any
    author: Author
    metadata: MessageMetadata
    attachments: list[Attachment]
    is_mention: bool | None
    links: list[LinkPreview]

    def __init__(
        self,
        *,
        id: str,
        thread_id: str,
        text: str,
        formatted: FormattedContent,
        raw: Any,
        author: Author,
        metadata: MessageMetadata,
        attachments: list[Attachment] | None = None,
        is_mention: bool | None = None,
        links: list[LinkPreview] | None = None,
    ) -> None:
        self.id = id
        self.thread_id = thread_id
        self.text = text
        self.formatted = formatted
        self.raw = raw
        self.author = author
        self.metadata = metadata
        self.attachments = list(attachments) if attachments is not None else []
        self.is_mention = is_mention
        self.links = list(links) if links is not None else []

    # ------------------------------------------------------------------
    # Alternative construction: ``Message.from_data(data)`` accepting a
    # :class:`MessageData` (mirrors upstream's single-positional ``new Message(data)``).
    # ------------------------------------------------------------------

    @classmethod
    def from_data(cls, data: MessageData) -> Message[Any]:
        """Construct a :class:`Message` from a :class:`MessageData` dataclass."""
        return cls(
            id=data.id,
            thread_id=data.thread_id,
            text=data.text,
            formatted=data.formatted,
            raw=data.raw,
            author=data.author,
            metadata=data.metadata,
            attachments=list(data.attachments),
            is_mention=data.is_mention,
            links=list(data.links),
        )

    # ------------------------------------------------------------------
    # JSON serialization
    # ------------------------------------------------------------------

    def to_json(self) -> SerializedMessage:
        """Serialize the message to a plain JSON-compatible dict.

        Non-serializable attachment fields (``data``, ``fetch_data``) are
        omitted. Dates are encoded as ISO-8601 strings.
        """
        author: SerializedAuthor = {
            "userId": self.author.user_id,
            "userName": self.author.user_name,
            "fullName": self.author.full_name,
            "isBot": self.author.is_bot,
            "isMe": self.author.is_me,
        }
        metadata: SerializedMetadata = {
            "dateSent": _iso(self.metadata.date_sent),
            "edited": self.metadata.edited,
            "editedAt": _iso(self.metadata.edited_at) if self.metadata.edited_at else None,
        }
        attachments: list[SerializedAttachment] = [
            {
                "type": att.type,
                "url": att.url,
                "name": att.name,
                "mimeType": att.mime_type,
                "size": att.size,
                "width": att.width,
                "height": att.height,
            }
            for att in self.attachments
        ]
        result: SerializedMessage = {
            "_type": "chat:Message",
            "id": self.id,
            "threadId": self.thread_id,
            "text": self.text,
            "formatted": self.formatted,
            "raw": self.raw,
            "author": author,
            "metadata": metadata,
            "attachments": attachments,
            "isMention": self.is_mention,
        }
        if self.links:
            result["links"] = [
                {
                    "url": link.url,
                    "title": link.title,
                    "description": link.description,
                    "imageUrl": link.image_url,
                    "siteName": link.site_name,
                }
                for link in self.links
            ]
        else:
            result["links"] = None
        return result

    @classmethod
    def from_json(cls, data: SerializedMessage) -> Message[Any]:
        """Reconstruct a :class:`Message` from a :class:`SerializedMessage` dict.

        ISO-8601 date strings are converted back to ``datetime`` objects.
        """
        author_raw = data["author"]
        author = Author(
            user_id=author_raw["userId"],
            user_name=author_raw["userName"],
            full_name=author_raw["fullName"],
            is_bot=author_raw["isBot"],
            is_me=author_raw["isMe"],
        )

        meta_raw = data["metadata"]
        edited_at_raw = meta_raw.get("editedAt") if "editedAt" in meta_raw else None
        metadata = MessageMetadata(
            date_sent=_parse_iso(meta_raw["dateSent"]),
            edited=meta_raw["edited"],
            edited_at=_parse_iso(edited_at_raw) if edited_at_raw else None,
        )

        attachments: list[Attachment] = []
        for att in data.get("attachments", []) or []:
            attachments.append(
                Attachment(
                    type=att["type"],
                    url=att.get("url"),
                    name=att.get("name"),
                    mime_type=att.get("mimeType"),
                    size=att.get("size"),
                    width=att.get("width"),
                    height=att.get("height"),
                )
            )

        links: list[LinkPreview] = []
        raw_links = data.get("links") if "links" in data else None
        if raw_links:
            for link in raw_links:
                links.append(
                    LinkPreview(
                        url=link["url"],
                        title=link.get("title"),
                        description=link.get("description"),
                        image_url=link.get("imageUrl"),
                        site_name=link.get("siteName"),
                    )
                )

        return cls(
            id=data["id"],
            thread_id=data["threadId"],
            text=data["text"],
            formatted=data["formatted"],
            raw=data.get("raw"),
            author=author,
            metadata=metadata,
            attachments=attachments,
            is_mention=data.get("isMention"),
            links=links,
        )

    # ------------------------------------------------------------------
    # Workflow serde hooks — mirror upstream's ``WORKFLOW_SERIALIZE`` /
    # ``WORKFLOW_DESERIALIZE`` Symbol-keyed statics. Part-B ``_serde.py``
    # will bind these to the workflow engine.
    # ------------------------------------------------------------------

    def __chat_serialize__(self) -> SerializedMessage:
        """Serde hook — ``_serde.py`` (part B) calls this to serialize a :class:`Message`."""
        return self.to_json()

    @classmethod
    def __chat_deserialize__(cls, data: SerializedMessage) -> Message[Any]:
        """Serde hook — ``_serde.py`` (part B) calls this to reconstruct a :class:`Message`."""
        return cls.from_json(data)


def _iso(dt: datetime) -> str:
    """Encode a ``datetime`` as an ISO-8601 string ending in ``Z`` (UTC).

    Matches JavaScript's ``Date.toISOString()`` exactly: always UTC-normalized,
    millisecond precision, trailing ``Z``.
    """
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string (incl. trailing ``Z``) into a timezone-aware ``datetime``."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
