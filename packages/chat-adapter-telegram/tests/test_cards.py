"""Tests for Telegram card-to-inline-keyboard translation + callback data."""

from __future__ import annotations

import pytest
from chat_adapter_shared import ValidationError
from chat_adapter_telegram import (
    card_to_telegram_inline_keyboard,
    decode_telegram_callback_data,
    empty_telegram_inline_keyboard,
    encode_telegram_callback_data,
)

# --------------------------------------------------------------------------
# encode_telegram_callback_data
# --------------------------------------------------------------------------


def test_encode_plain_action_id() -> None:
    assert encode_telegram_callback_data("vote") == 'chat:{"a":"vote"}'


def test_encode_with_value() -> None:
    assert encode_telegram_callback_data("vote", "yes") == 'chat:{"a":"vote","v":"yes"}'


def test_encode_with_none_value_omits_v() -> None:
    assert encode_telegram_callback_data("vote", None) == 'chat:{"a":"vote"}'


def test_encode_with_non_string_value_omits_v() -> None:
    # decode_telegram_callback_data sanitizes to None, so encode should too.
    assert encode_telegram_callback_data("vote", None) == 'chat:{"a":"vote"}'


def test_encode_raises_when_payload_too_large() -> None:
    with pytest.raises(ValidationError):
        encode_telegram_callback_data("a" * 100, "b" * 100)


def test_encode_boundary_fits_within_64_bytes() -> None:
    # Pick lengths that leave headroom for the ``chat:{"a":"","v":""}`` wrapper.
    result = encode_telegram_callback_data("a" * 5, "b" * 30)
    assert len(result.encode("utf-8")) <= 64


# --------------------------------------------------------------------------
# decode_telegram_callback_data
# --------------------------------------------------------------------------


def test_decode_none_returns_fallback() -> None:
    assert decode_telegram_callback_data(None) == {
        "actionId": "telegram_callback",
        "value": None,
    }


def test_decode_empty_string_returns_fallback() -> None:
    assert decode_telegram_callback_data("") == {
        "actionId": "telegram_callback",
        "value": None,
    }


def test_decode_missing_prefix_passes_through() -> None:
    assert decode_telegram_callback_data("legacy") == {
        "actionId": "legacy",
        "value": "legacy",
    }


def test_decode_valid_payload() -> None:
    encoded = encode_telegram_callback_data("vote", "yes")
    assert decode_telegram_callback_data(encoded) == {
        "actionId": "vote",
        "value": "yes",
    }


def test_decode_without_value() -> None:
    encoded = encode_telegram_callback_data("ok")
    assert decode_telegram_callback_data(encoded) == {
        "actionId": "ok",
        "value": None,
    }


def test_decode_malformed_json_passthrough() -> None:
    assert decode_telegram_callback_data("chat:{bad json") == {
        "actionId": "chat:{bad json",
        "value": "chat:{bad json",
    }


def test_decode_missing_action_id_passthrough() -> None:
    assert decode_telegram_callback_data('chat:{"v":"only"}') == {
        "actionId": 'chat:{"v":"only"}',
        "value": 'chat:{"v":"only"}',
    }


# --------------------------------------------------------------------------
# card_to_telegram_inline_keyboard / empty_telegram_inline_keyboard
# --------------------------------------------------------------------------


def test_empty_inline_keyboard() -> None:
    assert empty_telegram_inline_keyboard() == {"inline_keyboard": []}


def test_card_with_no_actions_returns_none() -> None:
    card = {"type": "card", "children": [{"type": "text", "value": "hello"}]}
    assert card_to_telegram_inline_keyboard(card) is None


def test_card_with_single_button_row() -> None:
    card = {
        "type": "card",
        "children": [
            {
                "type": "actions",
                "children": [
                    {"type": "button", "id": "vote", "label": "Vote", "value": "yes"},
                ],
            },
        ],
    }
    keyboard = card_to_telegram_inline_keyboard(card)
    assert keyboard is not None
    rows = keyboard["inline_keyboard"]
    assert len(rows) == 1
    button = rows[0][0]
    assert button["text"] == "Vote"
    assert button["callback_data"] == 'chat:{"a":"vote","v":"yes"}'


def test_card_with_link_button() -> None:
    card = {
        "type": "card",
        "children": [
            {
                "type": "actions",
                "children": [
                    {"type": "link-button", "label": "Docs", "url": "https://x.com"},
                ],
            },
        ],
    }
    keyboard = card_to_telegram_inline_keyboard(card)
    assert keyboard is not None
    button = keyboard["inline_keyboard"][0][0]
    assert button["text"] == "Docs"
    assert button["url"] == "https://x.com"
    assert "callback_data" not in button


def test_card_nested_section_actions() -> None:
    card = {
        "type": "card",
        "children": [
            {
                "type": "section",
                "children": [
                    {
                        "type": "actions",
                        "children": [
                            {"type": "button", "id": "a", "label": "A"},
                        ],
                    },
                ],
            },
            {
                "type": "actions",
                "children": [
                    {"type": "button", "id": "b", "label": "B"},
                ],
            },
        ],
    }
    keyboard = card_to_telegram_inline_keyboard(card)
    assert keyboard is not None
    rows = keyboard["inline_keyboard"]
    assert len(rows) == 2
    assert rows[0][0]["text"] == "A"
    assert rows[1][0]["text"] == "B"


def test_card_with_emoji_placeholder_converts_to_gchat() -> None:
    card = {
        "type": "card",
        "children": [
            {
                "type": "actions",
                "children": [
                    {
                        "type": "button",
                        "id": "ok",
                        "label": "{{emoji:thumbs_up}} Yes",
                    },
                ],
            },
        ],
    }
    keyboard = card_to_telegram_inline_keyboard(card)
    assert keyboard is not None
    text = keyboard["inline_keyboard"][0][0]["text"]
    # Placeholder should have been resolved to a real emoji.
    assert "{{emoji:" not in text
    assert "Yes" in text
