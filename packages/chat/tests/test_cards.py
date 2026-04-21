"""Tests for :mod:`chat.cards` — mirrors upstream ``cards.test.ts``."""

from __future__ import annotations

import pytest
from chat.cards import (
    Actions,
    Button,
    Card,
    CardLink,
    CardText,
    Divider,
    Field,
    Fields,
    Image,
    LinkButton,
    Section,
    Table,
    Text,
    card_child_to_fallback_text,
    card_to_fallback_text,
    is_card_element,
)
from chat.modals import RadioSelect, Select, SelectOption

# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------


class TestCard:
    def test_creates_card_with_title(self) -> None:
        card = Card(title="My Card")
        assert card["type"] == "card"
        assert card["title"] == "My Card"
        assert card["children"] == []

    def test_creates_card_with_all_options(self) -> None:
        card = Card(
            title="Order #1234",
            subtitle="Processing",
            image_url="https://example.com/image.png",
            children=[Text("Hello")],
        )
        assert card["title"] == "Order #1234"
        assert card["subtitle"] == "Processing"
        assert card["imageUrl"] == "https://example.com/image.png"
        assert len(card["children"]) == 1

    def test_creates_empty_card(self) -> None:
        card = Card()
        assert card["type"] == "card"
        assert card["children"] == []


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


class TestText:
    def test_creates_text_element(self) -> None:
        t = Text("Hello, world!")
        assert t["type"] == "text"
        assert t["content"] == "Hello, world!"
        assert "style" not in t

    def test_creates_bold_text_element(self) -> None:
        t = Text("Important", style="bold")
        assert t["content"] == "Important"
        assert t["style"] == "bold"

    def test_creates_muted_text_element(self) -> None:
        t = Text("Subtle note", style="muted")
        assert t["style"] == "muted"

    def test_card_text_alias_equals_text(self) -> None:
        assert CardText is Text


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


class TestImage:
    def test_creates_image_element(self) -> None:
        img = Image(url="https://example.com/img.png")
        assert img["type"] == "image"
        assert img["url"] == "https://example.com/img.png"
        assert "alt" not in img

    def test_creates_image_with_alt(self) -> None:
        img = Image(url="https://example.com/img.png", alt="A beautiful sunset")
        assert img["alt"] == "A beautiful sunset"


# ---------------------------------------------------------------------------
# Divider
# ---------------------------------------------------------------------------


class TestDivider:
    def test_creates_divider_element(self) -> None:
        d = Divider()
        assert d["type"] == "divider"


# ---------------------------------------------------------------------------
# Button
# ---------------------------------------------------------------------------


class TestButton:
    def test_creates_button_element(self) -> None:
        b = Button(id="submit", label="Submit")
        assert b["type"] == "button"
        assert b["id"] == "submit"
        assert b["label"] == "Submit"
        assert "style" not in b
        assert "value" not in b

    def test_creates_primary_button(self) -> None:
        b = Button(id="ok", label="OK", style="primary")
        assert b["style"] == "primary"

    def test_creates_danger_button_with_value(self) -> None:
        b = Button(id="delete", label="Delete", style="danger", value="item-123")
        assert b["style"] == "danger"
        assert b["value"] == "item-123"

    def test_creates_modal_button(self) -> None:
        b = Button(id="config", label="Configure", action_type="modal")
        assert b["actionType"] == "modal"

    def test_creates_disabled_button(self) -> None:
        b = Button(id="wait", label="Wait", disabled=True)
        assert b["disabled"] is True


# ---------------------------------------------------------------------------
# LinkButton
# ---------------------------------------------------------------------------


class TestLinkButton:
    def test_creates_link_button_element(self) -> None:
        b = LinkButton(url="https://example.com", label="Visit Site")
        assert b["type"] == "link-button"
        assert b["url"] == "https://example.com"
        assert b["label"] == "Visit Site"
        assert "style" not in b

    def test_creates_styled_link_button(self) -> None:
        b = LinkButton(url="https://docs.example.com", label="View Docs", style="primary")
        assert b["style"] == "primary"


# ---------------------------------------------------------------------------
# CardLink
# ---------------------------------------------------------------------------


class TestCardLink:
    def test_creates_link_element(self) -> None:
        link = CardLink(url="https://example.com", label="Visit Site")
        assert link["type"] == "link"
        assert link["url"] == "https://example.com"
        assert link["label"] == "Visit Site"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class TestActions:
    def test_creates_actions_container(self) -> None:
        actions = Actions(
            [
                Button(id="ok", label="OK"),
                Button(id="cancel", label="Cancel"),
            ]
        )
        assert actions["type"] == "actions"
        assert len(actions["children"]) == 2
        assert actions["children"][0]["label"] == "OK"
        assert actions["children"][1]["label"] == "Cancel"

    def test_creates_actions_with_mixed_button_types(self) -> None:
        actions = Actions(
            [
                Button(id="submit", label="Submit", style="primary"),
                LinkButton(url="https://example.com/help", label="Help"),
            ]
        )
        assert len(actions["children"]) == 2
        assert actions["children"][0]["type"] == "button"
        assert actions["children"][1]["type"] == "link-button"

    def test_creates_empty_actions(self) -> None:
        actions = Actions([])
        assert actions["children"] == []


# ---------------------------------------------------------------------------
# Section / Field / Fields / Table
# ---------------------------------------------------------------------------


class TestSection:
    def test_creates_section_container(self) -> None:
        section = Section([Text("Content"), Divider()])
        assert section["type"] == "section"
        assert len(section["children"]) == 2


class TestField:
    def test_creates_field_element(self) -> None:
        f = Field(label="Status", value="Active")
        assert f["type"] == "field"
        assert f["label"] == "Status"
        assert f["value"] == "Active"


class TestFields:
    def test_creates_fields_container(self) -> None:
        fields = Fields(
            [
                Field(label="Name", value="John"),
                Field(label="Email", value="john@example.com"),
            ]
        )
        assert fields["type"] == "fields"
        assert len(fields["children"]) == 2


class TestTable:
    def test_creates_table_element(self) -> None:
        table = Table(
            headers=["A", "B"],
            rows=[["1", "2"], ["3", "4"]],
            align=["left", "right"],
        )
        assert table["type"] == "table"
        assert table["headers"] == ["A", "B"]
        assert table["rows"] == [["1", "2"], ["3", "4"]]
        assert table["align"] == ["left", "right"]


# ---------------------------------------------------------------------------
# is_card_element
# ---------------------------------------------------------------------------


class TestIsCardElement:
    def test_returns_true_for_card_element(self) -> None:
        card = Card(title="Test")
        assert is_card_element(card) is True

    @pytest.mark.parametrize(
        "value",
        [
            {"type": "text", "content": "hello"},
            {"type": "button", "id": "x", "label": "X"},
            "string",
            None,
            123,
            {},
        ],
    )
    def test_returns_false_for_non_card(self, value: object) -> None:
        assert is_card_element(value) is False


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_composes_complete_card_with_all_element_types() -> None:
    card = Card(
        title="Order #1234",
        subtitle="Processing your order",
        image_url="https://example.com/order.png",
        children=[
            Text("Thank you for your order!"),
            CardLink(url="https://example.com/order/1234", label="View order details"),
            Divider(),
            Fields(
                [
                    Field(label="Order ID", value="#1234"),
                    Field(label="Total", value="$99.99"),
                ]
            ),
            Section(
                [
                    Text("Items:", style="bold"),
                    Text("2x Widget, 1x Gadget", style="muted"),
                ]
            ),
            Divider(),
            Actions(
                [
                    Button(id="track", label="Track Order", style="primary"),
                    Button(
                        id="cancel",
                        label="Cancel Order",
                        style="danger",
                        value="order-1234",
                    ),
                ]
            ),
        ],
    )

    assert card["type"] == "card"
    assert card["title"] == "Order #1234"
    assert len(card["children"]) == 7

    kinds = [c["type"] for c in card["children"]]
    assert kinds == ["text", "link", "divider", "fields", "section", "divider", "actions"]

    fields = card["children"][3]
    assert fields["type"] == "fields"
    assert len(fields["children"]) == 2

    actions = card["children"][6]
    assert actions["type"] == "actions"
    assert len(actions["children"]) == 2
    assert actions["children"][0]["id"] == "track"
    assert actions["children"][1]["value"] == "order-1234"


# ---------------------------------------------------------------------------
# Select / RadioSelect cross-import validation
# ---------------------------------------------------------------------------


class TestSelectValidation:
    def test_select_raises_when_options_empty(self) -> None:
        with pytest.raises(ValueError, match="at least one option"):
            Select(id="test", label="Test", options=[])

    def test_select_creates_with_valid_options(self) -> None:
        s = Select(
            id="test",
            label="Test",
            options=[SelectOption(label="A", value="a")],
        )
        assert s["type"] == "select"
        assert len(s["options"]) == 1

    def test_radio_select_raises_when_options_empty(self) -> None:
        with pytest.raises(ValueError, match="at least one option"):
            RadioSelect(id="test", label="Test", options=[])

    def test_radio_select_creates_with_valid_options(self) -> None:
        r = RadioSelect(
            id="test",
            label="Test",
            options=[SelectOption(label="A", value="a")],
        )
        assert r["type"] == "radio_select"
        assert len(r["options"]) == 1


# ---------------------------------------------------------------------------
# card_to_fallback_text
# ---------------------------------------------------------------------------


class TestCardToFallbackText:
    def test_renders_title_and_subtitle(self) -> None:
        card = Card(title="Hello", subtitle="Sub")
        assert card_to_fallback_text(card) == "**Hello**\nSub"

    def test_renders_text_children(self) -> None:
        card = Card(title="T", children=[Text("a"), Text("b")])
        assert card_to_fallback_text(card) == "**T**\na\nb"

    def test_skips_actions_from_fallback(self) -> None:
        card = Card(
            title="T",
            children=[Text("a"), Actions([Button(id="x", label="X")])],
        )
        # Actions must not appear in the fallback
        assert card_to_fallback_text(card) == "**T**\na"

    def test_renders_link_child(self) -> None:
        card = Card(children=[CardLink(url="https://a.b", label="Link")])
        assert card_to_fallback_text(card) == "Link (https://a.b)"

    def test_renders_fields_child(self) -> None:
        card = Card(
            children=[
                Fields(
                    [
                        Field(label="K1", value="V1"),
                        Field(label="K2", value="V2"),
                    ]
                )
            ]
        )
        assert card_to_fallback_text(card) == "K1: V1\nK2: V2"

    def test_renders_section_child(self) -> None:
        card = Card(
            children=[
                Section([Text("line one"), Text("line two")]),
            ]
        )
        assert card_to_fallback_text(card) == "line one\nline two"

    def test_renders_table_child(self) -> None:
        card = Card(
            children=[
                Table(headers=["A", "B"], rows=[["1", "2"]]),
            ]
        )
        text = card_to_fallback_text(card)
        assert "A" in text and "B" in text and "1" in text and "2" in text

    def test_unknown_child_type_returns_none(self) -> None:
        assert card_child_to_fallback_text({"type": "mystery"}) is None
