"""Convert :class:`chat.CardElement` values to GitHub-flavored markdown.

Python port of upstream ``packages/adapter-github/src/cards.ts``. GitHub has
no native rich-card surface, so cards are flattened to GFM: bold title and
subtitle, fields as key/value pairs, buttons as markdown links (link buttons)
or bold-bracketed text (action buttons, since GitHub has no interactivity).
"""

from __future__ import annotations

from typing import Any

from chat.cards import card_child_to_fallback_text
from chat_adapter_shared import render_gfm_table

# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def card_to_github_markdown(card: dict[str, Any]) -> str:
    """Render a :class:`chat.CardElement` dict to GitHub-flavored markdown.

    The output mirrors the TypeScript implementation:

    * Title is rendered as bold (``**Title**``)
    * Subtitle sits on its own line under the title
    * Header image becomes a markdown image
    * Children are joined by blank lines
    """

    lines: list[str] = []

    title = card.get("title")
    if title:
        lines.append(f"**{_escape_markdown(str(title))}**")

    subtitle = card.get("subtitle")
    if subtitle:
        lines.append(_escape_markdown(str(subtitle)))

    children = card.get("children") or []

    if (title or subtitle) and children:
        lines.append("")

    image_url = card.get("imageUrl")
    if image_url:
        lines.append(f"![]({image_url})")
        lines.append("")

    for idx, child in enumerate(children):
        child_lines = _render_child(child)
        if child_lines:
            lines.extend(child_lines)
            if idx < len(children) - 1:
                lines.append("")

    return "\n".join(lines)


def card_to_plain_text(card: dict[str, Any]) -> str:
    """Generate a plain-text fallback from a card (no markdown)."""

    parts: list[str] = []

    title = card.get("title")
    if title:
        parts.append(str(title))

    subtitle = card.get("subtitle")
    if subtitle:
        parts.append(str(subtitle))

    for child in card.get("children") or []:
        text = _child_to_plain_text(child)
        if text:
            parts.append(text)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_child(child: dict[str, Any]) -> list[str]:
    """Render a single card child element to markdown lines."""

    ctype = child.get("type")

    if ctype == "text":
        return _render_text(child)

    if ctype == "fields":
        return _render_fields(child)

    if ctype == "actions":
        return _render_actions(child)

    if ctype == "section":
        out: list[str] = []
        for sub in child.get("children") or []:
            out.extend(_render_child(sub))
        return out

    if ctype == "image":
        alt = child.get("alt")
        url = child.get("url", "")
        if alt:
            return [f"![{_escape_markdown(str(alt))}]({url})"]
        return [f"![]({url})"]

    if ctype == "link":
        label = child.get("label", "")
        url = child.get("url", "")
        return [f"[{_escape_markdown(str(label))}]({url})"]

    if ctype == "divider":
        return ["---"]

    if ctype == "table":
        return _render_table(child)

    fallback = card_child_to_fallback_text(child)
    if fallback:
        return [fallback]
    return []


def _render_text(text: dict[str, Any]) -> list[str]:
    """Render a text element with its style applied."""

    content = str(text.get("content", ""))
    style = text.get("style")

    if style == "bold":
        return [f"**{content}**"]
    if style == "muted":
        return [f"_{content}_"]
    return [content]


def _render_fields(fields: dict[str, Any]) -> list[str]:
    """Render fields as ``**Label:** Value`` pairs."""

    out: list[str] = []
    for field in fields.get("children") or []:
        label = _escape_markdown(str(field.get("label", "")))
        value = _escape_markdown(str(field.get("value", "")))
        out.append(f"**{label}:** {value}")
    return out


def _render_table(table: dict[str, Any]) -> list[str]:
    """Delegate table rendering to the shared GFM helper."""

    rendered = render_gfm_table(table)
    if isinstance(rendered, list):
        return list(rendered)
    if isinstance(rendered, str):
        return rendered.splitlines() or [rendered]
    return []


def _render_actions(actions: dict[str, Any]) -> list[str]:
    """Render actions (buttons) as a joined list of links or bold text."""

    button_texts: list[str] = []
    for button in actions.get("children") or []:
        label = _escape_markdown(str(button.get("label", "")))
        if button.get("type") == "link-button":
            url = button.get("url", "")
            button_texts.append(f"[{label}]({url})")
        else:
            button_texts.append(f"**[{label}]**")
    if not button_texts:
        return []
    return [" • ".join(button_texts)]


def _child_to_plain_text(child: dict[str, Any]) -> str | None:
    """Convert a card child to its plain-text representation."""

    ctype = child.get("type")

    if ctype == "text":
        return str(child.get("content", ""))

    if ctype == "fields":
        parts = [
            f"{field.get('label', '')}: {field.get('value', '')}"
            for field in child.get("children") or []
        ]
        return "\n".join(parts)

    if ctype == "actions":
        return None

    if ctype == "table":
        return "\n".join(_render_table(child))

    if ctype == "section":
        pieces = []
        for sub in child.get("children") or []:
            sub_text = _child_to_plain_text(sub)
            if sub_text:
                pieces.append(sub_text)
        return "\n".join(pieces) if pieces else None

    fallback = card_child_to_fallback_text(child)
    return fallback or None


def _escape_markdown(text: str) -> str:
    """Escape characters that would break the surrounding markdown formatting.

    Deliberately light-handed to preserve intentional markdown. Backslash is
    escaped first so later rewrites don't double-escape.
    """

    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


__all__ = ["card_to_github_markdown", "card_to_plain_text"]
