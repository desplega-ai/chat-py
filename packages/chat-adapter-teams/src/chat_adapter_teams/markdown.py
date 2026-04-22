"""Teams-specific format conversion using AST-based parsing.

Python port of upstream ``packages/adapter-teams/src/markdown.ts``.

Teams accepts a subset of HTML for formatting and — with
``textFormat = "markdown"`` — standard Markdown too.

- Bold: ``<b>`` / ``<strong>`` (equivalent to ``**text**``)
- Italic: ``<i>`` / ``<em>`` (equivalent to ``_text_``)
- Strikethrough: ``<s>`` / ``<strike>`` (equivalent to ``~~text~~``)
- Links: ``<a href="URL">text</a>`` (equivalent to ``[text](URL)``)
- Code: ``<code>`` / ``<pre>``
- Mentions: ``<at>Name</at>``
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from chat import (
    BaseFormatConverter,
    MdastNode,
    MdastRoot,
    get_node_children,
    is_blockquote_node,
    is_code_node,
    is_delete_node,
    is_emphasis_node,
    is_inline_code_node,
    is_link_node,
    is_list_node,
    is_paragraph_node,
    is_strong_node,
    is_table_node,
    is_text_node,
    parse_markdown,
)
from chat_adapter_shared import escape_table_cell

if TYPE_CHECKING:
    from chat import AdapterPostableMessage

# @mention → Teams <at> tag
_MENTION_PATTERN = re.compile(r"@(\w+)")
# Bold: <b>x</b>, <strong>x</strong>
_BOLD_HTML = re.compile(r"<(b|strong)>([^<]+)</(b|strong)>", re.IGNORECASE)
# Italic: <i>x</i>, <em>x</em>
_ITALIC_HTML = re.compile(r"<(i|em)>([^<]+)</(i|em)>", re.IGNORECASE)
# Strikethrough: <s>x</s>, <strike>x</strike>
_STRIKE_HTML = re.compile(r"<(s|strike)>([^<]+)</(s|strike)>", re.IGNORECASE)
# Links: <a href="url">text</a>
_LINK_HTML = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE)
# Inline code
_CODE_HTML = re.compile(r"<code>([^<]+)</code>", re.IGNORECASE)
# Preformatted code block
_PRE_HTML = re.compile(r"<pre>([^<]+)</pre>", re.IGNORECASE)
# Teams-style <at>name</at>
_AT_TAG = re.compile(r"<at>([^<]+)</at>", re.IGNORECASE)
# Remaining HTML tags
_GENERIC_TAG = re.compile(r"<[^>]+>")
# HTML entities — single-pass to prevent double-unescaping.
_HTML_ENTITY = re.compile(r"&(?:lt|gt|amp|quot|#39);")
_HTML_ENTITIES = {
    "&lt;": "<",
    "&gt;": ">",
    "&amp;": "&",
    "&quot;": '"',
    "&#39;": "'",
}


def _decode_html_entities(text: str) -> str:
    return _HTML_ENTITY.sub(lambda m: _HTML_ENTITIES.get(m.group(0), m.group(0)), text)


def _convert_mentions_to_teams(text: str) -> str:
    """Plain ``@name`` → ``<at>name</at>``."""

    return _MENTION_PATTERN.sub(r"<at>\1</at>", text)


class TeamsFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and Teams HTML/markdown."""

    # ---------------------------------------------------------------- overrides

    def render_postable(self, message: AdapterPostableMessage | str) -> str:
        """Override to apply the @mention translation to raw strings."""

        if isinstance(message, str):
            return _convert_mentions_to_teams(message)
        if isinstance(message, dict):
            if "raw" in message:
                return _convert_mentions_to_teams(message["raw"])
            if "markdown" in message:
                return self.from_ast(parse_markdown(message["markdown"]))
            if "ast" in message:
                return self.from_ast(message["ast"])
        return ""

    # -------------------------------------------------------------- AST → Teams

    def from_ast(self, ast: MdastRoot) -> str:
        """Render a Markdown AST to Teams format (GFM-compatible markdown)."""

        return self._from_ast_with_node_converter(ast, self._node_to_teams)

    # -------------------------------------------------------------- Teams → AST

    def to_ast(self, platform_text: str) -> MdastRoot:
        """Parse a Teams message (HTML + markdown) into an AST.

        Strips the HTML vocabulary, folds entities once, then delegates to the
        shared :func:`parse_markdown`.
        """

        markdown = platform_text
        markdown = _AT_TAG.sub(r"@\1", markdown)
        markdown = _BOLD_HTML.sub(r"**\2**", markdown)
        markdown = _ITALIC_HTML.sub(r"_\2_", markdown)
        markdown = _STRIKE_HTML.sub(r"~~\2~~", markdown)
        markdown = _LINK_HTML.sub(r"[\2](\1)", markdown)
        markdown = _CODE_HTML.sub(r"`\1`", markdown)
        markdown = _PRE_HTML.sub(r"```\n\1\n```", markdown)

        # Remaining HTML tags — loop to handle nested / reconstructed tags.
        prev = None
        while prev != markdown:
            prev = markdown
            markdown = _GENERIC_TAG.sub("", markdown)

        markdown = _decode_html_entities(markdown)
        return parse_markdown(markdown)

    # ----------------------------------------------------------- node visitor

    def _node_to_teams(self, node: MdastNode) -> str:
        if is_paragraph_node(node):
            return "".join(self._node_to_teams(c) for c in get_node_children(node))

        if is_text_node(node):
            return _convert_mentions_to_teams(str(node.get("value", "")))

        if is_strong_node(node):
            content = "".join(self._node_to_teams(c) for c in get_node_children(node))
            return f"**{content}**"

        if is_emphasis_node(node):
            content = "".join(self._node_to_teams(c) for c in get_node_children(node))
            return f"_{content}_"

        if is_delete_node(node):
            content = "".join(self._node_to_teams(c) for c in get_node_children(node))
            return f"~~{content}~~"

        if is_inline_code_node(node):
            return f"`{node.get('value', '')}`"

        if is_code_node(node):
            lang = str(node.get("lang") or "")
            return f"```{lang}\n{node.get('value', '')}\n```"

        if is_link_node(node):
            link_text = "".join(self._node_to_teams(c) for c in get_node_children(node))
            return f"[{link_text}]({node.get('url', '')})"

        if is_blockquote_node(node):
            return "\n".join(f"> {self._node_to_teams(c)}" for c in get_node_children(node))

        if is_list_node(node):
            return self._render_list(node, 0, self._node_to_teams)

        node_type = node.get("type")
        if node_type == "break":
            return "\n"
        if node_type == "thematicBreak":
            return "---"

        if is_table_node(node):
            return self._table_to_gfm(node)

        return self._default_node_to_text(node, self._node_to_teams)

    # ----------------------------------------------------------- table helper

    def _table_to_gfm(self, node: MdastNode) -> str:
        """Render an mdast table as a GFM pipe table (Teams renders natively)."""

        rows: list[list[str]] = []
        for row in get_node_children(node):
            cells: list[str] = []
            for cell in get_node_children(row):
                cell_content = "".join(self._node_to_teams(c) for c in get_node_children(cell))
                cells.append(cell_content)
            rows.append(cells)

        if not rows:
            return ""

        lines: list[str] = []
        lines.append("| " + " | ".join(escape_table_cell(c) for c in rows[0]) + " |")
        lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(escape_table_cell(c) for c in row) + " |")
        return "\n".join(lines)


__all__ = [
    "TeamsFormatConverter",
]
