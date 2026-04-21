"""Tests for :mod:`chat.message` — port of ``message.test.ts``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from chat.markdown import parse_markdown
from chat.message import Message
from chat.types import (
    Attachment,
    Author,
    MessageMetadata,
    SerializedMessage,
)


def _make_message(**overrides: Any) -> Message[Any]:
    base: dict[str, Any] = {
        "id": "msg-1",
        "thread_id": "slack:C123:1234.5678",
        "text": "Hello world",
        "formatted": parse_markdown("Hello world"),
        "raw": {"platform": "test"},
        "author": Author(
            user_id="U123",
            user_name="testuser",
            full_name="Test User",
            is_bot=False,
            is_me=False,
        ),
        "metadata": MessageMetadata(
            date_sent=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            edited=False,
        ),
        "attachments": [],
    }
    base.update(overrides)
    return Message(**base)


# ----------------------------------------------------------------------------
# constructor
# ----------------------------------------------------------------------------


class TestConstructor:
    def test_assigns_all_properties(self) -> None:
        msg = _make_message()
        assert msg.id == "msg-1"
        assert msg.thread_id == "slack:C123:1234.5678"
        assert msg.text == "Hello world"
        assert msg.author.user_name == "testuser"
        assert isinstance(msg.metadata.date_sent, datetime)
        assert msg.attachments == []
        assert msg.is_mention is None

    def test_assigns_is_mention_when_provided(self) -> None:
        msg = _make_message(is_mention=True)
        assert msg.is_mention is True


# ----------------------------------------------------------------------------
# to_json()
# ----------------------------------------------------------------------------


class TestToJson:
    def test_produces_correct_type_tag(self) -> None:
        json = _make_message().to_json()
        assert json["_type"] == "chat:Message"

    def test_serializes_dates_as_iso_strings(self) -> None:
        msg = _make_message(
            metadata=MessageMetadata(
                date_sent=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
                edited=True,
                edited_at=datetime(2024, 6, 1, 13, 0, 0, tzinfo=UTC),
            ),
        )
        json = msg.to_json()
        assert json["metadata"]["dateSent"] == "2024-06-01T12:00:00.000Z"
        assert json["metadata"]["editedAt"] == "2024-06-01T13:00:00.000Z"

    def test_strips_data_and_fetch_data_from_attachments(self) -> None:
        async def _fetch() -> bytes:
            return b"binary"

        msg = _make_message(
            attachments=[
                Attachment(
                    type="image",
                    url="https://example.com/img.png",
                    name="img.png",
                    data=b"binary",
                    fetch_data=_fetch,
                ),
            ],
        )
        json = msg.to_json()
        assert json["attachments"][0] == {
            "type": "image",
            "url": "https://example.com/img.png",
            "name": "img.png",
            "mimeType": None,
            "size": None,
            "width": None,
            "height": None,
        }
        assert "data" not in json["attachments"][0]
        assert "fetchData" not in json["attachments"][0]

    def test_includes_is_mention_flag(self) -> None:
        json = _make_message(is_mention=True).to_json()
        assert json["isMention"] is True


# ----------------------------------------------------------------------------
# from_json()
# ----------------------------------------------------------------------------


class TestFromJson:
    def test_converts_iso_strings_back_to_datetimes(self) -> None:
        json: SerializedMessage = {
            "_type": "chat:Message",
            "id": "msg-2",
            "threadId": "teams:ch:th",
            "text": "hi",
            "formatted": {"type": "root", "children": []},
            "raw": {},
            "author": {
                "userId": "U1",
                "userName": "u",
                "fullName": "U",
                "isBot": False,
                "isMe": False,
            },
            "metadata": {
                "dateSent": "2024-03-01T00:00:00.000Z",
                "edited": True,
                "editedAt": "2024-03-01T01:00:00.000Z",
            },
            "attachments": [],
        }
        msg = Message.from_json(json)
        assert isinstance(msg.metadata.date_sent, datetime)
        assert isinstance(msg.metadata.edited_at, datetime)

    def test_handles_missing_edited_at(self) -> None:
        json: SerializedMessage = {
            "_type": "chat:Message",
            "id": "msg-3",
            "threadId": "t",
            "text": "t",
            "formatted": {"type": "root", "children": []},
            "raw": {},
            "author": {
                "userId": "U",
                "userName": "u",
                "fullName": "U",
                "isBot": False,
                "isMe": False,
            },
            "metadata": {"dateSent": "2024-01-01T00:00:00.000Z", "edited": False},
            "attachments": [],
        }
        msg = Message.from_json(json)
        assert msg.metadata.edited_at is None


# ----------------------------------------------------------------------------
# Round-trip preservation
# ----------------------------------------------------------------------------


class TestRoundTrip:
    def test_preserves_all_fields(self) -> None:
        original = _make_message(
            is_mention=True,
            metadata=MessageMetadata(
                date_sent=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
                edited=True,
                edited_at=datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC),
            ),
            attachments=[
                Attachment(
                    type="file",
                    url="https://example.com/f.pdf",
                    name="f.pdf",
                ),
            ],
        )

        restored = Message.from_json(original.to_json())
        assert restored.id == original.id
        assert restored.text == original.text
        assert restored.is_mention == original.is_mention
        assert restored.metadata.date_sent == original.metadata.date_sent


# ----------------------------------------------------------------------------
# Workflow serde hooks — mirror upstream's WORKFLOW_SERIALIZE/DESERIALIZE
# ----------------------------------------------------------------------------


class TestWorkflowSerde:
    def test_serializes_via_hook(self) -> None:
        msg = _make_message()
        serialized = msg.__chat_serialize__()
        assert serialized["_type"] == "chat:Message"
        assert serialized["id"] == "msg-1"

    def test_deserializes_via_hook(self) -> None:
        msg = _make_message()
        serialized = msg.__chat_serialize__()
        restored = Message.__chat_deserialize__(serialized)
        assert restored.id == msg.id
        assert isinstance(restored.metadata.date_sent, datetime)
