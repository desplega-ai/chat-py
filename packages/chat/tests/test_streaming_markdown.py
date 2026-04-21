"""Tests for :mod:`chat.streaming_markdown` — mirrors ``streaming-markdown.test.ts``."""

from __future__ import annotations

import re

from chat.streaming_markdown import StreamingMarkdownRenderer


class TestAccumulation:
    def test_basic_text(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello")
        r.push(" World")
        assert r.render() == "Hello World"

    def test_empty_input(self) -> None:
        r = StreamingMarkdownRenderer()
        assert r.render() == ""
        assert r.get_text() == ""
        assert r.finish() == ""

    def test_no_trailing_newline(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello world")
        assert r.render() == "Hello world"

    def test_get_text_is_raw(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **wor")
        r.render()
        assert r.get_text() == "Hello **wor"


class TestRemendHealing:
    def test_closes_inline_markers(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **wor")
        out = r.render()
        # remend closes the bold marker — must have even ``**`` count.
        assert out.count("**") % 2 == 0
        assert "Hello **wor" in out

    def test_idempotent(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **wor")
        first = r.render()
        second = r.render()
        assert first == second


class TestTableBuffering:
    def test_holds_back_table_header(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n")
        out = r.render()
        assert "| A | B |" not in out
        assert "Text" in out

    def test_confirms_on_separator(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n")
        assert "| A | B |" not in r.render()
        r.push("|---|---|\n")
        out = r.render()
        assert "| A | B |" in out
        assert "|---|---|" in out

    def test_releases_on_non_table_line(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n")
        assert "| A | B |" not in r.render()
        r.push("Not a table\n")
        out = r.render()
        assert "| A | B |" in out
        assert "Not a table" in out

    def test_pipes_inside_fence_not_buffered(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("```\n| A |\n")
        assert "| A |" in r.render()

    def test_finish_flushes_held_lines(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n")
        assert "| A | B |" not in r.render()
        assert "| A | B |" in r.finish()

    def test_full_table_with_data(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("| A | B |\n|---|---|\n| 1 | 2 |\n")
        out = r.render()
        for frag in ("| A | B |", "|---|---|", "| 1 | 2 |"):
            assert frag in out

    def test_alignment_markers_in_separator(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("| Left | Center | Right |\n")
        assert "| Left |" not in r.render()
        r.push("|:---|:---:|---:|\n")
        out = r.render()
        assert "| Left | Center | Right |" in out

    def test_multiple_pipe_rows_held(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Intro\n\n| A | B |\n| C | D |\n")
        out = r.render()
        assert "| A | B |" not in out
        assert "| C | D |" not in out

    def test_tilde_fence(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("~~~\n| A |\n")
        assert "| A |" in r.render()

    def test_resumes_buffering_after_fence_closes(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("```\n| inside |\n```\n| A | B |\n")
        out = r.render()
        assert "| inside |" in out
        assert "| A | B |" not in out

    def test_incomplete_table_header_line_not_buffered(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |")
        assert "Text" in r.render()

    def test_second_table_held_after_first(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("| A | B |\n|---|---|\n| 1 | 2 |\n")
        assert "|---|---|" in r.render()
        r.push("\n| X | Y |\n")
        out = r.render()
        assert "| A | B |" in out
        assert "| 1 | 2 |" in out
        assert "| X | Y |" not in out

    def test_hold_release_new_hold(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("| A | B |\n")
        assert "| A | B |" not in r.render()
        r.push("Normal text\n")
        out = r.render()
        assert "| A | B |" in out
        assert "Normal text" in out
        r.push("| X | Y |\n")
        out = r.render()
        assert "| X | Y |" not in out

    def test_multiple_pushes_single_render(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("| A ")
        r.push("| B |\n")
        r.push("|---|---|\n")
        r.push("| 1 | 2 |\n")
        out = r.render()
        for frag in ("| A | B |", "|---|---|", "| 1 | 2 |"):
            assert frag in out


class TestFinishLifecycle:
    def test_push_after_finish(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello")
        r.finish()
        r.push(" World")
        assert "Hello World" in r.render()

    def test_idempotent_after_finish(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n")
        r.finish()
        first = r.render()
        second = r.render()
        assert first == second
        assert "| A | B |" in first

    def test_dirty_flag_push_render_cycle(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello")
        r1 = r.render()
        assert r1 == "Hello"
        # No push → cached.
        assert r.render() == r1

        r.push(" **bold")
        r2 = r.render()
        assert r2 != r1
        assert "Hello **bold" in r2
        assert r2.count("**") % 2 == 0


class TestGetCommittableTextInlineMarkers:
    def test_incomplete_line_stripped(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **wor")
        assert r.get_committable_text() == ""

    def test_unclosed_bold_held_on_complete_line(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **wor\n")
        committable = r.get_committable_text()
        assert committable == "Hello "

    def test_released_when_bold_closes(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **wor")
        assert r.get_committable_text() == ""
        r.push("ld** done\n")
        assert r.get_committable_text() == "Hello **world** done\n"

    def test_unclosed_italic_held(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello *ita\n")
        assert r.get_committable_text() == "Hello "

    def test_unclosed_strikethrough_held(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello ~~str\n")
        assert r.get_committable_text() == "Hello "

    def test_unclosed_inline_code_held(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello `cod\n")
        assert r.get_committable_text() == "Hello "

    def test_unclosed_link_held(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("See [link text\n")
        assert r.get_committable_text() == "See "

    def test_balanced_markers_clean(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **world** and *italic* done\n")
        assert r.get_committable_text() == "Hello **world** and *italic* done\n"


class TestGetCommittableTextTables:
    def test_holds_table_rows(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n")
        committable = r.get_committable_text()
        assert "| A | B |" not in committable
        assert "Text" in committable

    def test_wraps_confirmed_table(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        committable = r.get_committable_text()
        assert "```" in committable
        assert "| A | B |" in committable
        assert "| 1 | 2 |" in committable
        assert "Text" in committable

    def test_not_buffered_inside_fence(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("```\n| A |\n")
        assert "| A |" in r.get_committable_text()

    def test_flushed_after_finish(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Text\n\n| A | B |\n")
        assert "| A | B |" not in r.get_committable_text()
        r.finish()
        assert "| A | B |" in r.get_committable_text()

    def test_flush_unclosed_markers_after_finish(self) -> None:
        r = StreamingMarkdownRenderer()
        r.push("Hello **wor\n")
        assert r.get_committable_text() == "Hello "
        r.finish()
        assert r.get_committable_text() == "Hello **wor\n"


class TestGetCommittableTextDeltas:
    def test_table_streams_in_code_fence(self) -> None:
        r = StreamingMarkdownRenderer()
        last = ""

        r.push("Hello\n\n")
        committable = r.get_committable_text()
        assert committable[len(last) :] == "Hello\n\n"
        last = committable

        r.push("| A | B |\n")
        committable = r.get_committable_text()
        assert committable[len(last) :] == ""

        r.push("|---|---|\n")
        committable = r.get_committable_text()
        delta = committable[len(last) :]
        assert "```" in delta
        assert "| A | B |" in delta
        assert "|---|---|" in delta
        last = committable

        r.push("| 1 | 2 |\n")
        committable = r.get_committable_text()
        delta = committable[len(last) :]
        assert "| 1 | 2 |" in delta
        assert "```" not in delta
        last = committable

        r.push("\nMore text\n")
        committable = r.get_committable_text()
        delta = committable[len(last) :]
        assert "```" in delta
        assert "More text" in delta

    def test_inline_markers_append_stream(self) -> None:
        r = StreamingMarkdownRenderer()
        last = ""
        r.push("Hello ")
        assert r.get_committable_text() == ""

        r.push("**world** done\n")
        committable = r.get_committable_text()
        delta = committable[len(last) :]
        assert delta == "Hello **world** done\n"
        last = committable

        r.push("More **text")
        committable = r.get_committable_text()
        assert committable[len(last) :] == ""

        r.push("** end\n")
        committable = r.get_committable_text()
        delta = committable[len(last) :]
        assert delta == "More **text** end\n"


def _simulate_append_stream(
    chunks: list[str],
    *,
    wrap_tables_for_append: bool = True,
) -> tuple[str, str, list[str]]:
    r = StreamingMarkdownRenderer(wrap_tables_for_append=wrap_tables_for_append)
    last = ""
    deltas: list[str] = []
    for chunk in chunks:
        r.push(chunk)
        committable = r.get_committable_text()
        delta = committable[len(last) :]
        if delta:
            deltas.append(delta)
            last = committable
    r.finish()
    final = r.get_committable_text()
    final_delta = final[len(last) :]
    if final_delta:
        deltas.append(final_delta)
    return "".join(deltas), r.get_text(), deltas


class TestAppendOnly:
    def test_plain_text(self) -> None:
        appended, _, _ = _simulate_append_stream(["Hello ", "World", "!\n"])
        assert appended == "Hello World!\n"

    def test_bold_hold_release(self) -> None:
        appended, _, _ = _simulate_append_stream(["Hello ", "**bold", "** text\n"])
        assert "**bold**" in appended
        assert "Hello " in appended

    def test_table_wrapped_in_fence(self) -> None:
        appended, _, _ = _simulate_append_stream(
            [
                "Intro\n\n",
                "| A | B |\n",
                "|---|---|\n",
                "| 1 | 2 |\n",
                "| 3 | 4 |\n",
                "\nAfter table\n",
            ]
        )
        assert "```\n| A | B |" in appended
        assert "| 1 | 2 |" in appended
        assert "```\n\nAfter table" in appended
        assert appended.index("Intro") < appended.index("```")

    def test_table_no_wrap(self) -> None:
        appended, _, _ = _simulate_append_stream(
            [
                "Intro\n\n",
                "| A | B |\n",
                "|---|---|\n",
                "| 1 | 2 |\n",
                "\nAfter\n",
            ],
            wrap_tables_for_append=False,
        )
        assert "| A | B |" in appended
        assert "After" in appended
        assert "```" not in appended

    def test_table_flushed_on_finish(self) -> None:
        appended, _, deltas = _simulate_append_stream(
            [
                "Text\n\n",
                "| A | B |\n",
                "|---|---|\n",
                "| 1 | 2 |\n",
            ]
        )
        assert "| A | B |" in appended
        assert "```" in appended
        assert deltas[-1]

    def test_deltas_concatenate_to_final(self) -> None:
        appended, _, _ = _simulate_append_stream(
            [
                "Hello **world**\n",
                "\n",
                "| H1 | H2 |\n",
                "| - | - |\n",
                "| a | b |\n",
                "\nDone\n",
            ]
        )
        assert "Hello **world**" in appended
        assert "| H1 | H2 |" in appended
        assert "Done" in appended
        assert "```" in appended

    def test_monotonicity_across_chunks(self) -> None:
        r = StreamingMarkdownRenderer()
        last = ""
        chunks = [
            "Hello **world**\n",
            "\n",
            "| A | B |\n",
            "| - | - |\n",
            "| 1 | 2 |\n",
            "\nDone\n",
        ]
        deltas: list[str] = []
        for chunk in chunks:
            r.push(chunk)
            committable = r.get_committable_text()
            assert committable.startswith(last), (
                f"Monotonicity broke — previous={last!r} current={committable!r}"
            )
            delta = committable[len(last) :]
            if delta:
                deltas.append(delta)
                last = committable
        r.finish()
        final = r.get_committable_text()
        assert final.startswith(last)
        tail = final[len(last) :]
        if tail:
            deltas.append(tail)
        assert "".join(deltas) == final

    def test_multiple_tables(self) -> None:
        appended, _, _ = _simulate_append_stream(
            [
                "First:\n\n",
                "| A |\n",
                "|---|\n",
                "| 1 |\n",
                "\nSecond:\n\n",
                "| X |\n",
                "|---|\n",
                "| 9 |\n",
                "\nDone\n",
            ]
        )
        fence_count = len(re.findall(r"```", appended))
        assert fence_count == 4  # open+close per table
        assert "| 1 |" in appended
        assert "| 9 |" in appended


class TestExhaustivePrefixes:
    """Mirrors upstream's char-by-char prefix invariant tests on a complex doc."""

    COMPLEX = (
        "# Heading\n"
        "\n"
        "Some **bold** and *italic* text with `inline code` here.\n"
        "\n"
        "A [link](https://example.com) and ~~deleted~~ stuff.\n"
        "\n"
        "## Table section\n"
        "\n"
        "| Name | Age | City |\n"
        "| - | - | - |\n"
        "| Alice | 30 | NYC |\n"
        "| Bob | 25 | LA |\n"
        "\n"
        "Text after table with **bold again**.\n"
        "\n"
        "```\n"
        "code block with | pipes | inside\n"
        "and **markers** that are literal\n"
        "```\n"
        "\n"
        "Final paragraph.\n"
    )

    def test_get_committable_text_is_monotonic(self) -> None:
        r = StreamingMarkdownRenderer()
        prev = ""
        for i, ch in enumerate(self.COMPLEX):
            r.push(ch)
            committable = r.get_committable_text()
            assert committable.startswith(prev), (
                f"monotonicity broke at char {i} ({ch!r})\n"
                f"  prev: {prev[-40:]!r}\n"
                f"  now:  {committable[-40:]!r}"
            )
            prev = committable

    def test_final_delta_reconstruction(self) -> None:
        r = StreamingMarkdownRenderer()
        last = ""
        deltas: list[str] = []
        for ch in self.COMPLEX:
            r.push(ch)
            committable = r.get_committable_text()
            delta = committable[len(last) :]
            if delta:
                deltas.append(delta)
                last = committable
        r.finish()
        final = r.get_committable_text()
        tail = final[len(last) :]
        if tail:
            deltas.append(tail)
        assert "".join(deltas) == final
        assert r.get_text() == self.COMPLEX

    def test_finish_preserves_raw_text(self) -> None:
        for cut in (0, 10, 50, 100, 150, len(self.COMPLEX)):
            r = StreamingMarkdownRenderer()
            r.push(self.COMPLEX[:cut])
            r.finish()
            assert r.get_text() == self.COMPLEX[:cut]
