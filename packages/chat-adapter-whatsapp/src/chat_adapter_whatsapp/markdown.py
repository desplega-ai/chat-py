"""WhatsApp-specific format conversion using AST-based parsing.

Python port of upstream ``packages/adapter-whatsapp/src/markdown.ts``.

WhatsApp uses a markdown-like format with some differences from standard:

* **Bold:** ``*text*`` (single asterisk, not double)
* **Italic:** ``_text_``
* **Strikethrough:** ``~text~`` (single tilde, not double)
* **Monospace:** ```` ```text``` ````

See https://faq.whatsapp.com/539178204879377.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

from chat import (
    BaseFormatConverter,
    MdastNode,
    MdastRoot,
    is_table_node,
    parse_markdown,
    stringify_markdown,
    table_to_ascii,
    walk_ast,
)

if TYPE_CHECKING:
    from chat import AdapterPostableMessage

# ``**bold**`` → ``*bold*`` (only single-line spans).
_STRONG_PATTERN = re.compile(r"\*\*(.+?)\*\*")

# ``~~strike~~`` → ``~strike~``.
_STRIKE_PATTERN = re.compile(r"~~(.+?)~~")

# Single-asterisk bold: ``*x*`` not preceded/followed by ``*``, no newlines inside.
# Used to convert WhatsApp-style bold back to standard ``**x**`` for the parser.
_SINGLE_BOLD_PATTERN = re.compile(r"(?<!\*)\*(?!\*)([^\n*]+?)(?<!\*)\*(?!\*)")

# Single-tilde strikethrough: ``~x~`` not preceded/followed by ``~``, no newlines inside.
_SINGLE_STRIKE_PATTERN = re.compile(r"(?<!~)~(?!~)([^\n~]+?)(?<!~)~(?!~)")


def to_whatsapp_format(text: str) -> str:
    """Convert standard markdown emphasis markers to WhatsApp-flavoured ones.

    The stringifier is configured to emit ``_italic_`` and ``- bullets``, so
    only ``**bold**`` → ``*bold*`` and ``~~strike~~`` → ``~strike~`` need
    rewriting.
    """

    result = _STRONG_PATTERN.sub(r"*\1*", text)
    return _STRIKE_PATTERN.sub(r"~\1~", result)


def from_whatsapp_format(text: str) -> str:
    """Convert WhatsApp-flavoured markdown back to standard markdown.

    Single-asterisk bold ``*x*`` becomes ``**x**`` and single-tilde
    strikethrough ``~x~`` becomes ``~~x~~``. ``_italic_`` is preserved
    because it is identical in both formats.
    """

    result = _SINGLE_BOLD_PATTERN.sub(r"**\1**", text)
    return _SINGLE_STRIKE_PATTERN.sub(r"~~\1~~", result)


class WhatsAppFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and WhatsApp markdown."""

    def from_ast(self, ast: MdastRoot) -> str:
        """Render an AST to WhatsApp markdown.

        Headings collapse to a bold paragraph (with nested ``strong`` flattened
        to avoid ``***``), thematic breaks become a ``━━━`` separator, and
        tables fold to fenced code blocks. The resulting standard-markdown
        string is then post-processed to swap ``**bold**`` / ``~~strike~~`` for
        WhatsApp's single-character variants.
        """

        def _rewrite(node: MdastNode) -> MdastNode:
            ntype = node.get("type")
            if ntype == "heading":
                children = node.get("children") or []
                flattened: list[MdastNode] = []
                for child in children:
                    if child.get("type") == "strong":
                        flattened.extend(child.get("children") or [])
                    else:
                        flattened.append(child)
                return {
                    "type": "paragraph",
                    "children": [{"type": "strong", "children": flattened}],
                }
            if ntype == "thematicBreak":
                return {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "━━━"}],
                }
            if is_table_node(node):
                return {
                    "type": "code",
                    "value": table_to_ascii(node),
                    "lang": None,
                }
            return node

        transformed = walk_ast(copy.deepcopy(ast), _rewrite)
        markdown = stringify_markdown(
            transformed,
            {"emphasis": "_", "bullet": "-"},
        ).strip()
        return to_whatsapp_format(markdown)

    def to_ast(self, platform_text: str) -> MdastRoot:
        """Parse WhatsApp markdown into an AST.

        WhatsApp-flavoured bold/strike are swapped for the standard
        double-character forms before delegating to :func:`parse_markdown`.
        """

        return parse_markdown(from_whatsapp_format(platform_text))

    def render_postable(self, message: AdapterPostableMessage | str) -> str:
        """Handle raw strings / markdown / ast directly; fall back to the base.

        Plain strings and ``{raw: ...}`` messages ship verbatim. Everything
        else flows through the WhatsApp markdown renderer.
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
    "WhatsAppFormatConverter",
    "from_whatsapp_format",
    "to_whatsapp_format",
]
