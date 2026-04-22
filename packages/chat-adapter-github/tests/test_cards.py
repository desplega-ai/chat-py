"""Unit tests for :mod:`chat_adapter_github.cards`.

Mirrors upstream ``packages/adapter-github/src/cards.test.ts``.
"""

from __future__ import annotations

from chat import Card, CardLink
from chat_adapter_github.cards import card_to_github_markdown, card_to_plain_text


class TestCardToGitHubMarkdown:
    def test_renders_simple_card_with_title(self) -> None:
        card = {"type": "card", "title": "Hello World", "children": []}
        assert card_to_github_markdown(card) == "**Hello World**"

    def test_renders_title_and_subtitle(self) -> None:
        card = {
            "type": "card",
            "title": "Order #1234",
            "subtitle": "Status update",
            "children": [],
        }
        assert card_to_github_markdown(card) == "**Order #1234**\nStatus update"

    def test_renders_card_with_text_content(self) -> None:
        card = {
            "type": "card",
            "title": "Notification",
            "children": [
                {"type": "text", "content": "Your order has been shipped!"},
            ],
        }
        assert card_to_github_markdown(card) == "**Notification**\n\nYour order has been shipped!"

    def test_renders_card_with_fields(self) -> None:
        card = {
            "type": "card",
            "title": "Order Details",
            "children": [
                {
                    "type": "fields",
                    "children": [
                        {"type": "field", "label": "Order ID", "value": "12345"},
                        {"type": "field", "label": "Status", "value": "Shipped"},
                    ],
                },
            ],
        }
        result = card_to_github_markdown(card)
        assert "**Order ID:** 12345" in result
        assert "**Status:** Shipped" in result

    def test_renders_card_with_link_buttons(self) -> None:
        card = {
            "type": "card",
            "title": "Actions",
            "children": [
                {
                    "type": "actions",
                    "children": [
                        {
                            "type": "link-button",
                            "url": "https://example.com/track",
                            "label": "Track Order",
                        },
                        {
                            "type": "link-button",
                            "url": "https://example.com/help",
                            "label": "Get Help",
                        },
                    ],
                },
            ],
        }
        result = card_to_github_markdown(card)
        assert "[Track Order](https://example.com/track)" in result
        assert "[Get Help](https://example.com/help)" in result

    def test_renders_card_with_action_buttons_as_bold_text(self) -> None:
        card = {
            "type": "card",
            "title": "Approve?",
            "children": [
                {
                    "type": "actions",
                    "children": [
                        {
                            "type": "button",
                            "id": "approve",
                            "label": "Approve",
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "id": "reject",
                            "label": "Reject",
                            "style": "danger",
                        },
                    ],
                },
            ],
        }
        result = card_to_github_markdown(card)
        assert "**[Approve]**" in result
        assert "**[Reject]**" in result

    def test_renders_card_with_image(self) -> None:
        card = {
            "type": "card",
            "title": "Image Card",
            "children": [
                {
                    "type": "image",
                    "url": "https://example.com/image.png",
                    "alt": "Example image",
                },
            ],
        }
        result = card_to_github_markdown(card)
        assert "![Example image](https://example.com/image.png)" in result

    def test_renders_card_with_divider(self) -> None:
        card = {
            "type": "card",
            "children": [
                {"type": "text", "content": "Before"},
                {"type": "divider"},
                {"type": "text", "content": "After"},
            ],
        }
        result = card_to_github_markdown(card)
        assert "---" in result

    def test_renders_card_with_section(self) -> None:
        card = {
            "type": "card",
            "children": [
                {
                    "type": "section",
                    "children": [{"type": "text", "content": "Section content"}],
                },
            ],
        }
        result = card_to_github_markdown(card)
        assert "Section content" in result

    def test_handles_text_with_different_styles(self) -> None:
        card = {
            "type": "card",
            "children": [
                {"type": "text", "content": "Normal text"},
                {"type": "text", "content": "Bold text", "style": "bold"},
                {"type": "text", "content": "Muted text", "style": "muted"},
            ],
        }
        result = card_to_github_markdown(card)
        assert "Normal text" in result
        assert "**Bold text**" in result
        assert "_Muted text_" in result


class TestCardToPlainText:
    def test_generates_plain_text_from_card(self) -> None:
        card = {
            "type": "card",
            "title": "Hello",
            "subtitle": "World",
            "children": [
                {"type": "text", "content": "Some content"},
                {
                    "type": "fields",
                    "children": [{"type": "field", "label": "Key", "value": "Value"}],
                },
            ],
        }
        result = card_to_plain_text(card)
        assert "Hello" in result
        assert "World" in result
        assert "Some content" in result
        assert "Key: Value" in result


class TestCardWithCardLink:
    def test_renders_card_link_as_markdown_link(self) -> None:
        card = Card(children=[CardLink(url="https://example.com", label="Click here")])
        assert card_to_github_markdown(card) == "[Click here](https://example.com)"
