"""Tests for WhatsApp card translation + callback data helpers."""

from __future__ import annotations

from chat_adapter_whatsapp import (
    card_to_plain_text,
    card_to_whatsapp,
    card_to_whatsapp_text,
    decode_whatsapp_callback_data,
    encode_whatsapp_callback_data,
)

# ---------------------------------------------------------------------------
# encode_whatsapp_callback_data
# ---------------------------------------------------------------------------


def test_encode_action_id_only() -> None:
    assert encode_whatsapp_callback_data("my_action") == 'chat:{"a":"my_action"}'


def test_encode_action_id_and_value() -> None:
    assert encode_whatsapp_callback_data("my_action", "some_value") == (
        'chat:{"a":"my_action","v":"some_value"}'
    )


def test_encode_with_none_value_omits_v() -> None:
    assert encode_whatsapp_callback_data("vote", None) == 'chat:{"a":"vote"}'


# ---------------------------------------------------------------------------
# decode_whatsapp_callback_data
# ---------------------------------------------------------------------------


def test_decode_roundtrip_with_value() -> None:
    encoded = encode_whatsapp_callback_data("my_action", "some_value")
    decoded = decode_whatsapp_callback_data(encoded)
    assert decoded == {"actionId": "my_action", "value": "some_value"}


def test_decode_roundtrip_without_value() -> None:
    encoded = encode_whatsapp_callback_data("my_action")
    decoded = decode_whatsapp_callback_data(encoded)
    assert decoded == {"actionId": "my_action", "value": None}


def test_decode_passthrough_for_non_prefixed() -> None:
    assert decode_whatsapp_callback_data("raw_id") == {
        "actionId": "raw_id",
        "value": "raw_id",
    }


def test_decode_none_returns_fallback() -> None:
    assert decode_whatsapp_callback_data(None) == {
        "actionId": "whatsapp_callback",
        "value": None,
    }


def test_decode_empty_string_returns_fallback() -> None:
    assert decode_whatsapp_callback_data("") == {
        "actionId": "whatsapp_callback",
        "value": None,
    }


def test_decode_malformed_json_passthrough() -> None:
    assert decode_whatsapp_callback_data("chat:not-json") == {
        "actionId": "chat:not-json",
        "value": "chat:not-json",
    }


def test_decode_missing_action_id_passthrough() -> None:
    assert decode_whatsapp_callback_data('chat:{"v":"only"}') == {
        "actionId": 'chat:{"v":"only"}',
        "value": 'chat:{"v":"only"}',
    }


# ---------------------------------------------------------------------------
# card_to_whatsapp_text
# ---------------------------------------------------------------------------


def test_text_simple_card_with_title() -> None:
    card = {"type": "card", "title": "Hello World", "children": []}
    assert card_to_whatsapp_text(card) == "*Hello World*"


def test_text_card_with_title_and_subtitle() -> None:
    card = {
        "type": "card",
        "title": "Order #1234",
        "subtitle": "Status update",
        "children": [],
    }
    assert card_to_whatsapp_text(card) == "*Order #1234*\nStatus update"


def test_text_card_with_text_content() -> None:
    card = {
        "type": "card",
        "title": "Notification",
        "children": [
            {"type": "text", "content": "Your order has been shipped!"},
        ],
    }
    result = card_to_whatsapp_text(card)
    assert result == "*Notification*\n\nYour order has been shipped!"


def test_text_card_with_fields_uses_whatsapp_bold() -> None:
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
    result = card_to_whatsapp_text(card)
    assert "*Order ID:* 12345" in result
    assert "*Status:* Shipped" in result


def test_text_card_with_link_buttons_renders_urls() -> None:
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
    result = card_to_whatsapp_text(card)
    assert "Track Order: https://example.com/track" in result
    assert "Get Help: https://example.com/help" in result


def test_text_card_with_button_actions_renders_brackets() -> None:
    card = {
        "type": "card",
        "title": "Approve?",
        "children": [
            {
                "type": "actions",
                "children": [
                    {"type": "button", "id": "approve", "label": "Approve"},
                    {"type": "button", "id": "reject", "label": "Reject"},
                ],
            },
        ],
    }
    result = card_to_whatsapp_text(card)
    assert "[Approve]" in result
    assert "[Reject]" in result


def test_text_card_with_image() -> None:
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
    result = card_to_whatsapp_text(card)
    assert "Example image: https://example.com/image.png" in result


def test_text_card_with_divider() -> None:
    card = {
        "type": "card",
        "children": [
            {"type": "text", "content": "Before"},
            {"type": "divider"},
            {"type": "text", "content": "After"},
        ],
    }
    result = card_to_whatsapp_text(card)
    assert "---" in result


def test_text_card_with_section_unwraps_children() -> None:
    card = {
        "type": "card",
        "children": [
            {
                "type": "section",
                "children": [{"type": "text", "content": "Section content"}],
            },
        ],
    }
    result = card_to_whatsapp_text(card)
    assert "Section content" in result


def test_text_card_handles_text_styles() -> None:
    card = {
        "type": "card",
        "children": [
            {"type": "text", "content": "Normal text"},
            {"type": "text", "content": "Bold text", "style": "bold"},
            {"type": "text", "content": "Muted text", "style": "muted"},
        ],
    }
    result = card_to_whatsapp_text(card)
    assert "Normal text" in result
    assert "*Bold text*" in result
    assert "_Muted text_" in result


# ---------------------------------------------------------------------------
# card_to_whatsapp
# ---------------------------------------------------------------------------


def test_whatsapp_interactive_for_buttons() -> None:
    card = {
        "type": "card",
        "title": "Choose an action",
        "children": [
            {"type": "text", "content": "What would you like to do?"},
            {
                "type": "actions",
                "children": [
                    {"type": "button", "id": "btn_yes", "label": "Yes"},
                    {"type": "button", "id": "btn_no", "label": "No"},
                ],
            },
        ],
    }
    result = card_to_whatsapp(card)
    assert result["type"] == "interactive"
    assert result["interactive"]["type"] == "button"
    assert result["interactive"]["header"]["text"] == "Choose an action"
    buttons = result["interactive"]["action"]["buttons"]
    assert len(buttons) == 2
    assert buttons[0]["reply"]["id"] == encode_whatsapp_callback_data("btn_yes")
    assert buttons[1]["reply"]["id"] == encode_whatsapp_callback_data("btn_no")


def test_whatsapp_truncates_to_first_three_buttons() -> None:
    card = {
        "type": "card",
        "title": "Too many",
        "children": [
            {
                "type": "actions",
                "children": [
                    {"type": "button", "id": "btn_1", "label": "One"},
                    {"type": "button", "id": "btn_2", "label": "Two"},
                    {"type": "button", "id": "btn_3", "label": "Three"},
                    {"type": "button", "id": "btn_4", "label": "Four"},
                ],
            },
        ],
    }
    result = card_to_whatsapp(card)
    assert result["type"] == "interactive"
    assert len(result["interactive"]["action"]["buttons"]) == 3


def test_whatsapp_falls_back_to_text_for_link_only_actions() -> None:
    card = {
        "type": "card",
        "title": "Links only",
        "children": [
            {
                "type": "actions",
                "children": [
                    {"type": "link-button", "url": "https://x.com", "label": "Visit"},
                ],
            },
        ],
    }
    result = card_to_whatsapp(card)
    assert result["type"] == "text"


def test_whatsapp_falls_back_to_text_without_actions() -> None:
    card = {
        "type": "card",
        "title": "Info only",
        "children": [{"type": "text", "content": "Just some info"}],
    }
    result = card_to_whatsapp(card)
    assert result["type"] == "text"


def test_whatsapp_truncates_long_button_titles_to_20_chars() -> None:
    card = {
        "type": "card",
        "children": [
            {"type": "text", "content": "Choose"},
            {
                "type": "actions",
                "children": [
                    {
                        "type": "button",
                        "id": "btn_long",
                        "label": "This is a very long button title that exceeds the limit",
                    },
                ],
            },
        ],
    }
    result = card_to_whatsapp(card)
    assert result["type"] == "interactive"
    title = result["interactive"]["action"]["buttons"][0]["reply"]["title"]
    assert len(title) <= 20


def test_whatsapp_uses_default_body_when_card_has_no_text() -> None:
    card = {
        "type": "card",
        "children": [
            {
                "type": "actions",
                "children": [{"type": "button", "id": "ok", "label": "OK"}],
            },
        ],
    }
    result = card_to_whatsapp(card)
    assert result["type"] == "interactive"
    assert result["interactive"]["body"]["text"] == "Please choose an option"


def test_whatsapp_finds_actions_in_nested_section() -> None:
    card = {
        "type": "card",
        "title": "Nested",
        "children": [
            {
                "type": "section",
                "children": [
                    {
                        "type": "actions",
                        "children": [
                            {"type": "button", "id": "ok", "label": "OK"},
                        ],
                    },
                ],
            },
        ],
    }
    result = card_to_whatsapp(card)
    assert result["type"] == "interactive"


# ---------------------------------------------------------------------------
# card_to_plain_text
# ---------------------------------------------------------------------------


def test_plain_text_includes_title_subtitle_text_fields() -> None:
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


def test_plain_text_omits_actions() -> None:
    card = {
        "type": "card",
        "title": "Hello",
        "children": [
            {"type": "text", "content": "Body"},
            {
                "type": "actions",
                "children": [{"type": "button", "id": "ok", "label": "OK"}],
            },
        ],
    }
    result = card_to_plain_text(card)
    assert "Hello" in result
    assert "Body" in result
    # No action labels in plain text rendering.
    assert "OK" not in result
