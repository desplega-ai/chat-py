"""Tests for shared adapter utility functions.

Python port of upstream ``packages/adapter-shared/src/adapter-utils.test.ts``.
Like ``test_card_utils``, we use plain dicts for ``CardElement`` /
``AdapterPostableMessage`` per the dict-based AST convention in chat-py.
"""

from __future__ import annotations

from typing import Any

from chat_adapter_shared.adapter_utils import extract_card, extract_files


def Card(
    *,
    title: str | None = None,
    subtitle: str | None = None,
    image_url: str | None = None,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card: dict[str, Any] = {"type": "card", "children": children or []}
    if title is not None:
        card["title"] = title
    if subtitle is not None:
        card["subtitle"] = subtitle
    if image_url is not None:
        card["imageUrl"] = image_url
    return card


def CardText(content: str) -> dict[str, Any]:
    return {"type": "text", "content": content}


# ---------------------------------------------------------------------------
# extract_card
# ---------------------------------------------------------------------------


class TestExtractCardWithCardElement:
    def test_extracts_a_card_element_passed_directly(self) -> None:
        card = Card(title="Test Card", children=[CardText("Content")])
        result = extract_card(card)
        assert result is card

    def test_extracts_a_card_with_all_properties(self) -> None:
        card = Card(
            title="Order #123",
            subtitle="Processing",
            image_url="https://example.com/img.png",
            children=[CardText("Details")],
        )
        result = extract_card(card)
        assert result == card
        assert result is not None
        assert result["title"] == "Order #123"
        assert result["subtitle"] == "Processing"


class TestExtractCardWithPostableCardObject:
    def test_extracts_card_from_card_dict(self) -> None:
        card = Card(title="Nested Card")
        message: dict[str, Any] = {"card": card}
        result = extract_card(message)
        assert result is card

    def test_extracts_card_from_postable_card_with_fallback_text(self) -> None:
        card = Card(title="With Fallback")
        message: dict[str, Any] = {
            "card": card,
            "fallbackText": "Plain text version",
        }
        result = extract_card(message)
        assert result is card

    def test_extracts_card_from_postable_card_with_files(self) -> None:
        card = Card(title="With Files")
        files: list[dict[str, Any]] = [
            {"data": b"test", "filename": "test.txt"},
        ]
        message: dict[str, Any] = {"card": card, "files": files}
        result = extract_card(message)
        assert result is card


class TestExtractCardWithNonCardMessages:
    def test_returns_none_for_plain_string(self) -> None:
        result = extract_card("Hello world")
        assert result is None

    def test_returns_none_for_postable_raw(self) -> None:
        message: dict[str, Any] = {"raw": "Raw text"}
        result = extract_card(message)
        assert result is None

    def test_returns_none_for_postable_markdown(self) -> None:
        message: dict[str, Any] = {"markdown": "**Bold** text"}
        result = extract_card(message)
        assert result is None

    def test_returns_none_for_postable_ast(self) -> None:
        message: dict[str, Any] = {"ast": {"type": "root", "children": []}}
        result = extract_card(message)
        assert result is None

    def test_returns_none_for_none_input(self) -> None:
        result = extract_card(None)  # type: ignore[arg-type]
        assert result is None

    def test_returns_none_for_object_without_card_or_type(self) -> None:
        message: dict[str, Any] = {"something": "else"}
        result = extract_card(message)
        assert result is None

    def test_returns_none_for_non_card_type_object(self) -> None:
        message: dict[str, Any] = {"type": "text", "content": "not a card"}
        result = extract_card(message)
        assert result is None


# ---------------------------------------------------------------------------
# extract_files
# ---------------------------------------------------------------------------


class TestExtractFilesWithFilesPresent:
    def test_extracts_files_array_from_postable_raw(self) -> None:
        files: list[dict[str, Any]] = [
            {"data": b"content1", "filename": "file1.txt"},
            {"data": b"content2", "filename": "file2.txt"},
        ]
        message: dict[str, Any] = {"raw": "Text", "files": files}
        result = extract_files(message)
        assert result == files
        assert len(result) == 2

    def test_extracts_files_array_from_postable_markdown(self) -> None:
        files: list[dict[str, Any]] = [
            {
                "data": b"image",
                "filename": "image.png",
                "mimeType": "image/png",
            }
        ]
        message: dict[str, Any] = {"markdown": "**Text**", "files": files}
        result = extract_files(message)
        assert result == files
        assert result[0]["mimeType"] == "image/png"

    def test_extracts_files_array_from_postable_card(self) -> None:
        card = Card(title="Test")
        files: list[dict[str, Any]] = [{"data": b"doc", "filename": "doc.pdf"}]
        message: dict[str, Any] = {"card": card, "files": files}
        result = extract_files(message)
        assert result == files

    def test_handles_blob_like_data_in_files(self) -> None:
        import io

        blob = io.BytesIO(b"content")
        files: list[dict[str, Any]] = [{"data": blob, "filename": "blob.txt"}]
        message: dict[str, Any] = {"raw": "Text", "files": files}
        result = extract_files(message)
        assert len(result) == 1
        assert result[0]["data"] is blob

    def test_handles_bytearray_data_in_files(self) -> None:
        buffer = bytearray(8)
        files: list[dict[str, Any]] = [{"data": buffer, "filename": "binary.bin"}]
        message: dict[str, Any] = {"raw": "Text", "files": files}
        result = extract_files(message)
        assert len(result) == 1
        assert result[0]["data"] is buffer


class TestExtractFilesWithEmptyOrMissingFiles:
    def test_returns_empty_list_when_files_property_is_empty_list(self) -> None:
        message: dict[str, Any] = {"raw": "Text", "files": []}
        result = extract_files(message)
        assert result == []

    def test_returns_empty_list_when_files_property_is_none(self) -> None:
        message: dict[str, Any] = {"raw": "Text", "files": None}
        result = extract_files(message)
        assert result == []

    def test_returns_empty_list_for_postable_raw_without_files(self) -> None:
        message: dict[str, Any] = {"raw": "Just text"}
        result = extract_files(message)
        assert result == []

    def test_returns_empty_list_for_postable_markdown_without_files(self) -> None:
        message: dict[str, Any] = {"markdown": "**Bold**"}
        result = extract_files(message)
        assert result == []


class TestExtractFilesWithNonObjectMessages:
    def test_returns_empty_list_for_plain_string(self) -> None:
        result = extract_files("Hello world")
        assert result == []

    def test_returns_empty_list_for_card_element_no_files(self) -> None:
        card = Card(title="Test")
        result = extract_files(card)
        assert result == []

    def test_returns_empty_list_for_none_input(self) -> None:
        result = extract_files(None)  # type: ignore[arg-type]
        assert result == []
