r"""Minimal streaming-markdown healer — Python port subset of the ``remend`` npm package.

Upstream uses `remend <https://streamdown.ai/docs/termination>`_ to close
incomplete inline markers during LLM streaming so intermediate renders don't
flash raw markdown glyphs (``**wor`` rendering as literal asterisks before the
closing ``**`` arrives).

This Python port implements the subset needed by
:class:`chat.streaming_markdown.StreamingMarkdownRenderer`:

- Close unclosed ``**`` / ``__`` (bold).
- Close unclosed ``*`` / ``_`` (italic).
- Close unclosed inline `` ` `` (code).
- Close unclosed ``~~`` (strikethrough).
- Strip unclosed ``[link text`` / ``[link text]`` (link openers without
  a matching URL).

Content inside fenced code blocks (``\`\`\`...\`\`\``) and inside inline code
spans is treated as literal. The goal is "don't leak dangling markers", not
"fully parse markdown" — so edge cases around nested emphasis, HTML, math, etc.
fall back to best-effort.

Behaviour is exercised by
:mod:`tests/test_remend.py` and :mod:`tests/test_streaming_markdown.py`.
"""

from __future__ import annotations


def _count_unescaped(text: str, char: str) -> int:
    """Count occurrences of *char* outside fenced code blocks and escapes."""
    in_fence = False
    count = 0
    i = 0
    n = len(text)
    while i < n:
        # Check for a ``` fence
        if text[i : i + 3] in ("```", "~~~"):
            in_fence = not in_fence
            i += 3
            continue
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if not in_fence and text[i] == char:
            count += 1
        i += 1
    return count


def _is_inside_fence(text: str) -> bool:
    """Return ``True`` if *text* ends inside an unclosed fenced code block."""
    inside = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            inside = not inside
    return inside


def _split_at_fences(text: str) -> list[tuple[bool, str]]:
    """Split *text* into ``[(in_fence, chunk), ...]`` runs."""
    out: list[tuple[bool, str]] = []
    in_fence = False
    buf: list[str] = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            out.append((in_fence, "\n".join(buf)))
            buf = [line]
            in_fence = not in_fence
            continue
        buf.append(line)
    out.append((in_fence, "\n".join(buf)))
    return out


def _count_inline_code_ticks(text: str) -> int:
    """Count single-backticks outside fences (triple-tick fences are handled separately)."""
    in_fence = False
    count = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i : i + 3] in ("```", "~~~"):
            in_fence = not in_fence
            i += 3
            continue
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if not in_fence and text[i] == "`":
            count += 1
        i += 1
    return count


def _strip_unclosed_links(text: str) -> str:
    """If an unclosed ``[`` (no matching ``](url)`` pair) is present, truncate at it.

    Only the last unclosed link opener is considered. The upstream behaviour is
    to replace with ``](streamdown:incomplete-link)`` but chat-py callers feed
    the result straight into remark-parse, so we opt for the simpler "cut at
    the opener" approach (text-only mode).
    """
    # Scan forward, tracking whether each ``[`` has a matching ``](...)``.
    # If we find an unmatched ``[``, return everything before it (trimmed).
    n = len(text)
    i = 0
    depth = 0
    open_at: list[int] = []
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "[":
            open_at.append(i)
            depth += 1
        elif ch == "]" and open_at:
            # Look for `](` completing a link; otherwise treat as orphan close.
            if i + 1 < n and text[i + 1] == "(":
                # Find matching ')'
                j = i + 2
                while j < n and text[j] != ")" and text[j] != "\n":
                    if text[j] == "\\" and j + 1 < n:
                        j += 2
                        continue
                    j += 1
                if j < n and text[j] == ")":
                    # Fully closed link
                    open_at.pop()
                    depth -= 1
                    i = j + 1
                    continue
                # ](... unclosed — cut at the opener
                opener = open_at[-1]
                return text[:opener].rstrip()
            # ] without () — treat as closing the last opener
            open_at.pop()
            depth -= 1
        elif ch == "\n":
            # Line boundary — any still-open `[` with no `]` on same line is
            # unclosed. We check below after the loop.
            pass
        i += 1

    if open_at:
        # Unclosed ``[`` — the last opener was never closed.
        # If there's any ``]`` between the opener and end of text, we may
        # have a ``[text](partial``; either way, truncate at the opener.
        return text[: open_at[0]].rstrip()
    return text


def remend(text: str) -> str:
    """Close unclosed inline markers in *text* for safe intermediate rendering.

    The return value satisfies :func:`is_clean` — i.e. a subsequent call to
    :func:`remend` on it would not add any more closers.
    """
    if not text:
        return text

    # Phase 1: unclosed links — cut at the opener.
    text = _strip_unclosed_links(text)

    # Phase 2: inline code — odd number of backticks outside fences.
    if _count_inline_code_ticks(text) % 2 == 1 and not _is_inside_fence(text):
        text += "`"

    # Phases 3-6: strikethrough, bold, italic markers — only close when
    # we're not inside a fenced block (fences make markers literal).
    if _is_inside_fence(text):
        return text

    # Strikethrough: ``~~`` pairs.
    tildes = _count_double(text, "~")
    if tildes % 2 == 1:
        text += "~~"

    # Bold: ``**`` pairs.
    stars_double = _count_double(text, "*")
    if stars_double % 2 == 1:
        text += "**"

    # Bold via underscores: ``__`` pairs.
    unders_double = _count_double(text, "_")
    if unders_double % 2 == 1:
        text += "__"

    # Italic: single ``*`` not part of ``**``.
    stars_single = _count_single(text, "*")
    if stars_single % 2 == 1:
        text += "*"

    # Italic: single ``_`` not part of ``__``.
    unders_single = _count_single(text, "_")
    if unders_single % 2 == 1:
        text += "_"

    return text


def _count_double(text: str, char: str) -> int:
    """Count ``char*2`` runs outside fences and inline code."""
    in_fence = False
    in_code = False
    count = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i : i + 3] in ("```", "~~~"):
            in_fence = not in_fence
            i += 3
            continue
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if not in_fence and text[i] == "`":
            in_code = not in_code
            i += 1
            continue
        if not in_fence and not in_code and text[i] == char and i + 1 < n and text[i + 1] == char:
            count += 1
            i += 2
            continue
        i += 1
    return count


def _count_single(text: str, char: str) -> int:
    """Count lone ``char`` (not part of a double run) outside fences/code."""
    in_fence = False
    in_code = False
    count = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i : i + 3] in ("```", "~~~"):
            in_fence = not in_fence
            i += 3
            continue
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if not in_fence and text[i] == "`":
            in_code = not in_code
            i += 1
            continue
        if not in_fence and not in_code and text[i] == char:
            # Skip doubles — they were already counted in _count_double.
            is_double = (i + 1 < n and text[i + 1] == char) or (i > 0 and text[i - 1] == char)
            if not is_double:
                count += 1
        i += 1
    return count


def is_clean(text: str) -> bool:
    """Return ``True`` if :func:`remend` would leave *text* unchanged.

    Note: upstream uses ``remend(text).length <= text.length`` because the
    npm ``remend`` package may trim trailing whitespace. Our port may also
    *truncate* (for unclosed links), so we use strict equality — otherwise
    :func:`chat.streaming_markdown._find_clean_prefix` would accept a
    truncating remend as "clean" and keep the unclosed opener in output.
    """
    return remend(text) == text


__all__ = ["is_clean", "remend"]
