"""WhatsApp cards translator.

Python port of upstream ``packages/adapter-whatsapp/src/cards.ts``.

Renders :class:`chat.CardElement` dicts as either a WhatsApp interactive
button message (when ``actions`` children include 1-3 reply buttons) or a
text fallback that uses WhatsApp's ``*bold*`` / ``_italic_`` flavour. Also
provides callback-data encode / decode helpers used by reply-button IDs.

See https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-messages.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from .types import WhatsAppInteractiveMessage

_CALLBACK_DATA_PREFIX = "chat:"

# WhatsApp accepts at most three reply buttons per interactive message.
_MAX_REPLY_BUTTONS = 3

# Each reply-button title is capped at 20 characters by WhatsApp.
_MAX_BUTTON_TITLE_LENGTH = 20

# The interactive ``body.text`` field tops out at 1024 characters.
_MAX_BODY_LENGTH = 1024

# Header text (when present) tops out at 60 characters.
_MAX_HEADER_LENGTH = 60


class _WhatsAppCardActionPayload(TypedDict, total=False):
    a: str
    v: str


class WhatsAppCardInteractiveResult(TypedDict):
    """Interactive-message rendering of a card with reply buttons."""

    type: str
    interactive: WhatsAppInteractiveMessage


class WhatsAppCardTextResult(TypedDict):
    """Text fallback rendering of a card."""

    type: str
    text: str


WhatsAppCardResult = WhatsAppCardInteractiveResult | WhatsAppCardTextResult


def encode_whatsapp_callback_data(action_id: str, value: str | None = None) -> str:
    """Encode an ``actionId`` / ``value`` pair into a WhatsApp callback payload.

    Format: ``chat:{json}`` where ``json`` is ``{"a": actionId, "v"?: value}``.
    """

    payload: _WhatsAppCardActionPayload = {"a": action_id}
    if isinstance(value, str):
        payload["v"] = value
    return f"{_CALLBACK_DATA_PREFIX}{json.dumps(payload, separators=(',', ':'))}"


def decode_whatsapp_callback_data(data: str | None = None) -> dict[str, str | None]:
    """Decode callback data from a WhatsApp interactive reply.

    Returns ``{"actionId": ..., "value": ...}``. Falls back to passthrough for
    legacy or externally-generated button IDs that do not use the ``chat:``
    prefix — the raw string becomes both ``actionId`` and ``value``.
    """

    if not data:
        return {"actionId": "whatsapp_callback", "value": None}

    if not data.startswith(_CALLBACK_DATA_PREFIX):
        return {"actionId": data, "value": data}

    try:
        decoded = json.loads(data[len(_CALLBACK_DATA_PREFIX) :])
    except (ValueError, json.JSONDecodeError):
        return {"actionId": data, "value": data}

    if isinstance(decoded, dict):
        action_id = decoded.get("a")
        if isinstance(action_id, str) and action_id:
            raw_value = decoded.get("v")
            value = raw_value if isinstance(raw_value, str) else None
            return {"actionId": action_id, "value": value}

    return {"actionId": data, "value": data}


def card_to_whatsapp(card: dict[str, Any]) -> WhatsAppCardResult:
    """Render a :class:`chat.CardElement` dict to a WhatsApp message payload.

    If the card has reply-button ``actions`` that fit WhatsApp's constraints
    (max 3, titles truncated to 20 chars), produces an interactive button
    message. Otherwise falls back to a formatted text message.
    """

    actions = _find_actions(card.get("children") or [])
    action_buttons = _extract_reply_buttons(actions) if actions else None

    if action_buttons:
        body_text = _build_body_text(card)
        title = card.get("title")
        interactive: WhatsAppInteractiveMessage = {
            "type": "button",
            "body": {
                "text": _truncate(
                    body_text or "Please choose an option",
                    _MAX_BODY_LENGTH,
                ),
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": encode_whatsapp_callback_data(
                                str(btn.get("id", "")),
                                btn.get("value") if isinstance(btn.get("value"), str) else None,
                            ),
                            "title": _truncate(
                                str(btn.get("label", "")),
                                _MAX_BUTTON_TITLE_LENGTH,
                            ),
                        },
                    }
                    for btn in action_buttons
                ],
            },
        }
        if isinstance(title, str) and title:
            interactive["header"] = {
                "type": "text",
                "text": _truncate(title, _MAX_HEADER_LENGTH),
            }
        return {"type": "interactive", "interactive": interactive}

    return {"type": "text", "text": card_to_whatsapp_text(card)}


def card_to_whatsapp_text(card: dict[str, Any]) -> str:
    """Render a card to WhatsApp-flavoured markdown text.

    Used as fallback when an interactive message can't represent the card.
    """

    lines: list[str] = []

    title = card.get("title")
    if isinstance(title, str) and title:
        lines.append(f"*{_escape_whatsapp(title)}*")

    subtitle = card.get("subtitle")
    if isinstance(subtitle, str) and subtitle:
        lines.append(_escape_whatsapp(subtitle))

    children = card.get("children") or []
    if (title or subtitle) and children:
        lines.append("")

    image_url = card.get("imageUrl")
    if isinstance(image_url, str) and image_url:
        lines.append(image_url)
        lines.append("")

    for i, child in enumerate(children):
        child_lines = _render_child(child)
        if child_lines:
            lines.extend(child_lines)
            if i < len(children) - 1:
                lines.append("")

    return "\n".join(lines)


def card_to_plain_text(card: dict[str, Any]) -> str:
    """Render a card to plain text, with no formatting."""

    parts: list[str] = []

    title = card.get("title")
    if isinstance(title, str) and title:
        parts.append(title)

    subtitle = card.get("subtitle")
    if isinstance(subtitle, str) and subtitle:
        parts.append(subtitle)

    for child in card.get("children") or []:
        text = _child_to_plain_text(child)
        if text:
            parts.append(text)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _render_child(child: dict[str, Any]) -> list[str]:
    ctype = child.get("type")
    if ctype == "text":
        return _render_text(child)
    if ctype == "fields":
        return _render_fields(child)
    if ctype == "actions":
        return _render_actions(child)
    if ctype == "section":
        out: list[str] = []
        for nested in child.get("children") or []:
            out.extend(_render_child(nested))
        return out
    if ctype == "image":
        url = str(child.get("url", ""))
        alt = child.get("alt")
        if isinstance(alt, str) and alt:
            return [f"{alt}: {url}"]
        return [url]
    if ctype == "divider":
        return ["---"]
    return []


def _render_text(text: dict[str, Any]) -> list[str]:
    content = str(text.get("content", ""))
    style = text.get("style")
    if style == "bold":
        return [f"*{_escape_whatsapp(content)}*"]
    if style == "muted":
        return [f"_{_escape_whatsapp(content)}_"]
    return [_escape_whatsapp(content)]


def _render_fields(fields: dict[str, Any]) -> list[str]:
    return [
        f"*{_escape_whatsapp(str(f.get('label', '')))}:* {_escape_whatsapp(str(f.get('value', '')))}"
        for f in fields.get("children") or []
    ]


def _render_actions(actions: dict[str, Any]) -> list[str]:
    button_texts: list[str] = []
    for button in actions.get("children") or []:
        if button.get("type") == "link-button":
            button_texts.append(
                f"{_escape_whatsapp(str(button.get('label', '')))}: {button.get('url', '')}",
            )
        else:
            button_texts.append(f"[{_escape_whatsapp(str(button.get('label', '')))}]")
    if not button_texts:
        return []
    return [" | ".join(button_texts)]


def _child_to_plain_text(child: dict[str, Any]) -> str | None:
    ctype = child.get("type")
    if ctype == "text":
        return str(child.get("content", ""))
    if ctype == "fields":
        return "\n".join(
            f"{f.get('label', '')}: {f.get('value', '')}" for f in child.get("children") or []
        )
    if ctype == "actions":
        return None
    if ctype == "section":
        nested_lines = [_child_to_plain_text(nested) for nested in child.get("children") or []]
        return "\n".join(line for line in nested_lines if line)
    return None


def _find_actions(children: list[dict[str, Any]]) -> dict[str, Any] | None:
    for child in children:
        if child.get("type") == "actions":
            return child
        if child.get("type") == "section":
            nested = _find_actions(child.get("children") or [])
            if nested:
                return nested
    return None


def _extract_reply_buttons(
    actions: dict[str, Any],
) -> list[dict[str, Any]] | None:
    buttons: list[dict[str, Any]] = []
    for child in actions.get("children") or []:
        if child.get("type") == "button" and child.get("id"):
            buttons.append(child)
    if not buttons:
        return None
    return buttons[:_MAX_REPLY_BUTTONS]


def _build_body_text(card: dict[str, Any]) -> str:
    parts: list[str] = []
    subtitle = card.get("subtitle")
    if isinstance(subtitle, str) and subtitle:
        parts.append(subtitle)
    for child in card.get("children") or []:
        if child.get("type") == "actions":
            continue
        text = _child_to_plain_text(child)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _escape_whatsapp(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("~", "\\~")
        .replace("`", "\\`")
    )


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "\u2026"


__all__ = [
    "WhatsAppCardInteractiveResult",
    "WhatsAppCardResult",
    "WhatsAppCardTextResult",
    "card_to_plain_text",
    "card_to_whatsapp",
    "card_to_whatsapp_text",
    "decode_whatsapp_callback_data",
    "encode_whatsapp_callback_data",
]
