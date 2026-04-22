"""Discord Embed and Component converter for cross-platform cards.

Python port of upstream ``packages/adapter-discord/src/cards.ts``.

Converts :class:`chat.CardElement` values to Discord message payload fragments:
embeds (https://discord.com/developers/docs/resources/message#embed-object) and
action-row components
(https://discord.com/developers/docs/interactions/message-components).

Rather than depending on the JavaScript-only ``discord-api-types`` or
``discord.py`` packages, the output is plain ``dict[str, Any]`` matching the
Discord REST JSON schema.
"""

from __future__ import annotations

from typing import Any

from chat.cards import card_child_to_fallback_text
from chat_adapter_shared import (
    create_emoji_converter,
    render_gfm_table,
)

_convert_emoji = create_emoji_converter("discord")

# Discord blurple
_DEFAULT_EMBED_COLOR = 0x5865F2

# Discord button style enum — https://discord.com/developers/docs/interactions/message-components#button-object-button-styles
BUTTON_STYLE_PRIMARY = 1
BUTTON_STYLE_SECONDARY = 2
BUTTON_STYLE_SUCCESS = 3
BUTTON_STYLE_DANGER = 4
BUTTON_STYLE_LINK = 5


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def card_to_discord_payload(card: dict[str, Any]) -> dict[str, Any]:
    """Convert a :class:`chat.CardElement` to Discord embeds + components.

    Returns ``{"embeds": [...], "components": [...]}``.
    """

    embed: dict[str, Any] = {}
    fields: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []

    title = card.get("title")
    if title:
        embed["title"] = _convert_emoji(str(title))

    subtitle = card.get("subtitle")
    if subtitle:
        embed["description"] = _convert_emoji(str(subtitle))

    image_url = card.get("imageUrl")
    if image_url:
        embed["image"] = {"url": str(image_url)}

    embed["color"] = _DEFAULT_EMBED_COLOR

    text_parts: list[str] = []
    for child in card.get("children", []) or []:
        _process_child(child, text_parts, fields, components)

    if text_parts:
        joined = "\n\n".join(text_parts)
        existing = embed.get("description")
        if existing:
            embed["description"] = f"{existing}\n\n{joined}"
        else:
            embed["description"] = joined

    if fields:
        embed["fields"] = fields

    return {"embeds": [embed], "components": components}


def card_to_fallback_text(card: dict[str, Any]) -> str:
    """Render a :class:`CardElement` to Discord-flavored plain text.

    Mirrors upstream ``cardToFallbackText`` — bolds field labels, flattens
    sections, drops actions (they're interactive-only).
    """

    parts: list[str] = []
    title = card.get("title")
    if title:
        parts.append(f"**{_convert_emoji(str(title))}**")

    subtitle = card.get("subtitle")
    if subtitle:
        parts.append(_convert_emoji(str(subtitle)))

    for child in card.get("children", []) or []:
        text = _child_to_fallback_text(child)
        if text:
            parts.append(text)

    return "\n\n".join(parts)


def _child_to_fallback_text(child: dict[str, Any]) -> str | None:
    kind = child.get("type")
    if kind == "text":
        return _convert_emoji(str(child.get("content", "")))
    if kind == "fields":
        lines = [
            f"**{_convert_emoji(str(field.get('label', '')))}**: "
            f"{_convert_emoji(str(field.get('value', '')))}"
            for field in child.get("children", []) or []
        ]
        return "\n".join(lines)
    if kind == "actions":
        # Actions are interactive-only — excluded from fallback text.
        return None
    if kind == "section":
        lines = [
            text
            for text in (_child_to_fallback_text(c) for c in child.get("children", []) or [])
            if text
        ]
        return "\n".join(lines)
    if kind == "table":
        from chat import table_element_to_ascii

        headers = [str(h) for h in child.get("headers", []) or []]
        rows = [[str(c) for c in row] for row in child.get("rows", []) or []]
        return f"```\n{table_element_to_ascii(headers, rows)}\n```"
    if kind == "divider":
        return "---"
    return card_child_to_fallback_text(child)


# ---------------------------------------------------------------------------
# Child converters
# ---------------------------------------------------------------------------


def _process_child(
    child: dict[str, Any],
    text_parts: list[str],
    fields: list[dict[str, Any]],
    components: list[dict[str, Any]],
) -> None:
    kind = child.get("type")
    if kind == "text":
        text_parts.append(_convert_text_element(child))
    elif kind == "image":
        # Discord embeds only have one image (set at card level). Additional
        # images would require separate embeds — upstream leaves them out, we
        # follow suit.
        return
    elif kind == "divider":
        text_parts.append("───────────")
    elif kind == "actions":
        components.extend(_convert_actions_to_rows(child))
    elif kind == "section":
        for sub in child.get("children", []) or []:
            _process_child(sub, text_parts, fields, components)
    elif kind == "fields":
        for field in child.get("children", []) or []:
            fields.append(
                {
                    "name": _convert_emoji(str(field.get("label", ""))),
                    "value": _convert_emoji(str(field.get("value", ""))),
                    "inline": True,
                }
            )
    elif kind == "link":
        label = _convert_emoji(str(child.get("label", "")))
        url = str(child.get("url", ""))
        text_parts.append(f"[{label}]({url})")
    elif kind == "table":
        text_parts.append("\n".join(render_gfm_table(child)))
    else:
        text = card_child_to_fallback_text(child)
        if text:
            text_parts.append(text)


def _convert_text_element(element: dict[str, Any]) -> str:
    text = _convert_emoji(str(element.get("content", "")))
    style = element.get("style")
    if style == "bold":
        return f"**{text}**"
    if style == "muted":
        return f"*{text}*"
    return text


def _convert_actions_to_rows(element: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert an actions element to a list of Discord action rows.

    Discord allows max 5 buttons per action row, so we chunk them.
    """

    buttons: list[dict[str, Any]] = []
    for child in element.get("children", []) or []:
        kind = child.get("type")
        if kind == "button":
            buttons.append(_convert_button_element(child))
        elif kind == "link-button":
            buttons.append(_convert_link_button_element(child))

    rows: list[dict[str, Any]] = []
    for i in range(0, len(buttons), 5):
        rows.append({"type": 1, "components": buttons[i : i + 5]})
    return rows


def _convert_button_element(button: dict[str, Any]) -> dict[str, Any]:
    discord_button: dict[str, Any] = {
        "type": 2,
        "style": _get_button_style(button.get("style")),
        "label": str(button.get("label", "")),
        "custom_id": str(button.get("id", "")),
    }
    if button.get("disabled"):
        discord_button["disabled"] = True
    return discord_button


def _convert_link_button_element(button: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": 2,
        "style": BUTTON_STYLE_LINK,
        "label": str(button.get("label", "")),
        "url": str(button.get("url", "")),
    }


def _get_button_style(style: str | None) -> int:
    if style == "primary":
        return BUTTON_STYLE_PRIMARY
    if style == "danger":
        return BUTTON_STYLE_DANGER
    return BUTTON_STYLE_SECONDARY


__all__ = [
    "BUTTON_STYLE_DANGER",
    "BUTTON_STYLE_LINK",
    "BUTTON_STYLE_PRIMARY",
    "BUTTON_STYLE_SECONDARY",
    "BUTTON_STYLE_SUCCESS",
    "card_to_discord_payload",
    "card_to_fallback_text",
]
