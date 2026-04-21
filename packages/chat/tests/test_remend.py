"""Tests for :mod:`chat._remend` — minimal subset of the ``remend`` npm package."""

from __future__ import annotations

from chat._remend import is_clean, remend


class TestBold:
    def test_closes_unclosed_double_star(self) -> None:
        assert remend("Hello **wor") == "Hello **wor**"

    def test_leaves_closed_bold_alone(self) -> None:
        assert remend("Hello **world**") == "Hello **world**"

    def test_closes_unclosed_double_underscore(self) -> None:
        assert remend("Hello __wor") == "Hello __wor__"


class TestItalic:
    def test_closes_unclosed_single_star(self) -> None:
        assert remend("Hello *ita") == "Hello *ita*"

    def test_closes_unclosed_single_underscore(self) -> None:
        assert remend("Hello _ita") == "Hello _ita_"

    def test_closed_italic_is_untouched(self) -> None:
        assert remend("Hello *ok*") == "Hello *ok*"


class TestInlineCode:
    def test_closes_unclosed_backtick(self) -> None:
        assert remend("Hello `cod") == "Hello `cod`"

    def test_closed_inline_code_untouched(self) -> None:
        assert remend("Hello `code`") == "Hello `code`"


class TestStrikethrough:
    def test_closes_unclosed_tilde_pair(self) -> None:
        assert remend("Hello ~~str") == "Hello ~~str~~"

    def test_closed_strike_untouched(self) -> None:
        assert remend("Hello ~~gone~~") == "Hello ~~gone~~"


class TestLinks:
    def test_truncates_at_unclosed_link_opener(self) -> None:
        assert remend("See [link text") == "See"

    def test_truncates_at_unclosed_url(self) -> None:
        assert remend("See [text](http") == "See"

    def test_fully_closed_link_preserved(self) -> None:
        assert remend("See [x](https://a.com) end") == "See [x](https://a.com) end"


class TestCodeFences:
    def test_markers_inside_fence_ignored(self) -> None:
        text = "```\n**bold** and `code\n```"
        # Content inside the fence is literal — no closers should be added.
        assert remend(text) == text

    def test_unclosed_fence_not_modified(self) -> None:
        # Inside an open fence: don't add inline closers.
        text = "```python\nprint(**wor"
        assert remend(text) == text


class TestIsClean:
    def test_clean_text(self) -> None:
        assert is_clean("just plain text")
        assert is_clean("Hello **world**")
        assert is_clean("")

    def test_unclean_text(self) -> None:
        assert not is_clean("Hello **wor")
        assert not is_clean("See [link")


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert remend("") == ""

    def test_escaped_markers_not_counted(self) -> None:
        # Escaped ``\*`` doesn't open/close emphasis.
        assert remend(r"a \* b") == r"a \* b"
