"""Tests for :func:`card_to_adaptive_card` / :func:`cards.card_to_fallback_text`.

Mirrors upstream ``packages/adapter-teams/src/cards.test.ts``.
"""

from __future__ import annotations

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
)
from chat.modals import RadioSelect, Select, SelectOption
from chat_adapter_teams.cards import card_to_adaptive_card, card_to_fallback_text


class TestCardToAdaptiveCard:
    def test_creates_valid_adaptive_card_structure(self) -> None:
        card = Card(title="Test")
        adaptive = card_to_adaptive_card(card)

        assert adaptive["type"] == "AdaptiveCard"
        assert adaptive["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
        assert adaptive["version"] == "1.4"
        assert isinstance(adaptive["body"], list)

    def test_converts_card_with_title(self) -> None:
        card = Card(title="Welcome Message")
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        block = adaptive["body"][0]
        assert block["type"] == "TextBlock"
        assert block["text"] == "Welcome Message"
        assert block["weight"] == "Bolder"
        assert block["size"] == "Large"
        assert block["wrap"] is True

    def test_converts_card_with_title_and_subtitle(self) -> None:
        card = Card(title="Order Update", subtitle="Your package is on its way")
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 2
        subtitle = adaptive["body"][1]
        assert subtitle["type"] == "TextBlock"
        assert subtitle["text"] == "Your package is on its way"
        assert subtitle["isSubtle"] is True
        assert subtitle["wrap"] is True

    def test_converts_card_with_header_image(self) -> None:
        card = Card(title="Product", image_url="https://example.com/product.png")
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 2
        img = adaptive["body"][1]
        assert img["type"] == "Image"
        assert img["url"] == "https://example.com/product.png"
        assert img["size"] == "Stretch"

    def test_converts_text_elements(self) -> None:
        card = Card(
            children=[
                CardText("Regular text"),
                CardText("Bold text", style="bold"),
                CardText("Muted text", style="muted"),
            ]
        )
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 3
        assert adaptive["body"][0] == {
            "type": "TextBlock",
            "text": "Regular text",
            "wrap": True,
        }
        assert adaptive["body"][1] == {
            "type": "TextBlock",
            "text": "Bold text",
            "wrap": True,
            "weight": "Bolder",
        }
        assert adaptive["body"][2] == {
            "type": "TextBlock",
            "text": "Muted text",
            "wrap": True,
            "isSubtle": True,
        }

    def test_converts_image_elements(self) -> None:
        card = Card(children=[Image(url="https://example.com/img.png", alt="My image")])
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        assert adaptive["body"][0] == {
            "type": "Image",
            "url": "https://example.com/img.png",
            "altText": "My image",
            "size": "Auto",
        }

    def test_converts_divider_elements(self) -> None:
        card = Card(children=[Divider()])
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        assert adaptive["body"][0] == {
            "type": "Container",
            "separator": True,
            "items": [],
        }

    def test_converts_buttons_to_card_level_actions(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Button(id="approve", label="Approve", style="primary"),
                        Button(id="reject", label="Reject", style="danger", value="data-123"),
                        Button(id="skip", label="Skip"),
                    ]
                )
            ]
        )
        adaptive = card_to_adaptive_card(card)

        assert adaptive["body"] == []
        assert len(adaptive["actions"]) == 3

        first = adaptive["actions"][0]
        assert first["type"] == "Action.Submit"
        assert first["title"] == "Approve"
        assert first["data"] == {"actionId": "approve", "value": None}
        assert first["style"] == "positive"

        second = adaptive["actions"][1]
        assert second["type"] == "Action.Submit"
        assert second["title"] == "Reject"
        assert second["data"] == {"actionId": "reject", "value": "data-123"}
        assert second["style"] == "destructive"

        third = adaptive["actions"][2]
        assert third["type"] == "Action.Submit"
        assert third["title"] == "Skip"
        assert third["data"] == {"actionId": "skip", "value": None}
        assert "style" not in third

    def test_converts_link_buttons_to_action_openurl(self) -> None:
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
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["actions"]) == 1
        action = adaptive["actions"][0]
        assert action["type"] == "Action.OpenUrl"
        assert action["title"] == "View Docs"
        assert action["url"] == "https://example.com/docs"
        assert action["style"] == "positive"

    def test_converts_fields_to_factset(self) -> None:
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
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        block = adaptive["body"][0]
        assert block["type"] == "FactSet"
        assert block["facts"] == [
            {"title": "Status", "value": "Active"},
            {"title": "Priority", "value": "High"},
        ]

    def test_wraps_section_children_in_container(self) -> None:
        card = Card(children=[Section([CardText("Inside section")])])
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        container = adaptive["body"][0]
        assert container["type"] == "Container"
        assert len(container["items"]) == 1

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
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 4
        assert adaptive["body"][0]["type"] == "TextBlock"
        assert adaptive["body"][1]["type"] == "TextBlock"
        assert adaptive["body"][2]["type"] == "TextBlock"
        assert adaptive["body"][3]["type"] == "FactSet"

        assert len(adaptive["actions"]) == 1
        assert adaptive["actions"][0]["title"] == "Track Package"


class TestCardToFallbackText:
    def test_generates_fallback_text(self) -> None:
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

        assert "**Order Update**" in text
        assert "Status changed" in text
        assert "Your order is ready" in text
        assert "Order ID: #1234" in text
        assert "Status: Ready" in text
        # Actions excluded from fallback
        assert "[Schedule Pickup]" not in text
        assert "[Delay]" not in text

    def test_handles_card_with_only_title(self) -> None:
        card = Card(title="Simple Card")
        assert card_to_fallback_text(card) == "**Simple Card**"


class TestCardToAdaptiveCardModalButtons:
    def test_adds_msteams_task_fetch_hint(self) -> None:
        card = Card(
            children=[Actions([Button(id="open-dialog", label="Open", action_type="modal")])]
        )
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["actions"]) == 1
        action = adaptive["actions"][0]
        assert action["type"] == "Action.Submit"
        assert action["title"] == "Open"
        assert action["data"]["actionId"] == "open-dialog"
        assert action["data"]["msteams"] == {"type": "task/fetch"}


class TestCardToAdaptiveCardChoiceSets:
    def test_converts_select_to_compact_choiceset(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="color",
                            label="Pick a color",
                            options=[
                                SelectOption(label="Red", value="red"),
                                SelectOption(label="Blue", value="blue"),
                            ],
                            placeholder="Choose...",
                        )
                    ]
                )
            ]
        )
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        block = adaptive["body"][0]
        assert block["type"] == "Input.ChoiceSet"
        assert block["id"] == "color"
        assert block["label"] == "Pick a color"
        assert block["style"] == "compact"
        assert block["isRequired"] is True
        assert block["placeholder"] == "Choose..."
        assert len(block["choices"]) == 2
        assert block["choices"][0] == {"title": "Red", "value": "red"}

        # Auto-injects submit button
        assert len(adaptive["actions"]) == 1
        assert adaptive["actions"][0] == {
            "type": "Action.Submit",
            "title": "Submit",
            "data": {"actionId": "__auto_submit"},
        }

    def test_converts_radio_select_to_expanded_choiceset(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        RadioSelect(
                            id="plan",
                            label="Choose Plan",
                            options=[
                                SelectOption(label="Free", value="free"),
                                SelectOption(label="Pro", value="pro"),
                            ],
                        )
                    ]
                )
            ]
        )
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        block = adaptive["body"][0]
        assert block["type"] == "Input.ChoiceSet"
        assert block["id"] == "plan"
        assert block["label"] == "Choose Plan"
        assert block["style"] == "expanded"
        assert block["isRequired"] is True

        # Auto-injects submit button
        assert len(adaptive["actions"]) == 1
        assert adaptive["actions"][0]["data"] == {"actionId": "__auto_submit"}

    def test_does_not_auto_inject_when_buttons_present(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="color",
                            label="Color",
                            options=[SelectOption(label="Red", value="red")],
                        ),
                        Button(id="submit", label="Submit", style="primary"),
                    ]
                )
            ]
        )
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        assert adaptive["body"][0]["type"] == "Input.ChoiceSet"
        assert adaptive["body"][0]["id"] == "color"
        assert len(adaptive["actions"]) == 1
        assert adaptive["actions"][0]["type"] == "Action.Submit"
        assert adaptive["actions"][0]["title"] == "Submit"


class TestCardToAdaptiveCardCardLink:
    def test_converts_card_link_to_textblock_with_markdown(self) -> None:
        card = Card(children=[CardLink(url="https://example.com", label="Click here")])
        adaptive = card_to_adaptive_card(card)

        assert len(adaptive["body"]) == 1
        assert adaptive["body"][0] == {
            "type": "TextBlock",
            "text": "[Click here](https://example.com)",
            "wrap": True,
        }
