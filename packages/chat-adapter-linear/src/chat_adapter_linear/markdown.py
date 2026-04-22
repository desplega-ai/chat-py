"""Linear-specific format conversion using AST-based parsing.

Python port of upstream ``packages/adapter-linear/src/markdown.ts``.

Linear uses standard Markdown for comments, which is very close to the
mdast format :mod:`chat.markdown` emits. This converter is therefore a
near pass-through, mirroring the GitHub adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat import (
    BaseFormatConverter,
    MdastRoot,
    parse_markdown,
    stringify_markdown,
)

if TYPE_CHECKING:
    from chat import AdapterPostableMessage


class LinearFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and Linear-flavored markdown."""

    def from_ast(self, ast: MdastRoot) -> str:
        """Render an AST to Linear-compatible markdown."""

        return stringify_markdown(ast).strip()

    def to_ast(self, platform_text: str) -> MdastRoot:
        """Parse Linear markdown into an AST."""

        return parse_markdown(platform_text)

    def render_postable(self, message: AdapterPostableMessage | str) -> str:
        """Handle raw strings / markdown / ast directly; fall back to the base."""

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


__all__ = ["LinearFormatConverter"]
