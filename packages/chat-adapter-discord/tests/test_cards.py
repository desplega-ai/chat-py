"""Tests for the Discord cards translator.

Mirrors upstream ``packages/adapter-discord/src/cards.test.ts``.
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
    Section,
)
from chat import (
    Text as CardText,
)
from chat_adapter_discord.cards import (
    BUTTON_STYLE_DANGER,
    BUTTON_STYLE_LINK,
    BUTTON_STYLE_PRIMARY,
    BUTTON_STYLE_SECONDARY,
    card_to_discord_payload,
    card_to_fallback_text,
)


class TestCardToDiscordPayload:
    def test_converts_simple_card_with_title(self) -> None:
        card = Card(title="Welcome")
        payload = card_to_discord_payload(card)
        assert len(payload["embeds"]) == 1
        assert payload["embeds"][0]["title"] == "Welcome"
        assert payload["components"] == []

    def test_converts_card_with_title_and_subtitle(self) -> None:
        card = Card(title="Order Update", subtitle="Your order is on its way")
        payload = card_to_discord_payload(card)
        assert len(payload["embeds"]) == 1
        assert payload["embeds"][0]["title"] == "Order Update"
        assert "Your order is on its way" in payload["embeds"][0]["description"]

    def test_converts_card_with_header_image(self) -> None:
        card = Card(title="Product", image_url="https://example.com/product.png")
        payload = card_to_discord_payload(card)
        assert payload["embeds"][0]["image"] == {"url": "https://example.com/product.png"}

    def test_sets_default_color_to_blurple(self) -> None:
        card = Card(title="Test")
        payload = card_to_discord_payload(card)
        assert payload["embeds"][0]["color"] == 0x5865F2

    def test_converts_text_elements(self) -> None:
        card = Card(
            children=[
                CardText("Regular text"),
                CardText("Bold text", style="bold"),
                CardText("Muted text", style="muted"),
            ]
        )
        payload = card_to_discord_payload(card)
        description = payload["embeds"][0]["description"]
        assert "Regular text" in description
        assert "**Bold text**" in description
        assert "*Muted text*" in description

    def test_image_children_do_not_duplicate_embed(self) -> None:
        card = Card(children=[Image(url="https://example.com/img.png", alt="My image")])
        payload = card_to_discord_payload(card)
        assert len(payload["embeds"]) == 1

    def test_converts_divider_to_horizontal_line(self) -> None:
        card = Card(children=[CardText("Before"), Divider(), CardText("After")])
        payload = card_to_discord_payload(card)
        description = payload["embeds"][0]["description"]
        assert "Before" in description
        assert "───────────" in description
        assert "After" in description

    def test_converts_actions_with_buttons(self) -> None:
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
        payload = card_to_discord_payload(card)
        components = payload["components"]
        assert len(components) == 1
        assert components[0]["type"] == 1

        buttons = components[0]["components"]
        assert len(buttons) == 3
        assert buttons[0] == {
            "type": 2,
            "style": BUTTON_STYLE_PRIMARY,
            "label": "Approve",
            "custom_id": "approve",
        }
        assert buttons[1] == {
            "type": 2,
            "style": BUTTON_STYLE_DANGER,
            "label": "Reject",
            "custom_id": "reject",
        }
        assert buttons[2] == {
            "type": 2,
            "style": BUTTON_STYLE_SECONDARY,
            "label": "Skip",
            "custom_id": "skip",
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
        payload = card_to_discord_payload(card)
        buttons = payload["components"][0]["components"]
        assert buttons[0] == {
            "type": 2,
            "style": BUTTON_STYLE_DANGER,
            "label": "Cancelled",
            "custom_id": "cancel",
            "disabled": True,
        }
        assert buttons[1] == {
            "type": 2,
            "style": BUTTON_STYLE_SECONDARY,
            "label": "Retry",
            "custom_id": "retry",
        }

    def test_converts_link_buttons_with_link_style(self) -> None:
        card = Card(
            children=[Actions([LinkButton(url="https://example.com/docs", label="View Docs")])]
        )
        payload = card_to_discord_payload(card)
        buttons = payload["components"][0]["components"]
        assert buttons[0] == {
            "type": 2,
            "style": BUTTON_STYLE_LINK,
            "label": "View Docs",
            "url": "https://example.com/docs",
        }

    def test_converts_fields_to_embed_fields(self) -> None:
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
        payload = card_to_discord_payload(card)
        embed_fields = payload["embeds"][0]["fields"]
        assert len(embed_fields) == 2
        assert embed_fields[0] == {
            "name": "Status",
            "value": "Active",
            "inline": True,
        }
        assert embed_fields[1] == {
            "name": "Priority",
            "value": "High",
            "inline": True,
        }

    def test_flattens_section_children(self) -> None:
        card = Card(children=[Section([CardText("Inside section"), Divider()])])
        payload = card_to_discord_payload(card)
        description = payload["embeds"][0]["description"]
        assert "Inside section" in description
        assert "───────────" in description

    def test_converts_complete_card(self) -> None:
        card = Card(
            title="Order #1234",
            subtitle="Status update",
            children=[
                CardText("Your order has been shipped!"),
                Divider(),
                Fields(
                    [
                        Field(label="Tracking", value="ABC123"),
                        Field(label="ETA", value="Dec 25"),
                    ]
                ),
                Actions([Button(id="track", label="Track Package", style="primary")]),
            ],
        )
        payload = card_to_discord_payload(card)
        embed = payload["embeds"][0]
        assert embed["title"] == "Order #1234"
        assert "Status update" in embed["description"]
        assert "Your order has been shipped!" in embed["description"]
        assert "───────────" in embed["description"]
        assert len(embed["fields"]) == 2
        assert len(payload["components"]) == 1
        assert len(payload["components"][0]["components"]) == 1

    def test_handles_card_with_no_title_or_subtitle(self) -> None:
        card = Card(children=[CardText("Just content")])
        payload = card_to_discord_payload(card)
        assert "title" not in payload["embeds"][0]
        assert payload["embeds"][0]["description"] == "Just content"

    def test_combines_title_subtitle_and_content(self) -> None:
        card = Card(
            title="Title",
            subtitle="Subtitle",
            children=[CardText("Content")],
        )
        payload = card_to_discord_payload(card)
        description = payload["embeds"][0]["description"]
        assert payload["embeds"][0]["title"] == "Title"
        assert "Subtitle" in description
        assert "Content" in description


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
        assert "**Order Update**" in text
        assert "Status changed" in text
        assert "Your order is ready" in text
        assert "**Order ID**: #1234" in text
        assert "**Status**: Ready" in text
        # Actions are excluded from fallback.
        assert "[Schedule Pickup]" not in text
        assert "[Delay]" not in text

    def test_handles_card_with_only_title(self) -> None:
        text = card_to_fallback_text(Card(title="Simple Card"))
        assert text == "**Simple Card**"

    def test_handles_card_with_subtitle_only(self) -> None:
        text = card_to_fallback_text(Card(subtitle="Just a subtitle"))
        assert text == "Just a subtitle"

    def test_handles_empty_card(self) -> None:
        assert card_to_fallback_text(Card()) == ""

    def test_handles_card_with_multiple_fields(self) -> None:
        card = Card(
            children=[
                Fields(
                    [
                        Field(label="A", value="1"),
                        Field(label="B", value="2"),
                        Field(label="C", value="3"),
                    ]
                )
            ]
        )
        text = card_to_fallback_text(card)
        assert "**A**: 1" in text
        assert "**B**: 2" in text
        assert "**C**: 3" in text


class TestCardToDiscordPayloadWithCardLink:
    def test_appends_markdown_link_to_description(self) -> None:
        card = Card(children=[CardLink(url="https://example.com", label="Click here")])
        payload = card_to_discord_payload(card)
        assert len(payload["embeds"]) == 1
        assert payload["embeds"][0]["description"] == "[Click here](https://example.com)"
