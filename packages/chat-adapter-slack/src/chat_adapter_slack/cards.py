"""Slack Block Kit converter for cross-platform cards.

Python port of upstream ``packages/adapter-slack/src/cards.ts``.

Converts ``CardElement`` dicts to Slack Block Kit blocks. Card elements are
plain ``dict[str, Any]`` following the chat-py convention (see ``CLAUDE.md``);
this module reads them with dict-first, attribute-fallback semantics so both
shapes work transparently.

@see https://api.slack.com/block-kit
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict

from chat_adapter_shared import (
    card_to_fallback_text as _shared_card_to_fallback_text,
)
from chat_adapter_shared import (
    create_emoji_converter,
    map_button_style,
)

if TYPE_CHECKING:
    from chat import CardChild, CardElement

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

SlackBlock = dict[str, Any]
"""A Slack Block Kit block — dict-based to mirror the upstream JSON surface."""


class _SlackTextObject(TypedDict, total=False):
    type: str
    text: str
    emoji: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_convert_emoji = create_emoji_converter("slack")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or fall back to attribute access."""

    if isinstance(obj, dict):
        value = obj.get(key, default)
        return value if value is not None else default
    value = getattr(obj, key, default)
    return value if value is not None else default


def _markdown_to_mrkdwn(text: str) -> str:
    """Convert standard Markdown ``**bold**`` to Slack mrkdwn ``*bold*``."""

    return _BOLD_PATTERN.sub(r"*\1*", text)


# ---------------------------------------------------------------------------
# Card → Block Kit
# ---------------------------------------------------------------------------


def card_to_block_kit(card: CardElement) -> list[SlackBlock]:
    """Convert a ``CardElement`` dict to a list of Slack Block Kit blocks."""

    blocks: list[SlackBlock] = []

    title = _get(card, "title")
    if title:
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": _convert_emoji(title),
                    "emoji": True,
                },
            }
        )

    subtitle = _get(card, "subtitle")
    if subtitle:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": _convert_emoji(subtitle),
                    },
                ],
            }
        )

    image_url = _get(card, "imageUrl") or _get(card, "image_url")
    if image_url:
        blocks.append(
            {
                "type": "image",
                "image_url": image_url,
                "alt_text": title or "Card image",
            }
        )

    state = {"used_native_table": False}
    for child in _get(card, "children", []) or []:
        blocks.extend(_convert_child_to_blocks(child, state))

    return blocks


def _convert_child_to_blocks(
    child: CardChild,
    state: dict[str, bool],
) -> list[SlackBlock]:
    """Convert a card child element to one or more Slack blocks."""

    child_type = _get(child, "type")
    if child_type == "text":
        return [convert_text_to_block(child)]
    if child_type == "image":
        return [_convert_image_to_block(child)]
    if child_type == "divider":
        return [_convert_divider_to_block(child)]
    if child_type == "actions":
        return [_convert_actions_to_block(child)]
    if child_type == "section":
        return _convert_section_to_blocks(child, state)
    if child_type == "fields":
        return [convert_fields_to_block(child)]
    if child_type == "link":
        return [_convert_link_to_block(child)]
    if child_type == "table":
        return _convert_table_to_blocks(child, state)

    # Unknown — fall back to shared text representation.
    text = _shared_card_to_fallback_text(
        {"children": [child]},
        {"platform": "slack"},
    )
    if text:
        return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    return []


# ---------------------------------------------------------------------------
# Element converters
# ---------------------------------------------------------------------------


def convert_text_to_block(element: Any) -> SlackBlock:
    """Convert a ``TextElement`` dict to a section (or context) block."""

    text = _markdown_to_mrkdwn(_convert_emoji(_get(element, "content", "")))
    style = _get(element, "style")

    if style == "bold":
        formatted_text = f"*{text}*"
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": formatted_text},
        }

    if style == "muted":
        # Slack has no muted style — use a context block instead.
        return {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": text}],
        }

    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text},
    }


def _convert_link_to_block(element: Any) -> SlackBlock:
    url = _get(element, "url", "")
    label = _get(element, "label", "")
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"<{url}|{_convert_emoji(label)}>",
        },
    }


def _convert_image_to_block(element: Any) -> SlackBlock:
    return {
        "type": "image",
        "image_url": _get(element, "url", ""),
        "alt_text": _get(element, "alt") or "Image",
    }


def _convert_divider_to_block(_element: Any) -> SlackBlock:
    return {"type": "divider"}


def _convert_actions_to_block(element: Any) -> SlackBlock:
    elements: list[dict[str, Any]] = []
    for child in _get(element, "children", []) or []:
        child_type = _get(child, "type")
        if child_type == "link-button":
            elements.append(_convert_link_button_to_element(child))
        elif child_type == "select":
            elements.append(_convert_select_to_element(child))
        elif child_type == "radio_select":
            elements.append(_convert_radio_select_to_element(child))
        else:
            elements.append(_convert_button_to_element(child))
    return {"type": "actions", "elements": elements}


def _convert_button_to_element(button: Any) -> dict[str, Any]:
    label = _get(button, "label", "")
    element: dict[str, Any] = {
        "type": "button",
        "text": {
            "type": "plain_text",
            "text": _convert_emoji(label),
            "emoji": True,
        },
        "action_id": _get(button, "id", ""),
    }

    value = _get(button, "value")
    if value:
        element["value"] = value

    style = map_button_style(_get(button, "style"), "slack")
    if style:
        element["style"] = style

    return element


def _convert_link_button_to_element(button: Any) -> dict[str, Any]:
    label = _get(button, "label", "")
    url = _get(button, "url", "")
    element: dict[str, Any] = {
        "type": "button",
        "text": {
            "type": "plain_text",
            "text": _convert_emoji(label),
            "emoji": True,
        },
        # Match upstream: derive action_id from url so link buttons are
        # distinguishable on the callback side.
        "action_id": f"link-{url[:200]}",
        "url": url,
    }

    style = map_button_style(_get(button, "style"), "slack")
    if style:
        element["style"] = style

    return element


def _build_options(
    options: list[Any],
    *,
    text_type: str,
    converter: Callable[[str], str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for opt in options:
        label = _get(opt, "label", "")
        option: dict[str, Any] = {
            "text": {"type": text_type, "text": converter(label)},
            "value": _get(opt, "value", ""),
        }
        description = _get(opt, "description")
        if description:
            option["description"] = {
                "type": text_type,
                "text": converter(description),
            }
        out.append(option)
    return out


def _convert_select_to_element(select: Any) -> dict[str, Any]:
    options = _build_options(
        _get(select, "options", []) or [],
        text_type="plain_text",
        converter=_convert_emoji,
    )

    element: dict[str, Any] = {
        "type": "static_select",
        "action_id": _get(select, "id", ""),
        "options": options,
    }

    placeholder = _get(select, "placeholder")
    if placeholder:
        element["placeholder"] = {
            "type": "plain_text",
            "text": _convert_emoji(placeholder),
        }

    initial_value = _get(select, "initialOption") or _get(select, "initial_option")
    if initial_value:
        for opt in options:
            if opt["value"] == initial_value:
                element["initial_option"] = opt
                break

    return element


def _convert_radio_select_to_element(radio: Any) -> dict[str, Any]:
    limited_options = (_get(radio, "options", []) or [])[:10]
    options = _build_options(
        limited_options,
        text_type="mrkdwn",
        converter=_convert_emoji,
    )

    element: dict[str, Any] = {
        "type": "radio_buttons",
        "action_id": _get(radio, "id", ""),
        "options": options,
    }

    initial_value = _get(radio, "initialOption") or _get(radio, "initial_option")
    if initial_value:
        for opt in options:
            if opt["value"] == initial_value:
                element["initial_option"] = opt
                break

    return element


def _convert_table_to_blocks(
    element: Any,
    state: dict[str, bool],
) -> list[SlackBlock]:
    """Convert a table element to a native Slack table block (or ASCII fallback)."""

    from chat import table_element_to_ascii

    max_rows = 100
    max_cols = 20

    rows = _get(element, "rows", []) or []
    headers = _get(element, "headers", []) or []

    if state["used_native_table"] or len(rows) > max_rows or len(headers) > max_cols:
        ascii_table = table_element_to_ascii(headers, rows)
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```\n{ascii_table}\n```",
                },
            }
        ]

    state["used_native_table"] = True

    def _cell(value: str) -> dict[str, str]:
        converted = _convert_emoji(value)
        return {"type": "raw_text", "text": converted if converted else " "}

    header_row = [_cell(h) for h in headers]
    data_rows = [[_cell(c) for c in row] for row in rows]

    return [
        {
            "type": "table",
            "rows": [header_row, *data_rows],
        }
    ]


def _convert_section_to_blocks(
    element: Any,
    state: dict[str, bool],
) -> list[SlackBlock]:
    """Flatten a section's children into the surrounding block stream."""

    blocks: list[SlackBlock] = []
    for child in _get(element, "children", []) or []:
        blocks.extend(_convert_child_to_blocks(child, state))
    return blocks


def convert_fields_to_block(element: Any) -> SlackBlock:
    """Convert a ``FieldsElement`` to a section with a ``fields`` list."""

    fields: list[dict[str, str]] = []
    for field in _get(element, "children", []) or []:
        label = _markdown_to_mrkdwn(_convert_emoji(_get(field, "label", "")))
        value = _markdown_to_mrkdwn(_convert_emoji(_get(field, "value", "")))
        fields.append(
            {
                "type": "mrkdwn",
                "text": f"*{label}*\n{value}",
            }
        )
    return {"type": "section", "fields": fields}


# ---------------------------------------------------------------------------
# Fallback text
# ---------------------------------------------------------------------------


def card_to_fallback_text(card: CardElement) -> str:
    """Generate fallback text for a card — delegates to the shared helper."""

    return _shared_card_to_fallback_text(
        card,
        {"bold_format": "*", "line_break": "\n", "platform": "slack"},
    )


__all__ = [
    "SlackBlock",
    "card_to_block_kit",
    "card_to_fallback_text",
    "convert_fields_to_block",
    "convert_text_to_block",
]
