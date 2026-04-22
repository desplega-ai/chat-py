"""Discord-specific format conversion using AST-based parsing.

Python port of upstream ``packages/adapter-discord/src/markdown.ts``.

Discord uses standard markdown with a few extensions:

- Bold: ``**text**`` (standard)
- Italic: ``*text*`` or ``_text_`` (standard)
- Strikethrough: ``~~text~~`` (standard GFM)
- Links: ``[text](url)`` (standard)
- User mentions: ``<@userId>`` (or ``<@!userId>`` for nickname)
- Channel mentions: ``<#channelId>``
- Role mentions: ``<@&roleId>``
- Custom emoji: ``<:name:id>`` or ``<a:name:id>`` (animated)
- Spoiler: ``||text||``
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

if TYPE_CHECKING:
    from chat import AdapterPostableMessage

# ``@name`` → ``<@name>``
_MENTION_PATTERN = re.compile(r"@(\w+)")
# ``<@userId>`` or ``<@!userId>`` → ``@userId``
_USER_MENTION_TAG = re.compile(r"<@!?(\w+)>")
# ``<#channelId>`` → ``#channelId``
_CHANNEL_MENTION_TAG = re.compile(r"<#(\w+)>")
# ``<@&roleId>`` → ``@&roleId``
_ROLE_MENTION_TAG = re.compile(r"<@&(\w+)>")
# ``<:name:id>`` or ``<a:name:id>`` → ``:name:``
_CUSTOM_EMOJI_TAG = re.compile(r"<a?:(\w+):\d+>")
# ``||spoiler||`` → ``[spoiler: spoiler]``
_SPOILER_TAG = re.compile(r"\|\|([^|]+)\|\|")


def _convert_mentions_to_discord(text: str) -> str:
    """Plain ``@name`` → ``<@name>``."""

    return _MENTION_PATTERN.sub(r"<@\1>", text)


class DiscordFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and Discord markdown."""

    # ---------------------------------------------------------------- overrides

    def render_postable(self, message: AdapterPostableMessage | str) -> str:
        """Override to apply the @mention translation to raw strings."""

        if isinstance(message, str):
            return _convert_mentions_to_discord(message)
        if isinstance(message, dict):
            if "raw" in message:
                return _convert_mentions_to_discord(message["raw"])
            if "markdown" in message:
                return self.from_ast(parse_markdown(message["markdown"]))
            if "ast" in message:
                return self.from_ast(message["ast"])
        return ""

    # ------------------------------------------------------------ AST → Discord

    def from_ast(self, ast: MdastRoot) -> str:
        """Render a Markdown AST to Discord format."""

        return self._from_ast_with_node_converter(ast, self._node_to_discord)

    # ------------------------------------------------------------ Discord → AST

    def to_ast(self, platform_text: str) -> MdastRoot:
        """Parse a Discord message into a standard Markdown AST."""

        markdown = platform_text

        # User mentions: <@userId> or <@!userId> → @userId
        markdown = _USER_MENTION_TAG.sub(r"@\1", markdown)
        # Channel mentions: <#channelId> → #channelId
        markdown = _CHANNEL_MENTION_TAG.sub(r"#\1", markdown)
        # Role mentions: <@&roleId> → @&roleId
        markdown = _ROLE_MENTION_TAG.sub(r"@&\1", markdown)
        # Custom emoji: <:name:id> / <a:name:id> → :name:
        markdown = _CUSTOM_EMOJI_TAG.sub(r":\1:", markdown)
        # Spoiler: ||text|| → [spoiler: text]
        markdown = _SPOILER_TAG.sub(r"[spoiler: \1]", markdown)

        return parse_markdown(markdown)

    # ------------------------------------------------------------ node visitor

    def _node_to_discord(self, node: MdastNode) -> str:
        if is_paragraph_node(node):
            return "".join(self._node_to_discord(c) for c in get_node_children(node))

        if is_text_node(node):
            value = str(node.get("value", ""))
            return _convert_mentions_to_discord(value)

        if is_strong_node(node):
            content = "".join(self._node_to_discord(c) for c in get_node_children(node))
            return f"**{content}**"

        if is_emphasis_node(node):
            content = "".join(self._node_to_discord(c) for c in get_node_children(node))
            return f"*{content}*"

        if is_delete_node(node):
            content = "".join(self._node_to_discord(c) for c in get_node_children(node))
            return f"~~{content}~~"

        if is_inline_code_node(node):
            return f"`{node.get('value', '')}`"

        if is_code_node(node):
            lang = str(node.get("lang") or "")
            return f"```{lang}\n{node.get('value', '')}\n```"

        if is_link_node(node):
            link_text = "".join(self._node_to_discord(c) for c in get_node_children(node))
            return f"[{link_text}]({node.get('url', '')})"

        if is_blockquote_node(node):
            return "\n".join(f"> {self._node_to_discord(c)}" for c in get_node_children(node))

        if is_list_node(node):
            return self._render_list(node, 0, self._node_to_discord)

        node_type = node.get("type")
        if node_type == "break":
            return "\n"
        if node_type == "thematicBreak":
            return "---"

        if is_table_node(node):
            return self._table_to_codeblock(node)

        return self._default_node_to_text(node, self._node_to_discord)

    # ------------------------------------------------------------ table helper

    def _table_to_codeblock(self, node: MdastNode) -> str:
        """Render an mdast table as an ASCII table inside a fenced code block."""

        from chat import table_to_ascii  # local import — avoids cycle at module load

        return f"```\n{table_to_ascii(node)}\n```"


__all__ = [
    "DiscordFormatConverter",
]
