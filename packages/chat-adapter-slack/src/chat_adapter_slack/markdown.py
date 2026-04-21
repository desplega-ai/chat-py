"""Slack mrkdwn format conversion using AST-based parsing.

Python port of upstream ``packages/adapter-slack/src/markdown.ts``.

Slack "mrkdwn" is similar but not identical to standard Markdown:

- Bold: ``*text*`` (not ``**text**``)
- Italic: ``_text_`` (same)
- Strikethrough: ``~text~`` (not ``~~text~~``)
- Links: ``<url|text>`` (not ``[text](url)``)
- User mentions: ``<@U123>``
- Channel mentions: ``<#C123|name>``
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from chat import (
    AdapterPostableMessage,
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

if TYPE_CHECKING:
    from .cards import SlackBlock

# Match bare @mentions (e.g. ``@george``) to rewrite as Slack's ``<@george>``.
# The lookbehind excludes ``<`` (already-formatted mentions like ``<@U123>``) and
# any word character, so email addresses like ``user@example.com`` are left alone.
_BARE_MENTION_REGEX = re.compile(r"(?<![<\w])@(\w+)")

# Slack→markdown conversion regexes (used by :meth:`to_ast`).
_USER_MENTION_WITH_NAME = re.compile(r"<@([A-Z0-9_]+)\|([^<>]+)>")
_USER_MENTION = re.compile(r"<@([A-Z0-9_]+)>")
_CHANNEL_MENTION_WITH_NAME = re.compile(r"<#[A-Z0-9_]+\|([^<>]+)>")
_CHANNEL_MENTION = re.compile(r"<#([A-Z0-9_]+)>")
_LINK_WITH_TEXT = re.compile(r"<(https?://[^|<>]+)\|([^<>]+)>")
_BARE_LINK = re.compile(r"<(https?://[^<>]+)>")
_BOLD = re.compile(r"(?<![_*\\])\*([^*\n]+)\*(?![_*])")
_STRIKETHROUGH = re.compile(r"(?<!~)~([^~\n]+)~(?!~)")


class SlackFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and Slack mrkdwn."""

    # ------------------------------------------------------------------ helpers

    def _convert_mentions_to_slack(self, text: str) -> str:
        """Rewrite bare ``@mentions`` to Slack's ``<@mention>`` format."""

        return _BARE_MENTION_REGEX.sub(r"<@\1>", text)

    # ------------------------------------------------------------------ render

    def render_postable(self, message: AdapterPostableMessage) -> str:  # type: ignore[override]
        """Render a postable message to Slack mrkdwn.

        - ``str`` / ``{raw: str}`` — bare @mentions rewritten, otherwise untouched.
        - ``{markdown: str}`` — parsed as markdown then rendered via :meth:`from_ast`.
        - ``{ast: Root}`` — rendered directly via :meth:`from_ast`.
        """

        if isinstance(message, str):
            return self._convert_mentions_to_slack(message)
        if isinstance(message, dict):
            if "raw" in message:
                return self._convert_mentions_to_slack(message["raw"])
            if "markdown" in message:
                return self.from_ast(parse_markdown(message["markdown"]))
            if "ast" in message:
                return self.from_ast(message["ast"])
        return ""

    # ----------------------------------------------------------- AST <-> mrkdwn

    def from_ast(self, ast: MdastRoot) -> str:
        """Render a Markdown AST to Slack mrkdwn."""

        return self._from_ast_with_node_converter(ast, self._node_to_mrkdwn)

    def to_ast(self, platform_text: str) -> MdastRoot:
        """Parse Slack mrkdwn into a Markdown AST.

        We convert the Slack-specific syntax back to standard markdown via
        string substitutions, then delegate to :func:`parse_markdown`.
        """

        markdown = platform_text

        # User mentions: <@U123|name> -> @name, <@U123> -> @U123
        markdown = _USER_MENTION_WITH_NAME.sub(r"@\2", markdown)
        markdown = _USER_MENTION.sub(r"@\1", markdown)

        # Channel mentions: <#C123|name> -> #name, <#C123> -> #C123
        markdown = _CHANNEL_MENTION_WITH_NAME.sub(r"#\1", markdown)
        markdown = _CHANNEL_MENTION.sub(r"#\1", markdown)

        # Links: <url|text> -> [text](url), <url> -> url
        markdown = _LINK_WITH_TEXT.sub(r"[\2](\1)", markdown)
        markdown = _BARE_LINK.sub(r"\1", markdown)

        # Bold: *text* -> **text** (but not emphasis; negative lookbehind/ahead)
        markdown = _BOLD.sub(r"**\1**", markdown)

        # Strikethrough: ~text~ -> ~~text~~
        markdown = _STRIKETHROUGH.sub(r"~~\1~~", markdown)

        return parse_markdown(markdown)

    # ---------------------------------------------------------------- blocks

    def to_blocks_with_table(self, ast: MdastRoot) -> list[SlackBlock] | None:
        """Convert an AST to Slack blocks when it contains at least one table.

        Returns ``None`` if the AST has no tables — callers should fall back to a
        plain text message. Slack allows a single ``table`` block per message;
        additional tables render as ASCII inside a code block section.
        """

        children = get_node_children(ast)
        if not any(is_table_node(node) for node in children):
            return None

        blocks: list[SlackBlock] = []
        used_native_table = False
        text_buffer: list[str] = []

        def flush_text() -> None:
            nonlocal text_buffer
            if text_buffer:
                text = "\n\n".join(text_buffer)
                if text.strip():
                    blocks.append(
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": text},
                        }
                    )
                text_buffer = []

        for node in children:
            if is_table_node(node):
                flush_text()
                if used_native_table:
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"```\n{table_to_ascii(node)}\n```",
                            },
                        }
                    )
                else:
                    blocks.append(_mdast_table_to_slack_block(node, self._node_to_mrkdwn))
                    used_native_table = True
            else:
                text_buffer.append(self._node_to_mrkdwn(node))

        flush_text()
        return blocks

    # ------------------------------------------------------------- node visitor

    def _node_to_mrkdwn(self, node: MdastNode) -> str:
        """Convert a single mdast node to Slack mrkdwn."""

        if is_paragraph_node(node):
            return "".join(self._node_to_mrkdwn(c) for c in get_node_children(node))

        if is_text_node(node):
            value = str(node.get("value", ""))
            return _BARE_MENTION_REGEX.sub(r"<@\1>", value)

        if is_strong_node(node):
            content = "".join(self._node_to_mrkdwn(c) for c in get_node_children(node))
            return f"*{content}*"

        if is_emphasis_node(node):
            content = "".join(self._node_to_mrkdwn(c) for c in get_node_children(node))
            return f"_{content}_"

        if is_delete_node(node):
            content = "".join(self._node_to_mrkdwn(c) for c in get_node_children(node))
            return f"~{content}~"

        if is_inline_code_node(node):
            return f"`{node.get('value', '')}`"

        if is_code_node(node):
            lang = node.get("lang") or ""
            return f"```{lang}\n{node.get('value', '')}\n```"

        if is_link_node(node):
            link_text = "".join(self._node_to_mrkdwn(c) for c in get_node_children(node))
            return f"<{node.get('url', '')}|{link_text}>"

        if is_blockquote_node(node):
            return "\n".join(f"> {self._node_to_mrkdwn(c)}" for c in get_node_children(node))

        if is_list_node(node):
            return self._render_list(node, 0, self._node_to_mrkdwn, "•")

        node_type = node.get("type")
        if node_type == "break":
            return "\n"
        if node_type == "thematicBreak":
            return "---"

        if is_table_node(node):
            return f"```\n{table_to_ascii(node)}\n```"

        return self._default_node_to_text(node, self._node_to_mrkdwn)


def _mdast_table_to_slack_block(
    node: MdastNode,
    cell_converter: Any,
) -> SlackBlock:
    """Convert a mdast ``table`` node to a Slack ``table`` block.

    See https://docs.slack.dev/reference/block-kit/blocks/table-block/.
    """

    rows: list[list[dict[str, str]]] = []

    for row in get_node_children(node):
        cells: list[dict[str, str]] = []
        for cell in get_node_children(row):
            raw_text = "".join(cell_converter(c) for c in get_node_children(cell))
            # Slack's API rejects empty cell text — substitute a space.
            text = raw_text if raw_text else " "
            cells.append({"type": "raw_text", "text": text})
        rows.append(cells)

    block: SlackBlock = {"type": "table", "rows": rows}

    align = node.get("align")
    if align:
        block["column_settings"] = [{"align": a or "left"} for a in align]

    return block


# Backwards-compatibility alias — matches upstream.
SlackMarkdownConverter = SlackFormatConverter


__all__ = [
    "SlackFormatConverter",
    "SlackMarkdownConverter",
]
