"""Shared card conversion utilities for adapters.

Python port of upstream ``packages/adapter-shared/src/card-utils.ts``. Reduces
duplication across adapter implementations for card-to-platform-format
conversions.

Card elements are kept as plain ``dict[str, Any]`` to preserve cross-language
serialization parity with the upstream TypeScript SDK (see ``CLAUDE.md``).
Type hints reference the canonical ``chat`` core types under
``TYPE_CHECKING`` so this module loads cleanly even before the corresponding
``chat`` modules are ported.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from chat import CardChild, CardElement, TableElement


PlatformName = Literal["slack", "gchat", "teams", "discord"]
"""Supported platform names for adapter utilities."""


BUTTON_STYLE_MAPPINGS: dict[PlatformName, dict[str, str]] = {
    "slack": {"primary": "primary", "danger": "danger"},
    "gchat": {"primary": "primary", "danger": "danger"},
    "teams": {"primary": "positive", "danger": "destructive"},
    "discord": {"primary": "primary", "danger": "danger"},
}
"""Button style mappings per platform.

Maps our standard button styles (``"primary"``, ``"danger"``) to
platform-specific values.
"""


def create_emoji_converter(platform: PlatformName) -> Callable[[str], str]:
    """Create a platform-specific emoji converter function.

    Returns a callable that converts emoji placeholders (e.g. ``{{emoji:wave}}``)
    to the platform's native format.
    """

    from chat import convert_emoji_placeholders  # local import — chat may load lazily

    def _convert(text: str) -> str:
        return convert_emoji_placeholders(text, platform)

    return _convert


def map_button_style(
    style: str | None,
    platform: PlatformName,
) -> str | None:
    """Map a button style to the platform-specific value."""

    if not style:
        return None
    return BUTTON_STYLE_MAPPINGS[platform].get(style)


class FallbackTextOptions(TypedDict, total=False):
    """Options for fallback text generation."""

    bold_format: Literal["*", "**"]
    """Bold format string (default: ``"*"`` for mrkdwn, ``"**"`` for markdown)."""

    line_break: Literal["\n", "\n\n"]
    """Line break between sections (default: ``"\\n"``)."""

    platform: PlatformName
    """Platform for emoji conversion (optional)."""


def card_to_fallback_text(
    card: CardElement,
    options: FallbackTextOptions | None = None,
) -> str:
    """Generate fallback plain text from a card element.

    Used when the platform can't render rich cards or for notification
    previews. Consolidates duplicate implementations from individual adapters.
    """

    options = options or {}
    bold_format: str = options.get("bold_format", "*")
    line_break: str = options.get("line_break", "\n")
    platform: PlatformName | None = options.get("platform")

    convert_text: Callable[[str], str] = (
        create_emoji_converter(platform) if platform else (lambda t: t)
    )

    parts: list[str] = []

    title = _get(card, "title")
    if title:
        parts.append(f"{bold_format}{convert_text(title)}{bold_format}")

    subtitle = _get(card, "subtitle")
    if subtitle:
        parts.append(convert_text(subtitle))

    for child in _get(card, "children", []) or []:
        text = _child_to_fallback_text(child, convert_text)
        if text:
            parts.append(text)

    return line_break.join(parts)


def _child_to_fallback_text(
    child: CardChild,
    convert_text: Callable[[str], str],
) -> str | None:
    """Convert a card child element to fallback text. Internal helper."""

    child_type = _get(child, "type")
    if child_type == "text":
        return convert_text(_get(child, "content", ""))
    if child_type == "link":
        return f"{convert_text(_get(child, 'label', ''))} ({_get(child, 'url', '')})"
    if child_type == "fields":
        return "\n".join(
            f"{convert_text(_get(f, 'label', ''))}: {convert_text(_get(f, 'value', ''))}"
            for f in _get(child, "children", []) or []
        )
    if child_type == "actions":
        # Actions are interactive-only — exclude from fallback text.
        # Fallback text is used for notifications and screen readers where
        # buttons aren't actionable.
        return None
    if child_type == "section":
        return "\n".join(
            t
            for t in (
                _child_to_fallback_text(c, convert_text)
                for c in _get(child, "children", []) or []
            )
            if t
        )
    if child_type == "table":
        from chat import table_element_to_ascii

        return table_element_to_ascii(_get(child, "headers", []), _get(child, "rows", []))
    if child_type == "divider":
        return "---"

    # Unknown — defer to chat core
    from chat import card_child_to_fallback_text as _core

    return _core(child)


def escape_table_cell(value: str) -> str:
    """Escape a cell value for use in a GFM pipe table.

    Escapes ``\\`` to ``\\\\``, ``|`` to ``\\|``, and replaces newlines with
    spaces.
    """

    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_gfm_table(table: TableElement) -> list[str]:
    """Render a ``TableElement`` as a GFM markdown table with escaped cells."""

    headers = [escape_table_cell(h) for h in _get(table, "headers", []) or []]
    lines: list[str] = []
    lines.append(f"| {' | '.join(headers)} |")
    lines.append(f"| {' | '.join('---' for _ in headers)} |")
    for row in _get(table, "rows", []) or []:
        cells = [escape_table_cell(c) for c in row]
        lines.append(f"| {' | '.join(cells)} |")
    return lines


# ---------------------------------------------------------------------------
# Internal helper — read attribute or dict key (supports both shapes)
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or fall back to attribute access."""

    if isinstance(obj, dict):
        value = obj.get(key, default)
        return value if value is not None else default
    value = getattr(obj, key, default)
    return value if value is not None else default
