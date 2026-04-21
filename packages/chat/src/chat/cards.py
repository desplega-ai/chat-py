"""Card elements for cross-platform rich messaging — Python port of ``packages/chat/src/cards.ts``.

Provides builder functions for creating rich cards that automatically convert to
platform-specific formats (Slack Block Kit, Teams Adaptive Cards, Google Chat
Card v2). Cards are plain ``dict[str, Any]`` values with camelCase keys so the
wire format round-trips with the upstream TypeScript SDK.

The Python API mirrors upstream exactly — we keep PascalCase builder names
(``Card``, ``Text``, ``Button``, ...) so docs and cross-language snippets stay
1:1 with the TypeScript ``Card()``/``Text()`` factory functions. There is no
JSX equivalent; compose cards with nested builder calls and the ``children=``
keyword argument.

Example::

    from chat.cards import Actions, Button, Card, Text

    card = Card(
        title="Order #1234",
        children=[
            Text("Total: $50.00"),
            Actions([
                Button(id="approve", label="Approve", style="primary"),
                Button(id="reject", label="Reject", style="danger"),
            ]),
        ],
    )
    await thread.post(card)
"""

from __future__ import annotations

from typing import Any, Literal, Required, TypedDict

from chat.markdown import table_element_to_ascii

# ============================================================================
# Card element types
# ============================================================================

ButtonStyle = Literal["primary", "danger", "default"]
"""Button style options."""

TextStyle = Literal["plain", "bold", "muted"]
"""Text style options."""

TableAlignment = Literal["left", "center", "right"]
"""Column alignment for :class:`TableElement`."""


class ButtonElement(TypedDict, total=False):
    """Interactive button element."""

    type: Required[Literal["button"]]
    id: Required[str]
    label: Required[str]
    style: ButtonStyle | None
    value: str | None
    disabled: bool | None
    actionType: Literal["action", "modal"] | None


class LinkButtonElement(TypedDict, total=False):
    """Button that opens a URL when clicked."""

    type: Required[Literal["link-button"]]
    label: Required[str]
    url: Required[str]
    style: ButtonStyle | None


class TextElement(TypedDict, total=False):
    """Text content element."""

    type: Required[Literal["text"]]
    content: Required[str]
    style: TextStyle | None


class ImageElement(TypedDict, total=False):
    """Image element."""

    type: Required[Literal["image"]]
    url: Required[str]
    alt: str | None


class DividerElement(TypedDict):
    """Visual divider / separator."""

    type: Literal["divider"]


class LinkElement(TypedDict):
    """Inline hyperlink element."""

    type: Literal["link"]
    label: str
    url: str


class FieldElement(TypedDict):
    """Field for key-value display."""

    type: Literal["field"]
    label: str
    value: str


class FieldsElement(TypedDict):
    """Fields container for multi-column layout."""

    type: Literal["fields"]
    children: list[FieldElement]


class TableElement(TypedDict, total=False):
    """Table element for structured data display."""

    type: Required[Literal["table"]]
    headers: Required[list[str]]
    rows: Required[list[list[str]]]
    align: list[TableAlignment] | None


# ``ActionsElement`` and ``SectionElement`` reference ``SelectElement`` /
# ``RadioSelectElement`` from :mod:`chat.modals`. We type ``children`` loosely
# as ``list[dict[str, Any]]`` at the TypedDict level to keep the module free of
# a circular import while still carrying the shape at runtime.
class ActionsElement(TypedDict):
    """Container for action buttons, selects, and radio selects."""

    type: Literal["actions"]
    children: list[dict[str, Any]]


class SectionElement(TypedDict):
    """Section container for grouping elements."""

    type: Literal["section"]
    children: list[dict[str, Any]]


CardChild = (
    TextElement
    | ImageElement
    | DividerElement
    | ActionsElement
    | SectionElement
    | FieldsElement
    | LinkElement
    | TableElement
)
"""Union of all element types that may appear as a direct child of a Card."""


class CardElement(TypedDict, total=False):
    """Root card element."""

    type: Required[Literal["card"]]
    title: str | None
    subtitle: str | None
    imageUrl: str | None
    children: Required[list[dict[str, Any]]]


# ============================================================================
# Type guard
# ============================================================================


def is_card_element(value: Any) -> bool:
    """Return ``True`` if *value* is a ``CardElement`` dict."""
    return isinstance(value, dict) and value.get("type") == "card"


# ============================================================================
# Builder functions
# ============================================================================


def Card(
    *,
    title: str | None = None,
    subtitle: str | None = None,
    image_url: str | None = None,
    children: list[dict[str, Any]] | None = None,
) -> CardElement:
    """Create a ``CardElement``.

    ``image_url`` is the Pythonic alias for upstream ``imageUrl``; the emitted
    dict key stays ``imageUrl`` for wire compatibility.
    """
    card: CardElement = {"type": "card", "children": list(children or [])}
    if title is not None:
        card["title"] = title
    if subtitle is not None:
        card["subtitle"] = subtitle
    if image_url is not None:
        card["imageUrl"] = image_url
    return card


def Text(content: str, *, style: TextStyle | None = None) -> TextElement:
    """Create a ``TextElement``."""
    node: TextElement = {"type": "text", "content": content}
    if style is not None:
        node["style"] = style
    return node


#: Alias for :func:`Text` that avoids shadowing the built-in ``Text`` in
#: environments where ``Text`` is already defined.
CardText = Text


def Image(*, url: str, alt: str | None = None) -> ImageElement:
    """Create an ``ImageElement``."""
    node: ImageElement = {"type": "image", "url": url}
    if alt is not None:
        node["alt"] = alt
    return node


def Divider() -> DividerElement:
    """Create a ``DividerElement``."""
    return {"type": "divider"}


def Section(children: list[dict[str, Any]]) -> SectionElement:
    """Create a ``SectionElement``."""
    return {"type": "section", "children": list(children)}


def Actions(children: list[dict[str, Any]]) -> ActionsElement:
    """Create an ``ActionsElement`` containing buttons / selects / radio selects."""
    return {"type": "actions", "children": list(children)}


def Button(
    *,
    id: str,
    label: str,
    style: ButtonStyle | None = None,
    value: str | None = None,
    disabled: bool | None = None,
    action_type: Literal["action", "modal"] | None = None,
) -> ButtonElement:
    """Create a ``ButtonElement``."""
    node: ButtonElement = {"type": "button", "id": id, "label": label}
    if style is not None:
        node["style"] = style
    if value is not None:
        node["value"] = value
    if disabled is not None:
        node["disabled"] = disabled
    if action_type is not None:
        node["actionType"] = action_type
    return node


def LinkButton(
    *,
    url: str,
    label: str,
    style: ButtonStyle | None = None,
) -> LinkButtonElement:
    """Create a ``LinkButtonElement`` that opens *url* when clicked."""
    node: LinkButtonElement = {"type": "link-button", "url": url, "label": label}
    if style is not None:
        node["style"] = style
    return node


def Field(*, label: str, value: str) -> FieldElement:
    """Create a ``FieldElement``."""
    return {"type": "field", "label": label, "value": value}


def Fields(children: list[FieldElement]) -> FieldsElement:
    """Create a ``FieldsElement``."""
    return {"type": "fields", "children": list(children)}


def Table(
    *,
    headers: list[str],
    rows: list[list[str]],
    align: list[TableAlignment] | None = None,
) -> TableElement:
    """Create a ``TableElement``."""
    node: TableElement = {
        "type": "table",
        "headers": list(headers),
        "rows": [list(r) for r in rows],
    }
    if align is not None:
        node["align"] = list(align)
    return node


def CardLink(*, url: str, label: str) -> LinkElement:
    """Create a ``LinkElement`` for inline hyperlinks in cards."""
    return {"type": "link", "url": url, "label": label}


# ============================================================================
# Fallback text generation
# ============================================================================


def card_to_fallback_text(card: CardElement) -> str:
    """Render a :class:`CardElement` to plain-text fallback.

    Used for platforms that cannot render rich cards and for the
    ``SentMessage.text`` property.
    """
    parts: list[str] = []
    if card.get("title"):
        parts.append(f"**{card['title']}**")
    if card.get("subtitle"):
        parts.append(str(card["subtitle"]))

    for child in card.get("children", []):
        text = card_child_to_fallback_text(child)
        if text:
            parts.append(text)

    return "\n".join(parts)


def card_child_to_fallback_text(child: dict[str, Any]) -> str | None:
    """Render a card child element to plain-text fallback or ``None``.

    Returning ``None`` signals "contribute nothing" (used for interactive-only
    elements like :class:`ActionsElement` — see Slack guidance on fallback text
    excluding actions).
    """
    kind = child.get("type")
    if kind == "text":
        return str(child.get("content", ""))
    if kind == "link":
        return f"{child.get('label', '')} ({child.get('url', '')})"
    if kind == "fields":
        inner = [f"{f.get('label', '')}: {f.get('value', '')}" for f in child.get("children", [])]
        return "\n".join(inner)
    if kind == "actions":
        # Interactive-only — exclude from fallback text.
        return None
    if kind == "table":
        return table_element_to_ascii(child.get("headers", []) or [], child.get("rows", []) or [])
    if kind == "section":
        lines = [card_child_to_fallback_text(c) for c in child.get("children", [])]
        return "\n".join(line for line in lines if line)
    return None


__all__ = [
    "Actions",
    "ActionsElement",
    "Button",
    "ButtonElement",
    "ButtonStyle",
    "Card",
    "CardChild",
    "CardElement",
    "CardLink",
    "CardText",
    "Divider",
    "DividerElement",
    "Field",
    "FieldElement",
    "Fields",
    "FieldsElement",
    "Image",
    "ImageElement",
    "LinkButton",
    "LinkButtonElement",
    "LinkElement",
    "Section",
    "SectionElement",
    "Table",
    "TableAlignment",
    "TableElement",
    "Text",
    "TextElement",
    "TextStyle",
    "card_child_to_fallback_text",
    "card_to_fallback_text",
    "is_card_element",
]
