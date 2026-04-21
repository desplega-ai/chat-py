"""Tests for shared card utility functions.

Python port of upstream ``packages/adapter-shared/src/card-utils.test.ts``.

Card elements are dict-shaped per ``chat-py`` CLAUDE.md, so we construct them
as plain dicts here rather than waiting for chat-core ``Card`` builders to be
ported.
"""

from __future__ import annotations

from typing import Any

from chat_adapter_shared.card_utils import (
    BUTTON_STYLE_MAPPINGS,
    card_to_fallback_text,
    create_emoji_converter,
    escape_table_cell,
    map_button_style,
    render_gfm_table,
)

# ---------------------------------------------------------------------------
# Local builder helpers — mirror chat-core builder names so this test reads
# similarly to the TypeScript original. These produce plain dicts matching
# the dict-based AST defined in chat-py CLAUDE.md.
# ---------------------------------------------------------------------------


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


def Field(*, label: str, value: str) -> dict[str, Any]:
    return {"type": "field", "label": label, "value": value}


def Fields(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "fields", "children": children}


def Button(
    *,
    id: str,
    label: str,
    style: str | None = None,
) -> dict[str, Any]:
    btn: dict[str, Any] = {"type": "button", "id": id, "label": label}
    if style is not None:
        btn["style"] = style
    return btn


def Actions(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "actions", "children": children}


def Divider() -> dict[str, Any]:
    return {"type": "divider"}


def Table(*, headers: list[str], rows: list[list[str]]) -> dict[str, Any]:
    return {"type": "table", "headers": headers, "rows": rows}


# ---------------------------------------------------------------------------
# create_emoji_converter
# ---------------------------------------------------------------------------


class TestCreateEmojiConverter:
    def test_creates_a_slack_emoji_converter(self) -> None:
        convert = create_emoji_converter("slack")
        assert convert("{{emoji:wave}} Hello") == ":wave: Hello"
        assert convert("{{emoji:fire}}") == ":fire:"

    def test_creates_a_teams_emoji_converter(self) -> None:
        convert = create_emoji_converter("teams")
        result = convert("{{emoji:wave}} Hello")
        assert "Hello" in result
        assert "{{emoji:" not in result

    def test_creates_a_gchat_emoji_converter(self) -> None:
        convert = create_emoji_converter("gchat")
        result = convert("{{emoji:wave}} Hello")
        assert "Hello" in result
        assert "{{emoji:" not in result

    def test_returns_text_unchanged_when_no_emoji_placeholders(self) -> None:
        convert = create_emoji_converter("slack")
        assert convert("Hello world") == "Hello world"


# ---------------------------------------------------------------------------
# map_button_style
# ---------------------------------------------------------------------------


class TestMapButtonStyleSlack:
    def test_maps_primary_to_primary(self) -> None:
        assert map_button_style("primary", "slack") == "primary"

    def test_maps_danger_to_danger(self) -> None:
        assert map_button_style("danger", "slack") == "danger"

    def test_returns_none_for_none_style(self) -> None:
        assert map_button_style(None, "slack") is None


class TestMapButtonStyleTeams:
    def test_maps_primary_to_positive(self) -> None:
        assert map_button_style("primary", "teams") == "positive"

    def test_maps_danger_to_destructive(self) -> None:
        assert map_button_style("danger", "teams") == "destructive"

    def test_returns_none_for_none_style(self) -> None:
        assert map_button_style(None, "teams") is None


class TestMapButtonStyleGchat:
    def test_maps_primary_to_primary(self) -> None:
        assert map_button_style("primary", "gchat") == "primary"

    def test_maps_danger_to_danger(self) -> None:
        assert map_button_style("danger", "gchat") == "danger"


# ---------------------------------------------------------------------------
# BUTTON_STYLE_MAPPINGS
# ---------------------------------------------------------------------------


class TestButtonStyleMappings:
    def test_has_mappings_for_all_platforms(self) -> None:
        assert BUTTON_STYLE_MAPPINGS["slack"] is not None
        assert BUTTON_STYLE_MAPPINGS["teams"] is not None
        assert BUTTON_STYLE_MAPPINGS["gchat"] is not None
        assert BUTTON_STYLE_MAPPINGS["discord"] is not None

    def test_has_primary_and_danger_for_each_platform(self) -> None:
        for platform in ("slack", "teams", "gchat", "discord"):
            assert BUTTON_STYLE_MAPPINGS[platform]["primary"] is not None
            assert BUTTON_STYLE_MAPPINGS[platform]["danger"] is not None


# ---------------------------------------------------------------------------
# card_to_fallback_text
# ---------------------------------------------------------------------------


class TestCardToFallbackText:
    def test_formats_title_with_bold(self) -> None:
        card = Card(title="Test Title")
        assert card_to_fallback_text(card) == "*Test Title*"

    def test_formats_title_and_subtitle(self) -> None:
        card = Card(title="Title", subtitle="Subtitle")
        assert card_to_fallback_text(card) == "*Title*\nSubtitle"

    def test_uses_double_asterisks_for_markdown_bold_format(self) -> None:
        card = Card(title="Title")
        assert card_to_fallback_text(card, {"bold_format": "**"}) == "**Title**"

    def test_uses_double_line_breaks_when_specified(self) -> None:
        card = Card(title="Title", subtitle="Subtitle")
        assert (
            card_to_fallback_text(card, {"line_break": "\n\n"})
            == "*Title*\n\nSubtitle"
        )

    def test_formats_text_children(self) -> None:
        card = Card(title="Card", children=[CardText("Some content")])
        assert card_to_fallback_text(card) == "*Card*\nSome content"

    def test_formats_fields(self) -> None:
        card = Card(
            children=[
                Fields([
                    Field(label="Name", value="John"),
                    Field(label="Age", value="30"),
                ]),
            ],
        )
        assert card_to_fallback_text(card) == "Name: John\nAge: 30"

    def test_formats_fields_as_label_value_pairs(self) -> None:
        card = Card(children=[Fields([Field(label="Key", value="Value")])])
        assert card_to_fallback_text(card, {"bold_format": "**"}) == "Key: Value"

    def test_excludes_actions_from_fallback_text(self) -> None:
        card = Card(
            children=[
                Actions([
                    Button(id="ok", label="OK"),
                    Button(id="cancel", label="Cancel"),
                ]),
            ],
        )
        assert card_to_fallback_text(card) == ""

    def test_formats_dividers_as_horizontal_rules(self) -> None:
        card = Card(
            title="Title",
            children=[Divider(), CardText("After divider")],
        )
        assert card_to_fallback_text(card) == "*Title*\n---\nAfter divider"

    def test_converts_emoji_placeholders_when_platform_specified(self) -> None:
        card = Card(
            title="{{emoji:wave}} Welcome",
            children=[CardText("{{emoji:fire}} Hot stuff")],
        )
        result = card_to_fallback_text(card, {"platform": "slack"})
        assert result == "*:wave: Welcome*\n:fire: Hot stuff"

    def test_leaves_emoji_placeholders_when_no_platform_specified(self) -> None:
        card = Card(title="{{emoji:wave}} Welcome")
        result = card_to_fallback_text(card)
        assert result == "*{{emoji:wave}} Welcome*"

    def test_handles_complex_card_with_all_elements(self) -> None:
        card = Card(
            title="Order #123",
            subtitle="Your order is confirmed",
            children=[
                CardText("Thank you for your purchase!"),
                Divider(),
                Fields([
                    Field(label="Status", value="Processing"),
                    Field(label="Total", value="$99.99"),
                ]),
                Actions([
                    Button(id="view", label="View Order", style="primary"),
                    Button(id="cancel", label="Cancel", style="danger"),
                ]),
            ],
        )

        result = card_to_fallback_text(
            card,
            {"bold_format": "**", "line_break": "\n\n"},
        )

        assert "**Order #123**" in result
        assert "Your order is confirmed" in result
        assert "Thank you for your purchase!" in result
        assert "---" in result
        assert "Status: Processing" in result
        assert "Total: $99.99" in result
        assert "[View Order]" not in result
        assert "[Cancel]" not in result

    def test_handles_empty_card(self) -> None:
        card = Card()
        assert card_to_fallback_text(card) == ""

    def test_handles_card_with_only_children(self) -> None:
        card = Card(children=[CardText("Just text")])
        assert card_to_fallback_text(card) == "Just text"


# ---------------------------------------------------------------------------
# escape_table_cell
# ---------------------------------------------------------------------------


class TestEscapeTableCell:
    def test_escapes_pipe_characters(self) -> None:
        assert escape_table_cell("a|b") == "a\\|b"

    def test_escapes_multiple_pipes(self) -> None:
        assert escape_table_cell("a|b|c") == "a\\|b\\|c"

    def test_escapes_backslashes_before_pipes(self) -> None:
        assert escape_table_cell("a\\|b") == "a\\\\\\|b"

    def test_escapes_standalone_backslashes(self) -> None:
        assert escape_table_cell("a\\b") == "a\\\\b"

    def test_replaces_newlines_with_spaces(self) -> None:
        assert escape_table_cell("line1\nline2") == "line1 line2"

    def test_handles_text_with_no_special_characters(self) -> None:
        assert escape_table_cell("hello") == "hello"

    def test_handles_empty_string(self) -> None:
        assert escape_table_cell("") == ""


# ---------------------------------------------------------------------------
# render_gfm_table
# ---------------------------------------------------------------------------


class TestRenderGfmTable:
    def test_renders_a_basic_table(self) -> None:
        table = Table(
            headers=["Name", "Age"],
            rows=[["Alice", "30"], ["Bob", "25"]],
        )
        assert render_gfm_table(table) == [
            "| Name | Age |",
            "| --- | --- |",
            "| Alice | 30 |",
            "| Bob | 25 |",
        ]

    def test_escapes_pipes_in_cell_values(self) -> None:
        table = Table(
            headers=["Command", "Description"],
            rows=[["a|b", "pipes|here"]],
        )
        assert render_gfm_table(table) == [
            "| Command | Description |",
            "| --- | --- |",
            "| a\\|b | pipes\\|here |",
        ]

    def test_escapes_backslashes_in_cell_values(self) -> None:
        table = Table(
            headers=["Path"],
            rows=[["C:\\Users\\test"]],
        )
        lines = render_gfm_table(table)
        assert lines[2] == "| C:\\\\Users\\\\test |"

    def test_handles_empty_rows(self) -> None:
        table = Table(headers=["A", "B"], rows=[])
        assert render_gfm_table(table) == [
            "| A | B |",
            "| --- | --- |",
        ]
