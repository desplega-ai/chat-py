"""Telegram MarkdownV2 format conversion.

Python port of upstream ``packages/adapter-telegram/src/markdown.ts``.

Renders an mdast AST as Telegram MarkdownV2, which requires escaping
special characters outside of entities. See
https://core.telegram.org/bots/api#markdownv2-style.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Literal

from chat import (
    BaseFormatConverter,
    MdastNode,
    MdastRoot,
    get_node_children,
    get_node_value,
    is_table_node,
    parse_markdown,
    table_to_ascii,
    walk_ast,
)

if TYPE_CHECKING:
    from chat import AdapterPostableMessage

# MarkdownV2 requires escaping these characters in normal text:
# _ * [ ] ( ) ~ ` > # + - = | { } . ! \
_MARKDOWNV2_SPECIAL_CHARS = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

# Inside ``` code blocks, only ` and \ need escaping.
_CODE_BLOCK_SPECIAL_CHARS = re.compile(r"([`\\])")

# Inside (...) of inline links, only ) and \ need escaping.
_LINK_URL_SPECIAL_CHARS = re.compile(r"([)\\])")


TelegramParseMode = Literal["MarkdownV2", "plain"]
"""How the adapter intends a message to be rendered.

* ``"MarkdownV2"`` — the body was produced by the MarkdownV2 renderer and
  must be parsed by Telegram with ``parse_mode=MarkdownV2``.
* ``"plain"`` — the body ships verbatim with no markdown parsing (the Bot
  API receives no ``parse_mode`` field).
"""


def to_bot_api_parse_mode(mode: TelegramParseMode) -> str | None:
    """Translate the internal parse mode to the Bot API ``parse_mode`` field.

    Returns ``None`` for plain messages so the field is omitted.
    """

    return "MarkdownV2" if mode == "MarkdownV2" else None


# Maximum length of a Telegram text message body in characters.
TELEGRAM_MESSAGE_LIMIT = 4096

# Maximum length of a media caption (photo/document/etc.) in characters.
TELEGRAM_CAPTION_LIMIT = 1024

# Entity delimiters whose opener/closer pairing must be preserved when
# truncating a rendered MarkdownV2 string.
_MARKDOWN_V2_ENTITY_MARKERS = ("*", "_", "~", "`")

_MARKDOWN_V2_ELLIPSIS = "\\.\\.\\."
_PLAIN_ELLIPSIS = "..."


def escape_markdown_v2(text: str) -> str:
    """Escape text for use in normal MarkdownV2 context (outside entities)."""

    return _MARKDOWNV2_SPECIAL_CHARS.sub(r"\\\1", text)


def find_unescaped_positions(text: str, marker: str) -> list[int]:
    """Return indices of every occurrence of ``marker`` in ``text`` that is
    NOT preceded by an odd number of backslashes (i.e. not escaped).
    """

    positions: list[int] = []
    for i, char in enumerate(text):
        if char != marker:
            continue
        backslashes = 0
        j = i - 1
        while j >= 0 and text[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            positions.append(i)
    return positions


def ends_with_orphan_backslash(text: str) -> bool:
    """Return ``True`` if ``text`` ends with an unpaired trailing backslash."""

    trailing = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] != "\\":
            break
        trailing += 1
    return trailing % 2 == 1


def _trim_to_markdown_v2_safe_boundary(text: str) -> str:
    """Drop trailing characters that would produce invalid MarkdownV2 after
    a length-based truncation: orphan trailing ``\\``, unclosed entity
    delimiter (``*``, ``_``, ``~``, `` ` ``), or unmatched ``[``.
    """

    current = text
    max_iterations = len(current) + 1

    for _ in range(max_iterations):
        if ends_with_orphan_backslash(current):
            current = current[:-1]
            continue

        min_unsafe_position = len(current)

        for marker in _MARKDOWN_V2_ENTITY_MARKERS:
            positions = find_unescaped_positions(current, marker)
            if len(positions) % 2 == 1:
                last_unpaired = positions[-1] if positions else len(current)
                if last_unpaired < min_unsafe_position:
                    min_unsafe_position = last_unpaired

        open_brackets = find_unescaped_positions(current, "[")
        close_brackets = find_unescaped_positions(current, "]")
        if len(open_brackets) > len(close_brackets):
            last_open = open_brackets[-1] if open_brackets else len(current)
            if last_open < min_unsafe_position:
                min_unsafe_position = last_open

        if min_unsafe_position >= len(current):
            return current

        current = current[:min_unsafe_position]

    return current


def truncate_for_telegram(text: str, limit: int, parse_mode: TelegramParseMode) -> str:
    """Truncate ``text`` to ``limit`` chars and append an ellipsis.

    For MarkdownV2, a naive slice + ``"..."`` is unsafe: ``.`` is reserved
    and must be escaped, and the slice can leave orphan escape characters
    (``\\``) or cut through a paired entity (``*bold*``, `` `code` ``)
    resulting in ``Bad Request: can't parse entities``. This function uses
    an escaped ellipsis (``\\.\\.\\.``) and trims back past any unbalanced
    entity delimiter or orphan backslash before appending.
    """

    if len(text) <= limit:
        return text

    is_markdown_v2 = parse_mode == "MarkdownV2"
    ellipsis = _MARKDOWN_V2_ELLIPSIS if is_markdown_v2 else _PLAIN_ELLIPSIS
    slice_text = text[: limit - len(ellipsis)]

    if is_markdown_v2:
        slice_text = _trim_to_markdown_v2_safe_boundary(slice_text)

    return f"{slice_text}{ellipsis}"


def _escape_code_block(text: str) -> str:
    """Escape text inside code/pre blocks (only ` and \\ need escaping)."""

    return _CODE_BLOCK_SPECIAL_CHARS.sub(r"\\\1", text)


def _escape_link_url(text: str) -> str:
    """Escape text inside link URLs (only ) and \\ need escaping)."""

    return _LINK_URL_SPECIAL_CHARS.sub(r"\\\1", text)


def _render_children(node: MdastNode, joiner: str = "") -> str:
    return joiner.join(_render_markdown_v2(c) for c in get_node_children(node))


def _render_markdown_v2(node: MdastNode) -> str:
    """Recursively render an mdast node as Telegram MarkdownV2 text."""

    ntype = node.get("type")

    if ntype == "root":
        return "\n\n".join(_render_markdown_v2(c) for c in get_node_children(node))

    if ntype == "paragraph":
        return _render_children(node)

    if ntype == "text":
        return escape_markdown_v2(get_node_value(node))

    if ntype == "strong":
        return f"*{_render_children(node)}*"

    if ntype == "emphasis":
        return f"_{_render_children(node)}_"

    if ntype == "delete":
        return f"~{_render_children(node)}~"

    if ntype == "inlineCode":
        return f"`{_escape_code_block(get_node_value(node))}`"

    if ntype == "code":
        lang = node.get("lang") or ""
        val = _escape_code_block(get_node_value(node))
        return f"```{lang}\n{val}\n```"

    if ntype == "link":
        link_text = _render_children(node)
        url = _escape_link_url(str(node.get("url", "")))
        return f"[{link_text}]({url})"

    if ntype == "blockquote":
        inner = "\n".join(_render_markdown_v2(c) for c in get_node_children(node))
        return "\n".join(f">{line}" for line in inner.split("\n"))

    if ntype == "list":
        ordered = bool(node.get("ordered", False))
        parts: list[str] = []
        for i, item in enumerate(get_node_children(node)):
            content = "\n".join(_render_markdown_v2(c) for c in get_node_children(item))
            if ordered:
                parts.append(f"{escape_markdown_v2(f'{i + 1}.')} {content}")
            else:
                parts.append(f"\\- {content}")
        return "\n".join(parts)

    if ntype == "listItem":
        return "\n".join(_render_markdown_v2(c) for c in get_node_children(node))

    if ntype == "heading":
        text_out = _render_children(node)
        return f"*{text_out}*"

    if ntype == "thematicBreak":
        return escape_markdown_v2("———")

    if ntype == "break":
        return "\n"

    if ntype == "image":
        alt = escape_markdown_v2(str(node.get("alt") or ""))
        url = _escape_link_url(str(node.get("url") or ""))
        return f"[{alt}]({url})"

    if ntype == "html":
        # Telegram MarkdownV2 rejects raw HTML; escape so it renders literally.
        return escape_markdown_v2(get_node_value(node))

    if ntype in ("linkReference", "imageReference"):
        children = get_node_children(node)
        if children:
            return "".join(_render_markdown_v2(c) for c in children)
        label = node.get("label") or node.get("identifier") or ""
        return escape_markdown_v2(str(label))

    if ntype in ("definition", "footnoteDefinition", "yaml"):
        return ""

    if ntype == "footnoteReference":
        label = node.get("label") or node.get("identifier") or ""
        return escape_markdown_v2(f"[^{label}]")

    if ntype in ("table", "tableRow", "tableCell"):
        raise ValueError(
            f"Telegram MarkdownV2 renderer received a {ntype} node; "
            "from_ast should have preprocessed it into a code block.",
        )

    # Unhandled / unknown node — fall back to recursing children or returning value.
    children = get_node_children(node)
    if children:
        return "".join(_render_markdown_v2(c) for c in children)
    return escape_markdown_v2(get_node_value(node))


class TelegramFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and Telegram MarkdownV2."""

    def from_ast(self, ast: MdastRoot) -> str:
        """Render an AST to MarkdownV2 text, after folding tables into code blocks."""

        def _rewrite(node: MdastNode) -> MdastNode:
            if is_table_node(node):
                return {
                    "type": "code",
                    "value": table_to_ascii(node),
                    "lang": None,
                }
            return node

        transformed = walk_ast(copy.deepcopy(ast), _rewrite)
        return _render_markdown_v2(transformed).strip()

    def to_ast(self, platform_text: str) -> MdastRoot:
        return parse_markdown(platform_text)

    def render_postable(self, message: AdapterPostableMessage | str) -> str:
        """Handle raw strings / markdown / ast directly; fall back to the base.

        Plain strings and ``{raw: ...}`` messages ship verbatim. Everything
        else flows through the MarkdownV2 renderer.
        """

        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            if "raw" in message:
                return str(message["raw"])
            if "markdown" in message:
                return self.from_markdown(str(message["markdown"]))
            if "ast" in message:
                return self.from_ast(message["ast"])
        return super().render_postable(message)


__all__ = [
    "TELEGRAM_CAPTION_LIMIT",
    "TELEGRAM_MESSAGE_LIMIT",
    "TelegramFormatConverter",
    "TelegramParseMode",
    "ends_with_orphan_backslash",
    "escape_markdown_v2",
    "find_unescaped_positions",
    "to_bot_api_parse_mode",
    "truncate_for_telegram",
]
