"""Tests for the Google Chat Cards v2 converter.

Mirrors upstream ``packages/adapter-gchat/src/cards.test.ts``.
"""

from __future__ import annotations

from chat import (
    Actions,
    Button,
    Card,
    CardLink,
    Divider,
    Field,
    Fields,
    Image,
    LinkButton,
    RadioSelect,
    Section,
    Select,
    SelectOption,
)
from chat import (
    Text as CardText,
)
from chat_adapter_gchat.cards import card_to_fallback_text, card_to_google_card


class TestCardToGoogleCard:
    def test_creates_valid_card_structure(self) -> None:
        card = Card(title="Test")
        gchat_card = card_to_google_card(card)

        assert "card" in gchat_card
        assert isinstance(gchat_card["card"]["sections"], list)

    def test_accepts_optional_card_id(self) -> None:
        card = Card(title="Test")
        gchat_card = card_to_google_card(card, "my-card-id")
        assert gchat_card["cardId"] == "my-card-id"

    def test_converts_card_with_title(self) -> None:
        card = Card(title="Welcome Message")
        gchat_card = card_to_google_card(card)
        assert gchat_card["card"]["header"] == {"title": "Welcome Message"}

    def test_converts_card_with_title_and_subtitle(self) -> None:
        card = Card(title="Order Update", subtitle="Your package is on its way")
        gchat_card = card_to_google_card(card)
        assert gchat_card["card"]["header"] == {
            "title": "Order Update",
            "subtitle": "Your package is on its way",
        }

    def test_converts_card_with_header_image(self) -> None:
        card = Card(title="Product", image_url="https://example.com/product.png")
        gchat_card = card_to_google_card(card)
        assert gchat_card["card"]["header"] == {
            "title": "Product",
            "imageUrl": "https://example.com/product.png",
            "imageType": "SQUARE",
        }

    def test_converts_text_elements_to_text_paragraph(self) -> None:
        card = Card(
            children=[
                CardText("Regular text"),
                CardText("Bold text", style="bold"),
            ]
        )
        gchat_card = card_to_google_card(card)
        sections = gchat_card["card"]["sections"]
        assert len(sections) == 1
        widgets = sections[0]["widgets"]
        assert len(widgets) == 2
        assert widgets[0] == {"textParagraph": {"text": "Regular text"}}
        assert widgets[1] == {"textParagraph": {"text": "*Bold text*"}}

    def test_converts_image_elements(self) -> None:
        card = Card(children=[Image(url="https://example.com/img.png", alt="My image")])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 1
        assert widgets[0] == {
            "image": {
                "imageUrl": "https://example.com/img.png",
                "altText": "My image",
            }
        }

    def test_converts_divider_elements(self) -> None:
        card = Card(children=[Divider()])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 1
        assert widgets[0] == {"divider": {}}

    def test_converts_actions_with_buttons_to_button_list(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Button(id="approve", label="Approve", style="primary"),
                        Button(
                            id="reject",
                            label="Reject",
                            style="danger",
                            value="data-123",
                        ),
                        Button(id="skip", label="Skip"),
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 1
        button_list = widgets[0]["buttonList"]
        buttons = button_list["buttons"]
        assert len(buttons) == 3

        assert buttons[0] == {
            "text": "Approve",
            "onClick": {
                "action": {
                    "function": "approve",
                    "parameters": [{"key": "actionId", "value": "approve"}],
                }
            },
            "color": {"red": 0.2, "green": 0.5, "blue": 0.9},
        }

        assert buttons[1] == {
            "text": "Reject",
            "onClick": {
                "action": {
                    "function": "reject",
                    "parameters": [
                        {"key": "actionId", "value": "reject"},
                        {"key": "value", "value": "data-123"},
                    ],
                }
            },
            "color": {"red": 0.9, "green": 0.2, "blue": 0.2},
        }

        assert buttons[2] == {
            "text": "Skip",
            "onClick": {
                "action": {
                    "function": "skip",
                    "parameters": [{"key": "actionId", "value": "skip"}],
                }
            },
        }

    def test_sets_disabled_on_button(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Button(
                            id="cancel",
                            label="Cancelled",
                            style="danger",
                            disabled=True,
                        ),
                        Button(id="retry", label="Retry"),
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        buttons = widgets[0]["buttonList"]["buttons"]
        assert len(buttons) == 2

        assert buttons[0] == {
            "text": "Cancelled",
            "onClick": {
                "action": {
                    "function": "cancel",
                    "parameters": [{"key": "actionId", "value": "cancel"}],
                }
            },
            "color": {"red": 0.9, "green": 0.2, "blue": 0.2},
            "disabled": True,
        }

        # Non-disabled button should not have the disabled key
        assert buttons[1] == {
            "text": "Retry",
            "onClick": {
                "action": {
                    "function": "retry",
                    "parameters": [{"key": "actionId", "value": "retry"}],
                }
            },
        }

    def test_uses_endpoint_url_as_function_when_provided(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Button(id="approve", label="Approve"),
                        Button(id="reject", label="Reject", value="data-123"),
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(
            card, {"endpointUrl": "https://example.com/api/webhooks/gchat"}
        )
        buttons = gchat_card["card"]["sections"][0]["widgets"][0]["buttonList"]["buttons"]

        assert buttons[0] == {
            "text": "Approve",
            "onClick": {
                "action": {
                    "function": "https://example.com/api/webhooks/gchat",
                    "parameters": [{"key": "actionId", "value": "approve"}],
                }
            },
        }
        assert buttons[1] == {
            "text": "Reject",
            "onClick": {
                "action": {
                    "function": "https://example.com/api/webhooks/gchat",
                    "parameters": [
                        {"key": "actionId", "value": "reject"},
                        {"key": "value", "value": "data-123"},
                    ],
                }
            },
        }

    def test_converts_link_buttons_with_open_link(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        LinkButton(
                            url="https://example.com/docs",
                            label="View Docs",
                            style="primary",
                        )
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 1
        buttons = widgets[0]["buttonList"]["buttons"]
        assert len(buttons) == 1
        assert buttons[0] == {
            "text": "View Docs",
            "onClick": {"openLink": {"url": "https://example.com/docs"}},
            "color": {"red": 0.2, "green": 0.5, "blue": 0.9},
        }

    def test_converts_select_to_dropdown_selection_input(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="priority",
                            label="Priority",
                            options=[
                                SelectOption(label="High", value="high", description="Urgent"),
                                SelectOption(label="Normal", value="normal"),
                            ],
                            initial_option="normal",
                        )
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(
            card, {"endpointUrl": "https://example.com/api/webhooks/gchat"}
        )
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 1
        assert widgets[0] == {
            "selectionInput": {
                "name": "priority",
                "label": "Priority",
                "type": "DROPDOWN",
                "items": [
                    {"text": "High", "value": "high"},
                    {"text": "Normal", "value": "normal", "selected": True},
                ],
                "onChangeAction": {
                    "function": "https://example.com/api/webhooks/gchat",
                    "parameters": [{"key": "actionId", "value": "priority"}],
                },
            }
        }

    def test_converts_radio_select_to_radio_button_selection_input(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        RadioSelect(
                            id="status",
                            label="Status",
                            options=[
                                SelectOption(label="Open", value="open"),
                                SelectOption(label="Closed", value="closed"),
                            ],
                            initial_option="open",
                        )
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 1
        assert widgets[0] == {
            "selectionInput": {
                "name": "status",
                "label": "Status",
                "type": "RADIO_BUTTON",
                "items": [
                    {"text": "Open", "value": "open", "selected": True},
                    {"text": "Closed", "value": "closed"},
                ],
                "onChangeAction": {
                    "function": "status",
                    "parameters": [{"key": "actionId", "value": "status"}],
                },
            }
        }

    def test_preserves_action_order_for_mixed_types(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Button(id="refresh", label="Refresh"),
                        Select(
                            id="category",
                            label="Category",
                            options=[
                                SelectOption(label="Alpha", value="alpha"),
                                SelectOption(label="Beta", value="beta"),
                            ],
                        ),
                        LinkButton(url="https://example.com/docs", label="Docs"),
                        RadioSelect(
                            id="view",
                            label="View",
                            options=[
                                SelectOption(label="Summary", value="summary"),
                                SelectOption(label="Detailed", value="detailed"),
                            ],
                        ),
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]

        assert len(widgets) == 4
        assert len(widgets[0]["buttonList"]["buttons"]) == 1
        assert widgets[0]["buttonList"]["buttons"][0] == {
            "text": "Refresh",
            "onClick": {
                "action": {
                    "function": "refresh",
                    "parameters": [{"key": "actionId", "value": "refresh"}],
                }
            },
        }
        assert widgets[1]["selectionInput"]["name"] == "category"
        assert widgets[1]["selectionInput"]["type"] == "DROPDOWN"
        assert widgets[2]["buttonList"]["buttons"] == [
            {
                "text": "Docs",
                "onClick": {"openLink": {"url": "https://example.com/docs"}},
            }
        ]
        assert widgets[3]["selectionInput"]["name"] == "view"
        assert widgets[3]["selectionInput"]["type"] == "RADIO_BUTTON"

    def test_converts_fields_to_decorated_text_widgets(self) -> None:
        card = Card(
            children=[
                Fields(
                    [
                        Field(label="Status", value="Active"),
                        Field(label="Priority", value="High"),
                    ]
                )
            ]
        )
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 2
        assert widgets[0] == {"decoratedText": {"topLabel": "Status", "text": "Active"}}
        assert widgets[1] == {"decoratedText": {"topLabel": "Priority", "text": "High"}}

    def test_creates_separate_sections_for_section_children(self) -> None:
        card = Card(
            children=[
                CardText("Before section"),
                Section([CardText("Inside section")]),
                CardText("After section"),
            ]
        )
        gchat_card = card_to_google_card(card)

        sections = gchat_card["card"]["sections"]
        assert len(sections) == 3
        assert sections[0]["widgets"][0]["textParagraph"]["text"] == "Before section"
        assert sections[1]["widgets"][0]["textParagraph"]["text"] == "Inside section"
        assert sections[2]["widgets"][0]["textParagraph"]["text"] == "After section"

    def test_converts_complete_card(self) -> None:
        card = Card(
            title="Order #1234",
            subtitle="Status update",
            children=[
                CardText("Your order has been shipped!"),
                Fields(
                    [
                        Field(label="Tracking", value="ABC123"),
                        Field(label="ETA", value="Dec 25"),
                    ]
                ),
                Actions([Button(id="track", label="Track Package", style="primary")]),
            ],
        )
        gchat_card = card_to_google_card(card)
        assert gchat_card["card"]["header"]["title"] == "Order #1234"
        assert gchat_card["card"]["header"]["subtitle"] == "Status update"
        sections = gchat_card["card"]["sections"]
        assert len(sections) == 1
        widgets = sections[0]["widgets"]
        assert len(widgets) == 4
        assert "textParagraph" in widgets[0]
        assert "decoratedText" in widgets[1]
        assert "decoratedText" in widgets[2]
        assert "buttonList" in widgets[3]

    def test_creates_placeholder_section_for_empty_cards(self) -> None:
        card = Card()
        gchat_card = card_to_google_card(card)
        sections = gchat_card["card"]["sections"]
        assert len(sections) == 1
        assert len(sections[0]["widgets"]) == 1


class TestCardToFallbackText:
    def test_generates_fallback_text_for_card(self) -> None:
        card = Card(
            title="Order Update",
            subtitle="Status changed",
            children=[
                CardText("Your order is ready"),
                Fields(
                    [
                        Field(label="Order ID", value="#1234"),
                        Field(label="Status", value="Ready"),
                    ]
                ),
                Actions(
                    [
                        Button(id="pickup", label="Schedule Pickup"),
                        Button(id="delay", label="Delay"),
                    ]
                ),
            ],
        )
        text = card_to_fallback_text(card)

        assert "*Order Update*" in text
        assert "Status changed" in text
        assert "Your order is ready" in text
        assert "Order ID: #1234" in text
        assert "Status: Ready" in text
        # Actions are excluded from fallback text
        assert "[Schedule Pickup]" not in text
        assert "[Delay]" not in text

    def test_handles_card_with_only_title(self) -> None:
        card = Card(title="Simple Card")
        assert card_to_fallback_text(card) == "*Simple Card*"


class TestMarkdownBoldConversion:
    def test_converts_bold_in_card_text(self) -> None:
        card = Card(children=[CardText("The **domain** is example.com")])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert widgets[0]["textParagraph"]["text"] == "The *domain* is example.com"

    def test_converts_multiple_bold_segments(self) -> None:
        card = Card(children=[CardText("**Project**: my-app, **Status**: active")])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert widgets[0]["textParagraph"]["text"] == "*Project*: my-app, *Status*: active"

    def test_preserves_existing_single_asterisk_formatting(self) -> None:
        card = Card(children=[CardText("Already *bold* in GChat format")])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert widgets[0]["textParagraph"]["text"] == "Already *bold* in GChat format"

    def test_converts_bold_in_field_values(self) -> None:
        card = Card(children=[Fields([Field(label="Status", value="**Active**")])])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        decorated = widgets[0]["decoratedText"]
        assert decorated["text"] == "*Active*"
        assert "**" not in decorated["text"]

    def test_converts_bold_in_field_labels(self) -> None:
        card = Card(children=[Fields([Field(label="**Important**", value="value")])])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert widgets[0]["decoratedText"]["topLabel"] == "*Important*"

    def test_handles_text_with_no_markdown(self) -> None:
        card = Card(children=[CardText("Plain text")])
        gchat_card = card_to_google_card(card)
        widgets = gchat_card["card"]["sections"][0]["widgets"]
        assert widgets[0]["textParagraph"]["text"] == "Plain text"


class TestCardLinkConversion:
    def test_converts_card_link_to_html_anchor(self) -> None:
        card = Card(children=[CardLink(url="https://example.com", label="Click here")])
        google_card = card_to_google_card(card)
        assert len(google_card["card"]["sections"]) == 1
        widgets = google_card["card"]["sections"][0]["widgets"]
        assert len(widgets) == 1
        assert widgets[0] == {
            "textParagraph": {"text": '<a href="https://example.com">Click here</a>'}
        }
