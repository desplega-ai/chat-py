"""Teams Adaptive Card converter for cross-platform cards.

Python port of upstream ``packages/adapter-teams/src/cards.ts``.

Converts :class:`chat.CardElement` values to Microsoft Adaptive Cards v1.4 JSON.
Rather than depending on the ``@microsoft/teams.cards`` SDK (JavaScript-only),
the output is a plain ``dict[str, Any]`` matching the published JSON schema.

See https://adaptivecards.io/ for the Adaptive Card reference.
"""

from __future__ import annotations

from typing import Any

from chat.cards import card_child_to_fallback_text
from chat_adapter_shared import (
    card_to_fallback_text as _shared_card_to_fallback_text,
)
from chat_adapter_shared import (
    create_emoji_converter,
    map_button_style,
)

_convert_emoji = create_emoji_converter("teams")

_ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
_ADAPTIVE_CARD_VERSION = "1.4"

#: Sentinel action ID for auto-injected submit buttons when a card has
#: select / radio_select inputs but no explicit submit button.
AUTO_SUBMIT_ACTION_ID = "__auto_submit"


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def card_to_adaptive_card(card: dict[str, Any]) -> dict[str, Any]:
    """Convert a :class:`chat.CardElement` dict to an Adaptive Card JSON dict."""

    body: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    title = card.get("title")
    if title:
        body.append(
            {
                "type": "TextBlock",
                "text": _convert_emoji(str(title)),
                "weight": "Bolder",
                "size": "Large",
                "wrap": True,
            }
        )

    subtitle = card.get("subtitle")
    if subtitle:
        body.append(
            {
                "type": "TextBlock",
                "text": _convert_emoji(str(subtitle)),
                "isSubtle": True,
                "wrap": True,
            }
        )

    image_url = card.get("imageUrl")
    if image_url:
        body.append(
            {
                "type": "Image",
                "url": str(image_url),
                "size": "Stretch",
            }
        )

    for child in card.get("children", []) or []:
        elements, child_actions = _convert_child_to_adaptive(child)
        body.extend(elements)
        actions.extend(child_actions)

    adaptive: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": _ADAPTIVE_CARD_SCHEMA,
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    if actions:
        adaptive["actions"] = actions
    return adaptive


def card_to_fallback_text(card: dict[str, Any]) -> str:
    """Render a :class:`CardElement` to Teams-flavored plain text."""

    return _shared_card_to_fallback_text(
        card,
        {"bold_format": "**", "line_break": "\n\n", "platform": "teams"},
    )


# ---------------------------------------------------------------------------
# Child converters
# ---------------------------------------------------------------------------


def _convert_child_to_adaptive(
    child: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(elements, actions)`` for a card child element."""

    kind = child.get("type")
    if kind == "text":
        return [_convert_text_to_element(child)], []
    if kind == "image":
        return [_convert_image_to_element(child)], []
    if kind == "divider":
        return [_convert_divider_to_element()], []
    if kind == "actions":
        return _convert_actions_to_elements(child)
    if kind == "section":
        return _convert_section_to_elements(child)
    if kind == "fields":
        return [_convert_fields_to_element(child)], []
    if kind == "link":
        label = _convert_emoji(str(child.get("label", "")))
        url = str(child.get("url", ""))
        return (
            [
                {
                    "type": "TextBlock",
                    "text": f"[{label}]({url})",
                    "wrap": True,
                }
            ],
            [],
        )
    if kind == "table":
        return [_convert_table_to_element(child)], []

    text = card_child_to_fallback_text(child)
    if text:
        return [{"type": "TextBlock", "text": text, "wrap": True}], []
    return [], []


def _convert_text_to_element(element: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "TextBlock",
        "text": _convert_emoji(str(element.get("content", ""))),
        "wrap": True,
    }
    style = element.get("style")
    if style == "bold":
        node["weight"] = "Bolder"
    elif style == "muted":
        node["isSubtle"] = True
    return node


def _convert_image_to_element(element: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Image",
        "url": str(element.get("url", "")),
        "altText": str(element.get("alt") or "Image"),
        "size": "Auto",
    }


def _convert_divider_to_element() -> dict[str, Any]:
    # Adaptive Cards don't have a native divider — use an empty separator Container.
    return {"type": "Container", "separator": True, "items": []}


def _convert_actions_to_elements(
    element: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    elements: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    has_buttons = False
    has_inputs = False

    for child in element.get("children", []) or []:
        kind = child.get("type")
        if kind == "button":
            has_buttons = True
            actions.append(_convert_button_to_action(child))
        elif kind == "link-button":
            actions.append(_convert_link_button_to_action(child))
        elif kind == "select":
            has_inputs = True
            elements.append(_convert_select_to_element(child))
        elif kind == "radio_select":
            has_inputs = True
            elements.append(_convert_radio_select_to_element(child))

    # Teams inputs do not auto-submit — inject a Submit action when inputs are
    # present but no explicit button exists.
    if has_inputs and not has_buttons:
        actions.append(
            {
                "type": "Action.Submit",
                "title": "Submit",
                "data": {"actionId": AUTO_SUBMIT_ACTION_ID},
            }
        )

    return elements, actions


def _convert_select_to_element(select: dict[str, Any]) -> dict[str, Any]:
    choices = [
        {
            "title": _convert_emoji(str(opt.get("label", ""))),
            "value": str(opt.get("value", "")),
        }
        for opt in select.get("options", []) or []
    ]
    node: dict[str, Any] = {
        "type": "Input.ChoiceSet",
        "id": str(select.get("id", "")),
        "label": _convert_emoji(str(select.get("label", ""))),
        "style": "compact",
        "isRequired": not bool(select.get("optional") or False),
        "choices": choices,
    }
    placeholder = select.get("placeholder")
    if placeholder is not None:
        node["placeholder"] = str(placeholder)
    initial = select.get("initialOption")
    if initial is not None:
        node["value"] = str(initial)
    return node


def _convert_radio_select_to_element(radio: dict[str, Any]) -> dict[str, Any]:
    choices = [
        {
            "title": _convert_emoji(str(opt.get("label", ""))),
            "value": str(opt.get("value", "")),
        }
        for opt in radio.get("options", []) or []
    ]
    node: dict[str, Any] = {
        "type": "Input.ChoiceSet",
        "id": str(radio.get("id", "")),
        "label": _convert_emoji(str(radio.get("label", ""))),
        "style": "expanded",
        "isRequired": not bool(radio.get("optional") or False),
        "choices": choices,
    }
    initial = radio.get("initialOption")
    if initial is not None:
        node["value"] = str(initial)
    return node


def _convert_button_to_action(button: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "actionId": str(button.get("id", "")),
        "value": button.get("value"),
    }
    if button.get("actionType") == "modal":
        data["msteams"] = {"type": "task/fetch"}

    action: dict[str, Any] = {
        "type": "Action.Submit",
        "title": _convert_emoji(str(button.get("label", ""))),
        "data": data,
    }
    style = map_button_style(button.get("style"), "teams")
    if style:
        action["style"] = style
    return action


def _convert_link_button_to_action(button: dict[str, Any]) -> dict[str, Any]:
    action: dict[str, Any] = {
        "type": "Action.OpenUrl",
        "title": _convert_emoji(str(button.get("label", ""))),
        "url": str(button.get("url", "")),
    }
    style = map_button_style(button.get("style"), "teams")
    if style:
        action["style"] = style
    return action


def _convert_fields_to_element(element: dict[str, Any]) -> dict[str, Any]:
    facts = [
        {
            "title": _convert_emoji(str(child.get("label", ""))),
            "value": _convert_emoji(str(child.get("value", ""))),
        }
        for child in element.get("children", []) or []
    ]
    return {"type": "FactSet", "facts": facts}


def _convert_section_to_elements(
    element: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    container_items: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for child in element.get("children", []) or []:
        items, child_actions = _convert_child_to_adaptive(child)
        container_items.extend(items)
        actions.extend(child_actions)

    elements: list[dict[str, Any]] = []
    if container_items:
        elements.append({"type": "Container", "items": container_items})
    return elements, actions


def _convert_table_to_element(element: dict[str, Any]) -> dict[str, Any]:
    headers = [str(h) for h in element.get("headers", []) or []]
    rows = [[str(c) for c in row] for row in element.get("rows", []) or []]

    header_row = {
        "type": "ColumnSet",
        "columns": [
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": _convert_emoji(h),
                        "weight": "Bolder",
                        "wrap": True,
                    }
                ],
            }
            for h in headers
        ],
    }

    data_rows = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": _convert_emoji(cell),
                            "wrap": True,
                        }
                    ],
                }
                for cell in row
            ],
        }
        for row in rows
    ]

    return {"type": "Container", "items": [header_row, *data_rows]}


__all__ = [
    "AUTO_SUBMIT_ACTION_ID",
    "card_to_adaptive_card",
    "card_to_fallback_text",
]
