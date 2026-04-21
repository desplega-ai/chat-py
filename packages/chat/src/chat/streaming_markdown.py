"""Streaming markdown renderer — Python port of ``streaming-markdown.ts``.

The :class:`StreamingMarkdownRenderer` buffers LLM token chunks, hides
in-flight table headers until a separator row confirms, and closes any
unclosed inline markers via :func:`chat._remend.remend`. Output is plain
markdown; the adapter's ``edit_message`` → ``render_postable`` pipeline
handles the final platform conversion.

API parity with upstream:

- :meth:`push` — append a stream chunk.
- :meth:`render` — get markdown suitable for an intermediate ``edit_message``.
- :meth:`get_committable_text` — get the prefix safe for append-only
  streaming (Slack native streaming, etc.), possibly code-fence-wrapping
  confirmed tables so pipes render as literal text on surfaces without
  GFM support.
- :meth:`get_text` — raw accumulated text (no healing, no buffering) for
  the final edit.
- :meth:`finish` — signal stream end, flush held-back lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chat._remend import is_clean, remend

TABLE_ROW_RE = re.compile(r"^\|.*\|$")
TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:]*-+[\s:]*(\|[\s:]*-+[\s:]*)*\|$")

_INLINE_MARKER_CHARS: frozenset[str] = frozenset({"*", "~", "`", "["})


@dataclass(slots=True)
class _Options:
    wrap_tables_for_append: bool = True


class StreamingMarkdownRenderer:
    """Buffered streaming markdown renderer with table-confirmation gating."""

    def __init__(self, *, wrap_tables_for_append: bool = True) -> None:
        self._accumulated = ""
        self._dirty = True
        self._cached_render = ""
        self._finished = False
        self._fence_toggles = 0
        self._incomplete_line = ""
        self._options = _Options(wrap_tables_for_append=wrap_tables_for_append)

    def push(self, chunk: str) -> None:
        """Append *chunk* from the LLM stream."""
        self._accumulated += chunk
        self._dirty = True

        self._incomplete_line += chunk
        parts = self._incomplete_line.split("\n")
        self._incomplete_line = parts.pop() if parts else ""
        for line in parts:
            trimmed = line.lstrip()
            if trimmed.startswith("```") or trimmed.startswith("~~~"):
                self._fence_toggles += 1

    def _accumulated_inside_fence(self) -> bool:
        inside = self._fence_toggles % 2 == 1
        trimmed = self._incomplete_line.lstrip()
        if trimmed.startswith("```") or trimmed.startswith("~~~"):
            inside = not inside
        return inside

    def render(self) -> str:
        """Get markdown for an intermediate ``edit_message``."""
        if not self._dirty:
            return self._cached_render

        self._dirty = False

        if self._finished:
            self._cached_render = remend(self._accumulated)
            return self._cached_render

        if self._accumulated_inside_fence():
            self._cached_render = remend(self._accumulated)
            return self._cached_render

        committable = _get_committable_prefix(self._accumulated)
        self._cached_render = remend(committable)
        return self._cached_render

    def get_committable_text(self) -> str:
        """Get the append-only-safe prefix (delta-suitable for streaming)."""
        if self._finished:
            return self._format_append_only_text(self._accumulated, close_fences=True)

        text = self._accumulated
        if text and not text.endswith("\n"):
            last_newline = text.rfind("\n")
            without_incomplete = text[: last_newline + 1] if last_newline >= 0 else ""
            if _is_inside_code_fence(without_incomplete):
                return self._format_append_only_text(text)
            text = without_incomplete

        if _is_inside_code_fence(text):
            return self._format_append_only_text(text)

        committed = _get_committable_prefix(text)
        wrapped = self._format_append_only_text(committed)

        if _is_inside_code_fence(wrapped):
            return wrapped

        return _find_clean_prefix(wrapped)

    def get_text(self) -> str:
        """Return the raw accumulated text (no healing)."""
        return self._accumulated

    def finish(self) -> str:
        """Signal stream end and flush held-back lines. Returns :meth:`render`."""
        self._finished = True
        self._dirty = True
        return self.render()

    def _format_append_only_text(self, text: str, *, close_fences: bool = False) -> str:
        if not self._options.wrap_tables_for_append:
            return text
        return _wrap_tables_for_append(text, close_fences=close_fences)


def _find_clean_prefix(text: str) -> str:
    """Longest prefix of *text* with balanced inline markers."""
    if not text or is_clean(text):
        return text

    i = len(text) - 1
    while i >= 0:
        if text[i] in _INLINE_MARKER_CHARS:
            # Group consecutive same chars (e.g., ``**`` or ``~~``).
            while i > 0 and text[i - 1] == text[i]:
                i -= 1
            candidate = text[:i]
            if is_clean(candidate):
                return candidate
        i -= 1

    return ""


def _is_inside_code_fence(text: str) -> bool:
    inside = False
    for line in text.split("\n"):
        trimmed = line.lstrip()
        if trimmed.startswith("```") or trimmed.startswith("~~~"):
            inside = not inside
    return inside


def _get_committable_prefix(text: str) -> str:
    """Longest prefix that isn't awaiting table-confirmation."""
    ends_with_newline = text.endswith("\n")
    lines = text.split("\n")

    if not ends_with_newline and lines:
        lines.pop()

    if ends_with_newline and lines and lines[-1] == "":
        lines.pop()

    held_count = 0
    separator_found = False

    for i in range(len(lines) - 1, -1, -1):
        trimmed = lines[i].strip()
        if trimmed == "":
            break
        if TABLE_SEPARATOR_RE.match(trimmed):
            separator_found = True
            break
        if TABLE_ROW_RE.match(trimmed):
            held_count += 1
        else:
            break

    if separator_found or held_count == 0:
        return text

    commit_line_count = len(lines) - held_count
    committed_lines = lines[:commit_line_count]
    result = "\n".join(committed_lines)
    if committed_lines:
        result += "\n"
    return result


def _wrap_tables_for_append(text: str, *, close_fences: bool = False) -> str:
    had_trailing_newline = text.endswith("\n")
    lines = text.split("\n")

    if had_trailing_newline and lines and lines[-1] == "":
        lines.pop()

    result: list[str] = []
    in_table = False
    in_user_code_fence = False

    for i, line in enumerate(lines):
        trimmed = line.strip()

        if not in_table and (trimmed.startswith("```") or trimmed.startswith("~~~")):
            in_user_code_fence = not in_user_code_fence
            result.append(line)
            continue

        if in_user_code_fence:
            result.append(line)
            continue

        is_table_line = trimmed != "" and (
            bool(TABLE_ROW_RE.match(trimmed)) or bool(TABLE_SEPARATOR_RE.match(trimmed))
        )

        if is_table_line and not in_table:
            has_separator = False
            for j in range(i, len(lines)):
                t = lines[j].strip()
                if TABLE_SEPARATOR_RE.match(t):
                    has_separator = True
                    break
                if t == "" or not TABLE_ROW_RE.match(t):
                    break
            if has_separator:
                result.append("```")
                in_table = True
        elif not is_table_line and in_table:
            result.append("```")
            in_table = False

        result.append(line)

    if in_table and close_fences:
        result.append("```")

    output = "\n".join(result)
    if had_trailing_newline:
        output += "\n"
    return output


__all__ = ["StreamingMarkdownRenderer"]
