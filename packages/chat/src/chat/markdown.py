"""Markdown parsing and conversion utilities — Python port of ``packages/chat/src/markdown.ts``.

Upstream uses ``unified`` + ``remark-parse`` + ``remark-gfm`` + ``remark-stringify``
with an ``mdast`` AST. We parse with :mod:`mistune` (GFM via ``strikethrough`` +
``table`` plugins) and translate mistune's dict output to the same mdast shape —
``{"type": ..., "children"?: [...], "value"?: str, ...}``. The stringifier is
hand-rolled because no stable Python equivalent of ``remark-stringify`` exists,
and we need the output dict shape to round-trip cross-language.

mdast reference: https://github.com/syntax-tree/mdast
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, NotRequired, Protocol, TypedDict

import mistune

MdastNode = dict[str, Any]
"""An mdast AST node — plain ``dict`` with a ``type`` discriminator."""

MdastRoot = dict[str, Any]
"""An mdast root node — ``{"type": "root", "children": [...]}``."""


# ============================================================================
# Type guards for mdast nodes
# ============================================================================


def is_text_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a text node."""
    return node.get("type") == "text"


def is_paragraph_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a paragraph node."""
    return node.get("type") == "paragraph"


def is_strong_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a strong (bold) node."""
    return node.get("type") == "strong"


def is_emphasis_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is an emphasis (italic) node."""
    return node.get("type") == "emphasis"


def is_delete_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a delete (strikethrough) node."""
    return node.get("type") == "delete"


def is_inline_code_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is an inline code node."""
    return node.get("type") == "inlineCode"


def is_code_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a code block node."""
    return node.get("type") == "code"


def is_link_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a link node."""
    return node.get("type") == "link"


def is_blockquote_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a blockquote node."""
    return node.get("type") == "blockquote"


def is_list_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a list node."""
    return node.get("type") == "list"


def is_list_item_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a list item node."""
    return node.get("type") == "listItem"


def is_table_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a table node."""
    return node.get("type") == "table"


def is_table_row_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a table row node."""
    return node.get("type") == "tableRow"


def is_table_cell_node(node: MdastNode) -> bool:
    """Return ``True`` if ``node`` is a table cell node."""
    return node.get("type") == "tableCell"


# ============================================================================
# Helpers for accessing node properties type-safely
# ============================================================================


def get_node_children(node: MdastNode) -> list[MdastNode]:
    """Return ``node.children`` or ``[]`` if not present."""
    children = node.get("children")
    if isinstance(children, list):
        return children
    return []


def get_node_value(node: MdastNode) -> str:
    """Return ``node.value`` or ``""`` if not a string."""
    value = node.get("value")
    if isinstance(value, str):
        return value
    return ""


# ============================================================================
# Table rendering
# ============================================================================


def table_to_ascii(node: MdastNode) -> str:
    """Render an mdast table node as a padded ASCII table string.

    Produces output like::

        Name  | Age | Role
        ------|-----|--------
        Alice | 30  | Engineer

    Used by adapters that lack native table support.
    """
    rows: list[list[str]] = []
    for row in get_node_children(node):
        cells = [to_plain_text(cell) for cell in get_node_children(row)]
        rows.append(cells)

    if not rows:
        return ""
    headers = rows[0]
    data_rows = rows[1:]
    return table_element_to_ascii(headers, data_rows)


def table_element_to_ascii(headers: list[str], rows: list[list[str]]) -> str:
    """Render headers + rows as a padded ASCII table. Used for card ``TableElement`` fallback."""
    all_rows = [headers, *rows]
    col_count = max((len(r) for r in all_rows), default=0)
    if col_count == 0:
        return ""

    col_widths = [0] * col_count
    for row in all_rows:
        for i in range(col_count):
            cell = row[i] if i < len(row) else ""
            if len(cell) > col_widths[i]:
                col_widths[i] = len(cell)

    def format_row(cells: list[str]) -> str:
        padded = [
            (cells[i] if i < len(cells) else "").ljust(col_widths[i]) for i in range(col_count)
        ]
        return " | ".join(padded).rstrip()

    lines: list[str] = []
    lines.append(format_row(headers))
    lines.append("-|-".join("-" * w for w in col_widths))
    for row in rows:
        lines.append(format_row(row))
    return "\n".join(lines)


# ============================================================================
# Parse: mistune → mdast
# ============================================================================


def _create_parser() -> Callable[[str], list[MdastNode]]:
    parser = mistune.create_markdown(renderer="ast", plugins=["strikethrough", "table"])

    def parse(markdown: str) -> list[MdastNode]:
        result = parser(markdown)
        if isinstance(result, list):
            return result
        # Some mistune modes return (tokens, state) tuple
        if isinstance(result, tuple) and result and isinstance(result[0], list):
            return result[0]
        return []

    return parse


_parse_mistune = _create_parser()


def parse_markdown(markdown: str) -> MdastRoot:
    """Parse a markdown string into an mdast :class:`MdastRoot` dict.

    Supports GFM (strikethrough, tables) — same as upstream ``remark-gfm``.
    """
    mistune_nodes = _parse_mistune(markdown)
    children: list[MdastNode] = []
    for node in mistune_nodes:
        converted = _mistune_to_mdast(node)
        if converted is not None:
            children.append(converted)
    return {"type": "root", "children": children}


def _mistune_to_mdast(node: MdastNode) -> MdastNode | None:
    """Convert a single mistune AST dict to an mdast dict."""
    t = node.get("type")
    if t == "blank_line":
        return None
    if t == "text":
        return {"type": "text", "value": node.get("raw", "")}
    if t == "strong":
        return {"type": "strong", "children": _convert_children(node)}
    if t == "emphasis":
        return {"type": "emphasis", "children": _convert_children(node)}
    if t == "strikethrough":
        return {"type": "delete", "children": _convert_children(node)}
    if t == "codespan":
        return {"type": "inlineCode", "value": node.get("raw", "")}
    if t == "block_code":
        attrs = node.get("attrs") or {}
        info = attrs.get("info") or ""
        raw = node.get("raw", "")
        if raw.endswith("\n"):
            raw = raw[:-1]
        result: MdastNode = {"type": "code", "value": raw}
        if info:
            parts = info.split(None, 1)
            result["lang"] = parts[0]
            if len(parts) > 1:
                result["meta"] = parts[1]
            else:
                result["meta"] = None
        else:
            result["lang"] = None
            result["meta"] = None
        return result
    if t == "paragraph":
        return {"type": "paragraph", "children": _convert_children(node)}
    if t == "block_quote":
        return {"type": "blockquote", "children": _convert_children(node)}
    if t == "list":
        attrs = node.get("attrs") or {}
        ordered = bool(attrs.get("ordered", False))
        result = {
            "type": "list",
            "ordered": ordered,
            "start": attrs.get("start") if ordered else None,
            "spread": not bool(node.get("tight", True)),
            "children": _convert_children(node),
        }
        return result
    if t == "list_item":
        return {
            "type": "listItem",
            "spread": False,
            "checked": None,
            "children": _convert_children(node),
        }
    if t == "block_text":
        # mistune wraps list-item inline content in block_text; mdast uses paragraph.
        return {"type": "paragraph", "children": _convert_children(node)}
    if t == "heading":
        attrs = node.get("attrs") or {}
        return {
            "type": "heading",
            "depth": int(attrs.get("level", 1)),
            "children": _convert_children(node),
        }
    if t == "thematic_break":
        return {"type": "thematicBreak"}
    if t == "link":
        attrs = node.get("attrs") or {}
        return {
            "type": "link",
            "url": attrs.get("url", ""),
            "title": attrs.get("title"),
            "children": _convert_children(node),
        }
    if t == "image":
        attrs = node.get("attrs") or {}
        return {
            "type": "image",
            "url": attrs.get("url", ""),
            "title": attrs.get("title"),
            "alt": _inline_to_plain_text(node.get("children") or []),
        }
    if t == "table":
        return _convert_table(node)
    if t == "softbreak":
        return {"type": "text", "value": "\n"}
    if t == "linebreak":
        return {"type": "break"}
    if t in ("block_html", "inline_html"):
        return {"type": "html", "value": node.get("raw", "")}
    return None


def _convert_children(node: MdastNode) -> list[MdastNode]:
    out: list[MdastNode] = []
    for child in node.get("children") or []:
        converted = _mistune_to_mdast(child)
        if converted is not None:
            out.append(converted)
    return out


def _inline_to_plain_text(nodes: list[MdastNode]) -> str:
    """Concatenate raw text from mistune inline nodes — used for image alt."""
    parts: list[str] = []
    for n in nodes:
        if isinstance(n.get("raw"), str):
            parts.append(n["raw"])
        elif isinstance(n.get("children"), list):
            parts.append(_inline_to_plain_text(n["children"]))
    return "".join(parts)


def _convert_table(node: MdastNode) -> MdastNode:
    """Flatten mistune's ``table_head``/``table_body`` into a single ``tableRow`` list."""
    rows: list[MdastNode] = []
    align: list[str | None] = []
    for child in node.get("children") or []:
        ct = child.get("type")
        if ct == "table_head":
            rows.append(_convert_table_row(child))
            for cell in child.get("children") or []:
                align.append((cell.get("attrs") or {}).get("align"))
        elif ct == "table_body":
            for row in child.get("children") or []:
                rows.append(_convert_table_row(row))
    return {"type": "table", "align": align, "children": rows}


def _convert_table_row(row: MdastNode) -> MdastNode:
    cells: list[MdastNode] = []
    for cell in row.get("children") or []:
        cells.append({"type": "tableCell", "children": _convert_children(cell)})
    return {"type": "tableRow", "children": cells}


# ============================================================================
# Stringify: mdast → markdown
# ============================================================================


class StringifyOptions(TypedDict, total=False):
    """Options for :func:`stringify_markdown`."""

    bullet: str  # ``*``, ``-``, or ``+``. Default ``*``.
    emphasis: str  # ``*`` or ``_``. Default ``*``.


def stringify_markdown(ast: MdastRoot, options: StringifyOptions | None = None) -> str:
    """Stringify an mdast :class:`MdastRoot` back to markdown."""
    opts = options or {}
    bullet = opts.get("bullet", "*")
    emphasis_marker = opts.get("emphasis", "*")
    text = _stringify_node(ast, bullet, emphasis_marker)
    # remark-stringify always ends with a single newline.
    if not text.endswith("\n"):
        text += "\n"
    return text


def _stringify_node(node: MdastNode, bullet: str, em: str) -> str:
    t = node.get("type")
    if t == "root":
        return _join_blocks(node.get("children") or [], bullet, em)
    if t == "text":
        return node.get("value", "")
    if t == "paragraph":
        return _stringify_inline(node, bullet, em)
    if t == "strong":
        return f"**{_stringify_inline(node, bullet, em)}**"
    if t == "emphasis":
        return f"{em}{_stringify_inline(node, bullet, em)}{em}"
    if t == "delete":
        return f"~~{_stringify_inline(node, bullet, em)}~~"
    if t == "inlineCode":
        return f"`{node.get('value', '')}`"
    if t == "code":
        lang = node.get("lang") or ""
        meta = node.get("meta")
        info = lang
        if meta:
            info = f"{lang} {meta}" if lang else meta
        value = node.get("value", "")
        return f"```{info}\n{value}\n```"
    if t == "link":
        inner = _stringify_inline(node, bullet, em)
        url = node.get("url", "")
        title = node.get("title")
        if title:
            return f'[{inner}]({url} "{title}")'
        return f"[{inner}]({url})"
    if t == "image":
        url = node.get("url", "")
        title = node.get("title")
        alt = node.get("alt") or ""
        if title:
            return f'![{alt}]({url} "{title}")'
        return f"![{alt}]({url})"
    if t == "blockquote":
        inner = _join_blocks(node.get("children") or [], bullet, em)
        return "\n".join(("> " + line) if line else ">" for line in inner.split("\n"))
    if t == "list":
        return _stringify_list(node, bullet, em, depth=0)
    if t == "heading":
        depth = int(node.get("depth", 1))
        return ("#" * depth) + " " + _stringify_inline(node, bullet, em)
    if t == "thematicBreak":
        return "***"
    if t == "break":
        return "\\\n"
    if t == "html":
        return node.get("value", "")
    if t == "table":
        return _stringify_table(node, bullet, em)
    # Unknown node type — fall back to concatenating children text.
    if isinstance(node.get("children"), list):
        return _stringify_inline(node, bullet, em)
    return node.get("value", "") or ""


def _stringify_inline(node: MdastNode, bullet: str, em: str) -> str:
    return "".join(_stringify_node(c, bullet, em) for c in node.get("children") or [])


def _join_blocks(nodes: list[MdastNode], bullet: str, em: str) -> str:
    return "\n\n".join(_stringify_node(c, bullet, em) for c in nodes)


def _stringify_list(node: MdastNode, bullet: str, em: str, depth: int) -> str:
    ordered = bool(node.get("ordered", False))
    start_raw = node.get("start")
    start = start_raw if isinstance(start_raw, int) else 1
    indent = "  " * depth
    lines: list[str] = []
    for i, item in enumerate(node.get("children") or []):
        prefix = f"{start + i}." if ordered else bullet
        item_blocks: list[str] = []
        for child in item.get("children") or []:
            if child.get("type") == "list":
                item_blocks.append(_stringify_list(child, bullet, em, depth + 1))
            else:
                item_blocks.append(_stringify_node(child, bullet, em))
        item_text = "\n\n".join(b for b in item_blocks if b != "")
        item_lines = item_text.split("\n") if item_text else [""]
        first_prefix = f"{indent}{prefix} "
        continuation_prefix = f"{indent}{' ' * (len(prefix) + 1)}"
        formatted: list[str] = [first_prefix + item_lines[0]]
        for line in item_lines[1:]:
            formatted.append((continuation_prefix + line) if line else "")
        lines.append("\n".join(formatted))
    return "\n".join(lines)


def _stringify_table(node: MdastNode, bullet: str, em: str) -> str:
    rows = node.get("children") or []
    if not rows:
        return ""
    rendered_rows: list[list[str]] = []
    for row in rows:
        rendered_rows.append(
            [_stringify_inline(cell, bullet, em) for cell in row.get("children") or []]
        )
    col_count = max((len(r) for r in rendered_rows), default=0)
    if col_count == 0:
        return ""
    widths = [0] * col_count
    for row in rendered_rows:
        for i in range(col_count):
            cell = row[i] if i < len(row) else ""
            widths[i] = max(widths[i], len(cell))
    align = node.get("align") or []

    def fmt_cell(s: str, w: int, i: int) -> str:
        a = align[i] if i < len(align) else None
        if a == "center":
            total = w - len(s)
            left = total // 2
            right = total - left
            return " " * left + s + " " * right
        if a == "right":
            return s.rjust(w)
        return s.ljust(w)

    def fmt_row(cells: list[str]) -> str:
        return (
            "| "
            + " | ".join(
                fmt_cell(cells[i] if i < len(cells) else "", widths[i], i) for i in range(col_count)
            )
            + " |"
        )

    def fmt_sep() -> str:
        segs = []
        for i in range(col_count):
            w = max(widths[i], 3)
            a = align[i] if i < len(align) else None
            if a == "center":
                segs.append(":" + "-" * (w - 2) + ":")
            elif a == "right":
                segs.append("-" * (w - 1) + ":")
            elif a == "left":
                segs.append(":" + "-" * (w - 1))
            else:
                segs.append("-" * w)
        return "| " + " | ".join(segs) + " |"

    out = [fmt_row(rendered_rows[0]), fmt_sep()]
    for row in rendered_rows[1:]:
        out.append(fmt_row(row))
    return "\n".join(out)


# ============================================================================
# Plain text extraction
# ============================================================================


def to_plain_text(ast: MdastNode) -> str:
    """Extract plain text from an mdast node (strips all formatting).

    Mirrors ``mdast-util-to-string``: if the node has a string ``value``,
    return it; otherwise recurse into ``children`` and concatenate.
    """
    return _node_to_plain_text(ast)


def _node_to_plain_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    value = node.get("value")
    if isinstance(value, str):
        return value
    children = node.get("children")
    if isinstance(children, list):
        return "".join(_node_to_plain_text(c) for c in children)
    return ""


def markdown_to_plain_text(markdown: str) -> str:
    """Extract plain text directly from a markdown string."""
    return to_plain_text(parse_markdown(markdown))


# ============================================================================
# AST walker
# ============================================================================


def walk_ast(
    node: MdastNode,
    visitor: Callable[[MdastNode], MdastNode | None],
) -> MdastNode:
    """Walk the AST, invoking ``visitor`` on each descendant node.

    Mutates ``node.children`` in place. The visitor can return ``None`` to drop
    a node, a new dict to replace it, or the same dict to keep it as-is.

    Note: matches upstream — the visitor is called on children of ``node``,
    but not on ``node`` itself.
    """
    children = node.get("children")
    if isinstance(children, list):
        new_children: list[MdastNode] = []
        for child in children:
            result = visitor(child)
            if result is None:
                continue
            new_children.append(walk_ast(result, visitor))
        node["children"] = new_children
    return node


# ============================================================================
# AST builders
# ============================================================================


def text(value: str) -> MdastNode:
    """Create a text node."""
    return {"type": "text", "value": value}


def strong(children: list[MdastNode]) -> MdastNode:
    """Create a strong (bold) node."""
    return {"type": "strong", "children": list(children)}


def emphasis(children: list[MdastNode]) -> MdastNode:
    """Create an emphasis (italic) node."""
    return {"type": "emphasis", "children": list(children)}


def strikethrough(children: list[MdastNode]) -> MdastNode:
    """Create a delete (strikethrough) node."""
    return {"type": "delete", "children": list(children)}


def inline_code(value: str) -> MdastNode:
    """Create an inline code node."""
    return {"type": "inlineCode", "value": value}


def code_block(value: str, lang: str | None = None, meta: str | None = None) -> MdastNode:
    """Create a code block node."""
    return {"type": "code", "value": value, "lang": lang, "meta": meta}


def link(url: str, children: list[MdastNode], title: str | None = None) -> MdastNode:
    """Create a link node."""
    return {"type": "link", "url": url, "title": title, "children": list(children)}


def blockquote(children: list[MdastNode]) -> MdastNode:
    """Create a blockquote node."""
    return {"type": "blockquote", "children": list(children)}


def paragraph(children: list[MdastNode]) -> MdastNode:
    """Create a paragraph node."""
    return {"type": "paragraph", "children": list(children)}


def root(children: list[MdastNode]) -> MdastRoot:
    """Create a root node."""
    return {"type": "root", "children": list(children)}


# ============================================================================
# Postable-message shapes (forward refs for ``FormatConverter``)
# ============================================================================


class _RawPostable(TypedDict):
    raw: str


class _MarkdownPostable(TypedDict):
    markdown: str


class _AstPostable(TypedDict):
    ast: MdastRoot


class _CardPostable(TypedDict):
    card: Any  # CardElement — resolved in part B
    fallbackText: NotRequired[str]


# ``str | {raw} | {markdown} | {ast} | {card, fallbackText?} | CardElement``
PostableMessageInput = Any
"""Loose alias — upstream's ``AdapterPostableMessage`` union. Refined in part B."""


# ============================================================================
# FormatConverter protocol + base implementation
# ============================================================================


class FormatConverter(Protocol):
    """Interface for platform-specific format converters.

    The AST (:class:`MdastRoot`) is canonical. Conversions flow::

        Platform Format <-> AST <-> Markdown String

    Adapters implement this to convert between their platform-specific text
    format (Slack mrkdwn, Discord markdown, etc.) and the standard mdast AST.
    """

    def extract_plain_text(self, platform_text: str) -> str: ...

    def from_ast(self, ast: MdastRoot) -> str: ...

    def to_ast(self, platform_text: str) -> MdastRoot: ...


class MarkdownConverter(FormatConverter, Protocol):
    """Deprecated — use :class:`FormatConverter`. Retained for adapter compatibility."""

    def from_markdown(self, markdown: str) -> str: ...

    def to_markdown(self, platform_text: str) -> str: ...

    def to_plain_text(self, platform_text: str) -> str: ...


class BaseFormatConverter(ABC):
    """Abstract base class implementing :class:`FormatConverter` conveniences."""

    @abstractmethod
    def from_ast(self, ast: MdastRoot) -> str: ...

    @abstractmethod
    def to_ast(self, platform_text: str) -> MdastRoot: ...

    def _render_list(
        self,
        node: MdastNode,
        depth: int,
        node_converter: Callable[[MdastNode], str],
        unordered_bullet: str = "-",
    ) -> str:
        """Default rendering helper for adapters lacking native list formatting."""
        indent = "  " * depth
        start_raw = node.get("start")
        start = start_raw if isinstance(start_raw, int) else 1
        ordered = bool(node.get("ordered", False))
        lines: list[str] = []
        for i, item in enumerate(get_node_children(node)):
            prefix = f"{start + i}." if ordered else unordered_bullet
            is_first = True
            for child in get_node_children(item):
                if is_list_node(child):
                    lines.append(
                        self._render_list(child, depth + 1, node_converter, unordered_bullet)
                    )
                    continue
                text_out = node_converter(child)
                if not text_out.strip():
                    continue
                if is_first:
                    lines.append(f"{indent}{prefix} {text_out}")
                    is_first = False
                else:
                    lines.append(f"{indent}  {text_out}")
        return "\n".join(lines)

    def _default_node_to_text(
        self,
        node: MdastNode,
        node_converter: Callable[[MdastNode], str],
    ) -> str:
        """Fallback conversion — recurse into children or return the node value."""
        children = get_node_children(node)
        if children:
            return "".join(node_converter(c) for c in children)
        return get_node_value(node)

    def _from_ast_with_node_converter(
        self,
        ast: MdastRoot,
        node_converter: Callable[[MdastNode], str],
    ) -> str:
        """Template method — convert each top-level child and join with ``\\n\\n``."""
        parts = [node_converter(n) for n in get_node_children(ast)]
        return "\n\n".join(parts)

    def extract_plain_text(self, platform_text: str) -> str:
        return to_plain_text(self.to_ast(platform_text))

    def from_markdown(self, markdown: str) -> str:
        return self.from_ast(parse_markdown(markdown))

    def to_markdown(self, platform_text: str) -> str:
        return stringify_markdown(self.to_ast(platform_text))

    def to_plain_text(self, platform_text: str) -> str:
        """Deprecated — use :meth:`extract_plain_text`."""
        return self.extract_plain_text(platform_text)

    def render_postable(self, message: PostableMessageInput) -> str:
        """Render a postable message to a platform-format string.

        Supports:

        - ``str`` — passed through as-is
        - ``{"raw": str}`` — passed through
        - ``{"markdown": str}`` — converted via :meth:`from_markdown`
        - ``{"ast": MdastRoot}`` — converted via :meth:`from_ast`
        - ``{"card": CardElement, "fallbackText"?: str}`` — uses fallback text
          or :meth:`_card_to_fallback_text`
        - ``CardElement`` (direct) — uses :meth:`_card_to_fallback_text`
        """
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            if "raw" in message:
                return message["raw"]
            if "markdown" in message:
                return self.from_markdown(message["markdown"])
            if "ast" in message:
                return self.from_ast(message["ast"])
            if "card" in message:
                fallback = message.get("fallbackText") or message.get("fallback_text")
                if fallback:
                    return fallback
                return self._card_to_fallback_text(message["card"])
            if message.get("type") == "card":
                return self._card_to_fallback_text(message)
        # Object-style CardElement (has a .type attribute).
        if getattr(message, "type", None) == "card":
            return self._card_to_fallback_text(message)
        raise ValueError("Invalid PostableMessage format")

    def _card_to_fallback_text(self, card: Any) -> str:
        """Generate fallback text from a card element.

        Part-B note: the full ``CardElement`` type lands with ``cards.py``.
        This stub handles the common dict shape ``{type: "card", title?, subtitle?, children?}``
        and delegates to :meth:`_card_child_to_fallback_text` per child.
        """
        parts: list[str] = []
        title = _get(card, "title")
        if title:
            parts.append(f"**{title}**")
        subtitle = _get(card, "subtitle")
        if subtitle:
            parts.append(str(subtitle))
        for child in _get(card, "children") or []:
            rendered = self._card_child_to_fallback_text(child)
            if rendered:
                parts.append(rendered)
        return "\n".join(parts)

    def _card_child_to_fallback_text(self, child: Any) -> str | None:
        """Convert a card child element to fallback text — see ``cards.ts``."""
        t = _get(child, "type")
        if t == "text":
            return str(_get(child, "content") or "")
        if t == "fields":
            fields = _get(child, "children") or []
            return "\n".join(f"**{_get(f, 'label')}**: {_get(f, 'value')}" for f in fields)
        if t == "actions":
            # Interactive-only — omitted from fallback text.
            return None
        if t == "table":
            return table_element_to_ascii(_get(child, "headers") or [], _get(child, "rows") or [])
        if t == "section":
            rendered = [
                self._card_child_to_fallback_text(c) for c in (_get(child, "children") or [])
            ]
            return "\n".join(r for r in rendered if r)
        return None


def _get(obj: Any, key: str) -> Any:
    """Get an attribute/key from a dict-or-object — used for dual-shape card support."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
