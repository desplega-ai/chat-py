"""Google Chat Card v2 converter for cross-platform cards.

Python port of upstream ``packages/adapter-gchat/src/cards.ts``.

Converts :class:`~chat.CardElement` dicts to Google Chat Cards v2 JSON.

Reference: https://developers.google.com/chat/api/reference/rest/v1/cards
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from chat_adapter_shared import (
    card_to_fallback_text as _shared_card_to_fallback_text,
)
from chat_adapter_shared import (
    create_emoji_converter,
)

if TYPE_CHECKING:
    from chat import CardElement

# ---------------------------------------------------------------------------
# Types — Google Chat Cards v2 wire shapes
# ---------------------------------------------------------------------------


class GoogleChatCardHeader(TypedDict, total=False):
    title: str
    subtitle: str
    imageUrl: str
    imageType: Literal["CIRCLE", "SQUARE"]


class GoogleChatCardBody(TypedDict, total=False):
    header: GoogleChatCardHeader
    sections: list[GoogleChatCardSection]


class GoogleChatCard(TypedDict, total=False):
    """Outer Cards v2 envelope — ``{card: {...}, cardId?: str}``."""

    card: GoogleChatCardBody
    cardId: str


class GoogleChatCardSection(TypedDict, total=False):
    collapsible: bool
    header: str
    widgets: list[GoogleChatWidget]


class GoogleChatWidget(TypedDict, total=False):
    buttonList: dict[str, Any]
    decoratedText: dict[str, Any]
    divider: dict[str, Any]
    image: dict[str, Any]
    selectionInput: dict[str, Any]
    textParagraph: dict[str, Any]


class CardConversionOptions(TypedDict, total=False):
    """Options accepted by :func:`card_to_google_card`."""

    cardId: str
    endpointUrl: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Markdown ``**bold**`` → Google Chat ``*bold*``.
_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")

_convert_emoji = create_emoji_converter("gchat")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or fall back to attribute access."""

    if isinstance(obj, dict):
        value = obj.get(key, default)
        return value if value is not None else default
    value = getattr(obj, key, default)
    return value if value is not None else default


def _markdown_to_gchat(text: str) -> str:
    """Rewrite standard markdown ``**bold**`` to Google Chat ``*bold*``."""

    return _BOLD_PATTERN.sub(r"*\1*", text)


# ---------------------------------------------------------------------------
# Card → Cards v2 JSON
# ---------------------------------------------------------------------------


def card_to_google_card(
    card: CardElement,
    options: CardConversionOptions | str | None = None,
) -> GoogleChatCard:
    """Convert a ``CardElement`` to Google Chat Cards v2 JSON.

    The second argument may be a ``CardConversionOptions`` dict or a legacy
    ``card_id`` string (upstream retains the same backward compatibility).
    """

    if isinstance(options, str):
        opts: CardConversionOptions = {"cardId": options}
    else:
        opts = options or {}

    endpoint_url = opts.get("endpointUrl")

    # Header ---------------------------------------------------------------
    title = _get(card, "title")
    subtitle = _get(card, "subtitle")
    image_url = _get(card, "imageUrl")

    header: GoogleChatCardHeader | None = None
    if title or subtitle or image_url:
        header = {"title": _convert_emoji(title or "")}
        if subtitle:
            header["subtitle"] = _convert_emoji(subtitle)
        if image_url:
            header["imageUrl"] = image_url
            header["imageType"] = "SQUARE"

    # Sections -------------------------------------------------------------
    sections: list[GoogleChatCardSection] = []
    current_widgets: list[GoogleChatWidget] = []
    children = _get(card, "children", []) or []

    def flush_current() -> None:
        nonlocal current_widgets
        if current_widgets:
            sections.append({"widgets": current_widgets})
            current_widgets = []

    for child in children:
        child_type = _get(child, "type")
        if child_type == "section":
            flush_current()
            section_widgets = _convert_section_to_widgets(child, endpoint_url)
            sections.append({"widgets": section_widgets})
        else:
            current_widgets.extend(_convert_child_to_widgets(child, endpoint_url))

    flush_current()

    # GChat requires at least one section with at least one widget.
    if not sections:
        sections.append({"widgets": [{"textParagraph": {"text": ""}}]})

    body: GoogleChatCardBody = {"sections": sections}
    if header is not None:
        body["header"] = header

    google_card: GoogleChatCard = {"card": body}
    if opts.get("cardId"):
        google_card["cardId"] = opts["cardId"]
    return google_card


def _convert_child_to_widgets(child: Any, endpoint_url: str | None) -> list[GoogleChatWidget]:
    child_type = _get(child, "type")

    if child_type == "text":
        return [_convert_text_to_widget(child)]
    if child_type == "image":
        return [_convert_image_to_widget(child)]
    if child_type == "divider":
        return [{"divider": {}}]
    if child_type == "actions":
        return _convert_actions_to_widgets(child, endpoint_url)
    if child_type == "section":
        return _convert_section_to_widgets(child, endpoint_url)
    if child_type == "fields":
        return _convert_fields_to_widgets(child)
    if child_type == "link":
        url = _get(child, "url", "")
        label = _convert_emoji(_get(child, "label", ""))
        return [{"textParagraph": {"text": f'<a href="{url}">{label}</a>'}}]
    if child_type == "table":
        return [_convert_table_to_widget(child)]

    # Fallback — use core helper.
    from chat.cards import card_child_to_fallback_text

    text = card_child_to_fallback_text(child) if isinstance(child, dict) else None
    if text:
        return [{"textParagraph": {"text": text}}]
    return []


def _convert_text_to_widget(element: Any) -> GoogleChatWidget:
    content = _get(element, "content", "")
    style = _get(element, "style")
    text = _markdown_to_gchat(_convert_emoji(content))

    if style == "bold":
        text = f"*{text}*"
    elif style == "muted":
        # GChat has no muted style — render plain text (without markdown conversion).
        text = _convert_emoji(content)

    return {"textParagraph": {"text": text}}


def _convert_image_to_widget(element: Any) -> GoogleChatWidget:
    return {
        "image": {
            "imageUrl": _get(element, "url", ""),
            "altText": _get(element, "alt") or "Image",
        }
    }


def _convert_actions_to_widgets(element: Any, endpoint_url: str | None) -> list[GoogleChatWidget]:
    widgets: list[GoogleChatWidget] = []
    buttons: list[dict[str, Any]] = []

    def flush_buttons() -> None:
        nonlocal buttons
        if not buttons:
            return
        widgets.append({"buttonList": {"buttons": buttons}})
        buttons = []

    for child in _get(element, "children", []) or []:
        child_type = _get(child, "type")
        if child_type == "button":
            buttons.append(_convert_button(child, endpoint_url))
        elif child_type == "link-button":
            buttons.append(_convert_link_button(child))
        elif child_type in ("select", "radio_select"):
            flush_buttons()
            widgets.append(_convert_selection_input(child, endpoint_url))

    flush_buttons()
    return widgets


def _convert_selection_input(element: Any, endpoint_url: str | None) -> GoogleChatWidget:
    items: list[dict[str, Any]] = []
    initial = _get(element, "initialOption")
    for option in _get(element, "options", []) or []:
        item: dict[str, Any] = {
            "text": _convert_emoji(_get(option, "label", "")),
            "value": _get(option, "value", ""),
        }
        if item["value"] == initial:
            item["selected"] = True
        items.append(item)

    elem_type = _get(element, "type")
    selection_type = "RADIO_BUTTON" if elem_type == "radio_select" else "DROPDOWN"
    action_id = _get(element, "id", "")

    return {
        "selectionInput": {
            "name": action_id,
            "label": _convert_emoji(_get(element, "label", "")),
            "type": selection_type,
            "items": items,
            "onChangeAction": {
                "function": endpoint_url or action_id,
                "parameters": [{"key": "actionId", "value": action_id}],
            },
        }
    }


def _convert_button(button: Any, endpoint_url: str | None) -> dict[str, Any]:
    action_id = _get(button, "id", "")
    parameters: list[dict[str, str]] = [{"key": "actionId", "value": action_id}]
    value = _get(button, "value")
    if value:
        parameters.append({"key": "value", "value": value})

    google_button: dict[str, Any] = {
        "text": _convert_emoji(_get(button, "label", "")),
        "onClick": {
            "action": {
                "function": endpoint_url or action_id,
                "parameters": parameters,
            }
        },
    }

    color = _style_to_color(_get(button, "style"))
    if color is not None:
        google_button["color"] = color

    if _get(button, "disabled"):
        google_button["disabled"] = True

    return google_button


def _convert_link_button(button: Any) -> dict[str, Any]:
    google_button: dict[str, Any] = {
        "text": _convert_emoji(_get(button, "label", "")),
        "onClick": {"openLink": {"url": _get(button, "url", "")}},
    }
    color = _style_to_color(_get(button, "style"))
    if color is not None:
        google_button["color"] = color
    return google_button


def _style_to_color(style: str | None) -> dict[str, float] | None:
    if style == "primary":
        return {"red": 0.2, "green": 0.5, "blue": 0.9}
    if style == "danger":
        return {"red": 0.9, "green": 0.2, "blue": 0.2}
    return None


def _convert_section_to_widgets(element: Any, endpoint_url: str | None) -> list[GoogleChatWidget]:
    widgets: list[GoogleChatWidget] = []
    for child in _get(element, "children", []) or []:
        widgets.extend(_convert_child_to_widgets(child, endpoint_url))
    return widgets


def _convert_fields_to_widgets(element: Any) -> list[GoogleChatWidget]:
    widgets: list[GoogleChatWidget] = []
    for field in _get(element, "children", []) or []:
        widgets.append(
            {
                "decoratedText": {
                    "topLabel": _markdown_to_gchat(_convert_emoji(_get(field, "label", ""))),
                    "text": _markdown_to_gchat(_convert_emoji(_get(field, "value", ""))),
                }
            }
        )
    return widgets


def _convert_table_to_widget(element: Any) -> GoogleChatWidget:
    from chat import table_element_to_ascii

    headers = list(_get(element, "headers", []) or [])
    rows = _get(element, "rows", []) or []
    ascii_table = table_element_to_ascii(headers, rows)
    return {"textParagraph": {"text": f'<font face="monospace">{ascii_table}</font>'}}


# ---------------------------------------------------------------------------
# Fallback text
# ---------------------------------------------------------------------------


def card_to_fallback_text(card: CardElement) -> str:
    """Generate fallback text for a card.

    Used for notifications and environments where rich cards aren't rendered.
    """

    return _shared_card_to_fallback_text(
        card,
        {"boldFormat": "*", "lineBreak": "\n", "platform": "gchat"},
    )


__all__ = [
    "CardConversionOptions",
    "GoogleChatCard",
    "GoogleChatCardBody",
    "GoogleChatCardHeader",
    "GoogleChatCardSection",
    "GoogleChatWidget",
    "card_to_fallback_text",
    "card_to_google_card",
]
