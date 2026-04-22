"""Google Chat-specific format conversion using AST-based parsing.

Python port of upstream ``packages/adapter-gchat/src/markdown.ts``.

Google Chat supports a subset of text formatting:

- Bold: ``*text*``
- Italic: ``_text_``
- Strikethrough: ``~text~``
- Monospace: `` `text` ``
- Code blocks: ``` ```text``` ```
- Links: ``<url|label>`` (or bare URL when label equals URL)
- Unordered list bullets: ``•``
"""

from __future__ import annotations

import re

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
    table_to_ascii,
)

# Google Chat ``*text*`` → standard markdown ``**text**``. Lookbehind/ahead
# prevent matching across emphasis markers or escaped asterisks.
_GCHAT_BOLD = re.compile(r"(?<![_*\\])\*([^*\n]+)\*(?![_*])")

# Google Chat ``~text~`` → standard markdown ``~~text~~``.
_GCHAT_STRIKETHROUGH = re.compile(r"(?<!~)~([^~\n]+)~(?!~)")


class GoogleChatFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and Google Chat text format."""

    # ------------------------------------------------------------- AST <-> GChat

    def from_ast(self, ast: MdastRoot) -> str:
        """Render a Markdown AST to Google Chat format."""

        return self._from_ast_with_node_converter(ast, self._node_to_gchat)

    def to_ast(self, platform_text: str) -> MdastRoot:
        """Parse Google Chat text into a Markdown AST.

        Rewrites Google Chat's single-marker bold/strike to standard-markdown
        double-marker form, then delegates to :func:`parse_markdown`. Italic
        (``_text_``) and code (`` `text` ``) are already standard markdown.
        """

        markdown = platform_text
        markdown = _GCHAT_BOLD.sub(r"**\1**", markdown)
        markdown = _GCHAT_STRIKETHROUGH.sub(r"~~\1~~", markdown)
        return parse_markdown(markdown)

    # ------------------------------------------------------------- node visitor

    def _node_to_gchat(self, node: MdastNode) -> str:
        """Convert a single mdast node to Google Chat format."""

        if is_paragraph_node(node):
            return "".join(self._node_to_gchat(c) for c in get_node_children(node))

        if is_text_node(node):
            # @mentions are passed through as-is — clickable mentions require
            # ``<users/{user_id}>`` syntax which needs a user ID lookup beyond
            # the scope of format conversion.
            return str(node.get("value", ""))

        if is_strong_node(node):
            # Markdown ``**text**`` → GChat ``*text*``.
            content = "".join(self._node_to_gchat(c) for c in get_node_children(node))
            return f"*{content}*"

        if is_emphasis_node(node):
            content = "".join(self._node_to_gchat(c) for c in get_node_children(node))
            return f"_{content}_"

        if is_delete_node(node):
            # Markdown ``~~text~~`` → GChat ``~text~``.
            content = "".join(self._node_to_gchat(c) for c in get_node_children(node))
            return f"~{content}~"

        if is_inline_code_node(node):
            return f"`{node.get('value', '')}`"

        if is_code_node(node):
            return f"```\n{node.get('value', '')}\n```"

        if is_link_node(node):
            link_text = "".join(self._node_to_gchat(c) for c in get_node_children(node))
            url = str(node.get("url", ""))
            if link_text == url:
                return url
            return f"<{url}|{link_text}>"

        if is_blockquote_node(node):
            return "\n".join(f"> {self._node_to_gchat(c)}" for c in get_node_children(node))

        if is_list_node(node):
            return self._render_list(node, 0, self._node_to_gchat, "•")

        node_type = node.get("type")
        if node_type == "break":
            return "\n"
        if node_type == "thematicBreak":
            return "---"

        if is_table_node(node):
            return f"```\n{table_to_ascii(node)}\n```"

        return self._default_node_to_text(node, self._node_to_gchat)


__all__ = [
    "GoogleChatFormatConverter",
]
