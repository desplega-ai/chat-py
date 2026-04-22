"""GitHub-specific format conversion using AST-based parsing.

Python port of upstream ``packages/adapter-github/src/markdown.ts``.

GitHub uses GitHub-Flavored Markdown (GFM) which is very close to the
standard markdown that :mod:`chat.markdown` already produces. The converter
is therefore a near pass-through: :meth:`to_ast` delegates directly to
``parse_markdown``, :meth:`from_ast` delegates to ``stringify_markdown`` and
trims the result.

@mentions, ``#issue`` refs, and SHA references all survive the standard
GFM round-trip unmodified, so no additional translation is required here.
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


class GitHubFormatConverter(BaseFormatConverter):
    """Convert between standard Markdown AST and GitHub-flavored markdown."""

    def from_ast(self, ast: MdastRoot) -> str:
        """Render an AST to GFM-compatible markdown."""

        return stringify_markdown(ast).strip()

    def to_ast(self, platform_text: str) -> MdastRoot:
        """Parse GitHub markdown into an AST.

        GitHub uses standard GFM, so we use the standard parser.
        """

        return parse_markdown(platform_text)

    def render_postable(self, message: AdapterPostableMessage | str) -> str:
        """Handle raw strings / markdown / ast directly; fall back to the base.

        GitHub @mentions are already in the correct format (``@username``) so no
        translation is needed for the ``str`` and ``raw`` branches.
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


__all__ = ["GitHubFormatConverter"]
