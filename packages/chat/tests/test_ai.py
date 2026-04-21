"""Tests for :mod:`chat.ai` — mirrors upstream ``ai.test.ts``."""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from unittest.mock import MagicMock

import pytest
from chat.ai import AiMessage, AiMessagePart, to_ai_messages
from chat.mock_adapter import create_test_message
from chat.types import Attachment, Author, LinkPreview

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bot_author() -> Author:
    return Author(
        user_id="bot",
        user_name="bot",
        full_name="Bot",
        is_bot=True,
        is_me=True,
    )


def _user_author(user_id: str = "U1", user_name: str = "alice") -> Author:
    return Author(
        user_id=user_id,
        user_name=user_name,
        full_name=user_name.capitalize(),
        is_bot=False,
        is_me=False,
    )


def _bytes_source(data: bytes):
    async def _fetch() -> bytes:
        return data

    return _fetch


# ---------------------------------------------------------------------------
# Role mapping + filtering
# ---------------------------------------------------------------------------


async def test_maps_is_me_to_assistant_and_others_to_user() -> None:
    messages = [
        create_test_message("1", "Hello bot"),
        create_test_message("2", "Hi there!", author=_bot_author()),
        create_test_message("3", "Follow up question"),
    ]

    result = await to_ai_messages(messages)

    assert result == [
        {"role": "user", "content": "Hello bot"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "Follow up question"},
    ]


async def test_filters_empty_and_whitespace_text() -> None:
    messages = [
        create_test_message("1", "Hello"),
        create_test_message("2", ""),
        create_test_message("3", "   "),
        create_test_message("4", "\t\n"),
        create_test_message("5", "World"),
    ]

    result = await to_ai_messages(messages)

    assert result == [
        {"role": "user", "content": "Hello"},
        {"role": "user", "content": "World"},
    ]


async def test_preserves_chronological_order() -> None:
    messages = [
        create_test_message("1", "First"),
        create_test_message("2", "Second", author=_bot_author()),
        create_test_message("3", "Third"),
    ]

    result = await to_ai_messages(messages)

    assert [m["content"] for m in result] == ["First", "Second", "Third"]


async def test_include_names_prefixes_user_messages() -> None:
    messages = [
        create_test_message("1", "Hello", author=_user_author("U1", "alice")),
        create_test_message("2", "Hi!", author=_bot_author()),
        create_test_message("3", "Thanks", author=_user_author("U2", "bob")),
    ]

    result = await to_ai_messages(messages, {"includeNames": True})

    assert result == [
        {"role": "user", "content": "[alice]: Hello"},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "[bob]: Thanks"},
    ]


async def test_empty_input_returns_empty() -> None:
    assert await to_ai_messages([]) == []


async def test_all_empty_text_returns_empty() -> None:
    messages = [create_test_message("1", ""), create_test_message("2", "   ")]
    assert await to_ai_messages(messages) == []


# ---------------------------------------------------------------------------
# Link preview tests
# ---------------------------------------------------------------------------


async def test_appends_link_preview_metadata() -> None:
    messages = [
        create_test_message(
            "1",
            "Check this out",
            links=[
                LinkPreview(
                    url="https://vercel.com/blog/post",
                    title="New Feature",
                    description="A cool new feature",
                    site_name="Vercel",
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)

    assert result == [
        {
            "role": "user",
            "content": (
                "Check this out\n\n"
                "Links:\n"
                "https://vercel.com/blog/post\n"
                "Title: New Feature\n"
                "Description: A cool new feature\n"
                "Site: Vercel"
            ),
        },
    ]


async def test_appends_multiple_links() -> None:
    messages = [
        create_test_message(
            "1",
            "See these links",
            links=[
                LinkPreview(url="https://example.com"),
                LinkPreview(url="https://vercel.com", title="Vercel"),
            ],
        ),
    ]

    result = await to_ai_messages(messages)

    assert result[0]["content"] == (
        "See these links\n\nLinks:\nhttps://example.com\n\nhttps://vercel.com\nTitle: Vercel"
    )


async def test_labels_links_with_fetch_message_as_embedded() -> None:
    async def _fetcher() -> Any:
        return create_test_message("linked", "linked")

    messages = [
        create_test_message(
            "1",
            "Look at this thread",
            links=[
                LinkPreview(
                    url="https://team.slack.com/archives/C123/p1234567890123456",
                    fetch_message=_fetcher,
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)

    assert result[0]["content"] == (
        "Look at this thread\n\n"
        "Links:\n"
        "[Embedded message: https://team.slack.com/archives/C123/p1234567890123456]"
    )


async def test_embedded_link_includes_metadata() -> None:
    async def _fetcher() -> Any:
        return create_test_message("linked", "linked")

    messages = [
        create_test_message(
            "1",
            "Look at this",
            links=[
                LinkPreview(
                    url="https://team.slack.com/archives/C123/p1234567890123456",
                    title="Original message preview",
                    fetch_message=_fetcher,
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)

    assert result[0]["content"] == (
        "Look at this\n\n"
        "Links:\n"
        "[Embedded message: https://team.slack.com/archives/C123/p1234567890123456]\n"
        "Title: Original message preview"
    )


async def test_mixes_embedded_and_regular_links() -> None:
    async def _fetcher() -> Any:
        return create_test_message("linked", "linked")

    messages = [
        create_test_message(
            "1",
            "Check these",
            links=[
                LinkPreview(
                    url="https://team.slack.com/archives/C123/p1234567890123456",
                    fetch_message=_fetcher,
                ),
                LinkPreview(
                    url="https://vercel.com",
                    title="Vercel",
                    site_name="Vercel",
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)

    assert result[0]["content"] == (
        "Check these\n\n"
        "Links:\n"
        "[Embedded message: https://team.slack.com/archives/C123/p1234567890123456]\n\n"
        "https://vercel.com\nTitle: Vercel\nSite: Vercel"
    )


async def test_no_links_section_when_empty() -> None:
    messages = [create_test_message("1", "No links here")]
    result = await to_ai_messages(messages)
    assert result[0]["content"] == "No links here"


# ---------------------------------------------------------------------------
# Attachment tests
# ---------------------------------------------------------------------------


async def test_includes_image_attachment_as_file_part() -> None:
    data = b"jpeg-data"
    messages = [
        create_test_message(
            "1",
            "Look at this image",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/jpeg",
                    name="photo.jpg",
                    fetch_data=_bytes_source(data),
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    content = result[0]["content"]

    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "Look at this image"}
    assert content[1]["type"] == "file"


async def test_includes_text_file_attachment_as_file_part() -> None:
    data = b'{"key": "value"}'
    messages = [
        create_test_message(
            "1",
            "Here is a config",
            attachments=[
                Attachment(
                    type="file",
                    mime_type="application/json",
                    name="config.json",
                    fetch_data=_bytes_source(data),
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    content = result[0]["content"]

    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "Here is a config"}
    assert content[1]["type"] == "file"


@pytest.mark.parametrize(
    "mime_type",
    [
        "text/plain",
        "text/csv",
        "text/html",
        "application/json",
        "application/xml",
        "application/javascript",
        "application/yaml",
    ],
)
async def test_supports_various_text_mime_types(mime_type: str) -> None:
    messages = [
        create_test_message(
            "1",
            "file",
            attachments=[
                Attachment(
                    type="file",
                    mime_type=mime_type,
                    fetch_data=_bytes_source(b"content"),
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    content = result[0]["content"]

    assert isinstance(content, list)
    assert content[1]["type"] == "file"


async def test_multiple_attachments_as_parts() -> None:
    messages = [
        create_test_message(
            "1",
            "Multiple files",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    fetch_data=_bytes_source(b"png1"),
                ),
                Attachment(
                    type="image",
                    mime_type="image/jpeg",
                    fetch_data=_bytes_source(b"jpg2"),
                ),
                Attachment(
                    type="file",
                    mime_type="text/plain",
                    name="log.txt",
                    fetch_data=_bytes_source(b"log content"),
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    content = result[0]["content"]

    assert isinstance(content, list)
    assert len(content) == 4
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "file"
    assert content[2]["type"] == "file"
    assert content[3]["type"] == "file"


async def test_warns_on_video_attachment() -> None:
    on_unsupported = MagicMock()
    messages = [
        create_test_message(
            "1",
            "Watch this",
            attachments=[
                Attachment(
                    type="video",
                    url="https://example.com/video.mp4",
                    mime_type="video/mp4",
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages, {"onUnsupportedAttachment": on_unsupported})

    assert result[0]["content"] == "Watch this"
    assert on_unsupported.call_count == 1
    assert on_unsupported.call_args[0][0].type == "video"


async def test_warns_on_audio_attachment() -> None:
    on_unsupported = MagicMock()
    messages = [
        create_test_message(
            "1",
            "Listen to this",
            attachments=[
                Attachment(
                    type="audio",
                    url="https://example.com/audio.mp3",
                    mime_type="audio/mpeg",
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages, {"onUnsupportedAttachment": on_unsupported})

    assert result[0]["content"] == "Listen to this"
    assert on_unsupported.call_count == 1
    assert on_unsupported.call_args[0][0].type == "audio"


async def test_skips_non_text_file_silently() -> None:
    on_unsupported = MagicMock()
    messages = [
        create_test_message(
            "1",
            "Here is a PDF",
            attachments=[
                Attachment(
                    type="file",
                    url="https://example.com/doc.pdf",
                    mime_type="application/pdf",
                    name="doc.pdf",
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages, {"onUnsupportedAttachment": on_unsupported})

    assert result[0]["content"] == "Here is a PDF"
    on_unsupported.assert_not_called()


async def test_fetch_data_inlines_image_as_base64() -> None:
    data = b"fake-png-data"
    messages = [
        create_test_message(
            "1",
            "Private image",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    fetch_data=_bytes_source(data),
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    content = result[0]["content"]

    assert isinstance(content, list)
    assert content[1]["type"] == "file"
    expected_b64 = base64.b64encode(data).decode("ascii")
    assert content[1]["data"] == f"data:image/png;base64,{expected_b64}"
    assert content[1]["mediaType"] == "image/png"


async def test_fetch_data_inlines_text_file_as_base64() -> None:
    data = b"error at line 42"
    messages = [
        create_test_message(
            "1",
            "Here is a log",
            attachments=[
                Attachment(
                    type="file",
                    mime_type="text/plain",
                    name="server.log",
                    fetch_data=_bytes_source(data),
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    content = result[0]["content"]

    assert isinstance(content, list)
    assert content[1]["type"] == "file"
    expected_b64 = base64.b64encode(data).decode("ascii")
    assert content[1]["data"] == f"data:text/plain;base64,{expected_b64}"
    assert content[1]["filename"] == "server.log"


async def test_skips_image_when_fetch_data_fails() -> None:
    async def _fail() -> bytes:
        raise RuntimeError("network error")

    messages = [
        create_test_message(
            "1",
            "Image here",
            attachments=[
                Attachment(
                    type="image",
                    url="https://example.com/img.png",
                    mime_type="image/png",
                    fetch_data=_fail,
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    assert result[0]["content"] == "Image here"


async def test_skips_attachments_without_url_or_fetch() -> None:
    messages = [
        create_test_message(
            "1",
            "Uploaded something",
            attachments=[
                Attachment(type="image", mime_type="image/png"),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    assert result[0]["content"] == "Uploaded something"


async def test_keeps_string_content_when_no_supported_attachments() -> None:
    messages = [create_test_message("1", "Just text", attachments=[])]
    result = await to_ai_messages(messages)
    assert isinstance(result[0]["content"], str)


async def test_includes_links_in_text_part_with_attachments() -> None:
    messages = [
        create_test_message(
            "1",
            "Image with link",
            links=[LinkPreview(url="https://example.com", title="Example")],
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    fetch_data=_bytes_source(b"img"),
                ),
            ],
        ),
    ]

    result = await to_ai_messages(messages)
    content = result[0]["content"]

    assert isinstance(content, list)
    text_part = content[0]
    assert text_part["type"] == "text"
    assert "Links:\nhttps://example.com" in text_part["text"]
    assert content[1]["type"] == "file"


# ---------------------------------------------------------------------------
# transformMessage tests
# ---------------------------------------------------------------------------


async def test_transform_message_modifies_text() -> None:
    messages = [create_test_message("1", "Hello <@U123>")]

    def _transform(ai_message: AiMessage, _source: Any) -> AiMessage:
        content = ai_message["content"]
        assert isinstance(content, str)
        return {
            "role": "user",
            "content": content.replace("<@U123>", "@VercelBot"),
        }

    result = await to_ai_messages(messages, {"transformMessage": _transform})
    assert result == [{"role": "user", "content": "Hello @VercelBot"}]


async def test_transform_message_none_skips_message() -> None:
    messages = [
        create_test_message("1", "Keep this"),
        create_test_message("2", "Skip this"),
        create_test_message("3", "Keep this too"),
    ]

    def _transform(ai_message: AiMessage, _source: Any) -> AiMessage | None:
        content = ai_message["content"]
        if isinstance(content, str) and "Skip" in content:
            return None
        return ai_message

    result = await to_ai_messages(messages, {"transformMessage": _transform})
    assert result == [
        {"role": "user", "content": "Keep this"},
        {"role": "user", "content": "Keep this too"},
    ]


async def test_transform_message_receives_source() -> None:
    messages = [
        create_test_message(
            "msg-1",
            "Hello",
            author=_user_author("U1", "alice"),
        ),
    ]

    captured: dict[str, Any] = {}

    def _transform(ai_message: AiMessage, source: Any) -> AiMessage:
        captured["ai"] = ai_message
        captured["source"] = source
        return ai_message

    await to_ai_messages(messages, {"transformMessage": _transform})

    assert captured["ai"] == {"role": "user", "content": "Hello"}
    assert captured["source"].id == "msg-1"
    assert captured["source"].author.user_name == "alice"


async def test_transform_message_async() -> None:
    messages = [create_test_message("1", "Original")]

    async def _transform(ai_message: AiMessage, _source: Any) -> AiMessage:
        await asyncio.sleep(0)
        return {"role": "user", "content": "Transformed"}

    result = await to_ai_messages(messages, {"transformMessage": _transform})
    assert result == [{"role": "user", "content": "Transformed"}]


async def test_transform_receives_multipart_content_for_attachments() -> None:
    messages = [
        create_test_message(
            "1",
            "Image here",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    fetch_data=_bytes_source(b"png-data"),
                ),
            ],
        ),
    ]

    captured: dict[str, Any] = {}

    def _transform(ai_message: AiMessage, _source: Any) -> AiMessage:
        captured["ai"] = ai_message
        return ai_message

    await to_ai_messages(messages, {"transformMessage": _transform})
    ai_msg = captured["ai"]
    assert ai_msg["role"] == "user"
    content: list[AiMessagePart] = ai_msg["content"]
    assert isinstance(content, list)
    assert len(content) == 2
