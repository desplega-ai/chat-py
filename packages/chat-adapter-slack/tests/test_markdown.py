"""Tests for the Slack markdown converter.

Mirrors upstream ``packages/adapter-slack/src/markdown.test.ts``.
"""

from __future__ import annotations

from chat import parse_markdown
from chat_adapter_slack.markdown import (
    SlackFormatConverter,
    SlackMarkdownConverter,
)


def _converter() -> SlackFormatConverter:
    return SlackFormatConverter()


# ---------------------------------------------------------------------------
# fromMarkdown (markdown -> mrkdwn)
# ---------------------------------------------------------------------------


class TestFromMarkdown:
    def test_bold(self) -> None:
        assert _converter().from_markdown("Hello **world**!") == "Hello *world*!"

    def test_italic(self) -> None:
        assert _converter().from_markdown("Hello _world_!") == "Hello _world_!"

    def test_strikethrough(self) -> None:
        assert _converter().from_markdown("Hello ~~world~~!") == "Hello ~world~!"

    def test_links(self) -> None:
        assert (
            _converter().from_markdown("Check [this](https://example.com)")
            == "Check <https://example.com|this>"
        )

    def test_inline_code(self) -> None:
        assert _converter().from_markdown("Use `const x = 1`") == "Use `const x = 1`"

    def test_code_blocks(self) -> None:
        output = _converter().from_markdown("```js\nconst x = 1;\n```")
        assert "```" in output
        assert "const x = 1;" in output

    def test_mixed_formatting(self) -> None:
        output = _converter().from_markdown("**Bold** and _italic_ and [link](https://x.com)")
        assert "*Bold*" in output
        assert "_italic_" in output
        assert "<https://x.com|link>" in output


# ---------------------------------------------------------------------------
# toMarkdown (mrkdwn -> markdown)
# ---------------------------------------------------------------------------


class TestToMarkdown:
    def test_bold(self) -> None:
        assert "**world**" in _converter().to_markdown("Hello *world*!")

    def test_strikethrough(self) -> None:
        assert "~~world~~" in _converter().to_markdown("Hello ~world~!")

    def test_links_with_text(self) -> None:
        result = _converter().to_markdown("Check <https://example.com|this>")
        assert "[this](https://example.com)" in result

    def test_bare_links(self) -> None:
        result = _converter().to_markdown("Visit <https://example.com>")
        assert "https://example.com" in result

    def test_user_mentions(self) -> None:
        result = _converter().to_markdown("Hey <@U123|john>!")
        assert "@john" in result

    def test_channel_mentions(self) -> None:
        result = _converter().to_markdown("Join <#C123|general>")
        assert "#general" in result

    def test_bare_channel_id_mentions(self) -> None:
        result = _converter().to_markdown("Join <#C123>")
        assert "#C123" in result


# ---------------------------------------------------------------------------
# Mentions (via renderPostable + fromMarkdown)
# ---------------------------------------------------------------------------


class TestMentions:
    def test_existing_slack_mention_in_string_untouched(self) -> None:
        assert (
            _converter().render_postable("Hey <@U12345>. Please select")
            == "Hey <@U12345>. Please select"
        )

    def test_existing_slack_mention_in_markdown_untouched(self) -> None:
        assert (
            _converter().render_postable({"markdown": "Hey <@U12345>. Please select"})
            == "Hey <@U12345>. Please select"
        )

    def test_bare_at_mention_in_string_rewritten(self) -> None:
        assert (
            _converter().render_postable("Hey @george. Please select")
            == "Hey <@george>. Please select"
        )

    def test_bare_at_mention_in_markdown_rewritten(self) -> None:
        assert (
            _converter().render_postable({"markdown": "Hey @george. Please select"})
            == "Hey <@george>. Please select"
        )

    def test_from_markdown_leaves_slack_mentions_alone(self) -> None:
        assert _converter().from_markdown("Hey <@U12345>") == "Hey <@U12345>"

    def test_email_in_plain_string_untouched(self) -> None:
        assert (
            _converter().render_postable("Contact user@example.com for help")
            == "Contact user@example.com for help"
        )

    def test_email_in_markdown_not_turned_into_mention(self) -> None:
        # Python's markdown parser does not auto-link bare emails (unlike GFM
        # upstream). The crucial invariant is the same: ``@example`` MUST NOT
        # be rewritten as a ``<@example>`` Slack mention.
        result = _converter().render_postable({"markdown": "Contact user@example.com for help"})
        assert "<@example>" not in result
        assert "user@example.com" in result

    def test_mailto_in_plain_string_untouched(self) -> None:
        assert (
            _converter().render_postable("Email <mailto:user@example.com>")
            == "Email <mailto:user@example.com>"
        )

    def test_email_inside_markdown_link_text_preserved(self) -> None:
        assert (
            _converter().from_markdown("Email [user@example.com](mailto:user@example.com)")
            == "Email <mailto:user@example.com|user@example.com>"
        )

    def test_bare_mentions_adjacent_to_punctuation(self) -> None:
        assert _converter().render_postable("(cc @george, @anne)") == "(cc <@george>, <@anne>)"


# ---------------------------------------------------------------------------
# toPlainText
# ---------------------------------------------------------------------------


class TestToPlainText:
    def test_removes_bold_markers(self) -> None:
        assert _converter().to_plain_text("Hello *world*!") == "Hello world!"

    def test_removes_italic_markers(self) -> None:
        assert _converter().to_plain_text("Hello _world_!") == "Hello world!"

    def test_extracts_link_text(self) -> None:
        assert _converter().to_plain_text("Check <https://example.com|this>") == "Check this"

    def test_formats_user_mentions(self) -> None:
        result = _converter().to_plain_text("Hey <@U123>!")
        assert "@U123" in result

    def test_complex_messages(self) -> None:
        text = "*Bold* and _italic_ with <https://x.com|link> and <@U123|user>"
        result = _converter().to_plain_text(text)
        assert "Bold" in result
        assert "italic" in result
        assert "link" in result
        assert "user" in result
        assert "*" not in result
        assert "<" not in result


# ---------------------------------------------------------------------------
# Table rendering (fromMarkdown)
# ---------------------------------------------------------------------------


class TestTableRendering:
    def test_renders_markdown_tables_as_code_blocks(self) -> None:
        result = _converter().from_markdown("| Name | Age |\n|------|-----|\n| Alice | 30 |")
        assert "```" in result
        assert "Name" in result
        assert "Age" in result
        assert "Alice" in result
        assert "30" in result

    def test_preserves_table_structure_in_code_block(self) -> None:
        result = _converter().from_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        assert result.startswith("```\n")
        assert result.endswith("\n```")


# ---------------------------------------------------------------------------
# toBlocksWithTable
# ---------------------------------------------------------------------------


class TestToBlocksWithTable:
    def test_returns_none_when_no_tables(self) -> None:
        ast = _converter().to_ast("Hello world")
        assert _converter().to_blocks_with_table(ast) is None

    def test_native_table_block_for_markdown_table(self) -> None:
        ast = _converter().to_ast("| Name | Age |\n|------|-----|\n| Alice | 30 |")
        blocks = _converter().to_blocks_with_table(ast)
        assert blocks is not None
        assert len(blocks) == 1
        assert blocks[0]["type"] == "table"
        assert blocks[0]["rows"] == [
            [
                {"type": "raw_text", "text": "Name"},
                {"type": "raw_text", "text": "Age"},
            ],
            [
                {"type": "raw_text", "text": "Alice"},
                {"type": "raw_text", "text": "30"},
            ],
        ]

    def test_surrounding_text_as_section_blocks(self) -> None:
        markdown = "Here are the results:\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAll done."
        ast = _converter().to_ast(markdown)
        blocks = _converter().to_blocks_with_table(ast)
        assert blocks is not None
        assert len(blocks) == 3
        assert blocks[0]["type"] == "section"
        assert "Here are the results" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "table"
        assert blocks[2]["type"] == "section"
        assert "All done" in blocks[2]["text"]["text"]

    def test_native_first_ascii_second(self) -> None:
        markdown = "| A | B |\n|---|---|\n| 1 | 2 |\n\n| C | D |\n|---|---|\n| 3 | 4 |"
        ast = _converter().to_ast(markdown)
        blocks = _converter().to_blocks_with_table(ast)
        assert blocks is not None
        assert len(blocks) == 2
        assert blocks[0]["type"] == "table"
        # Second table falls back to ASCII inside a section block.
        assert blocks[1]["type"] == "section"
        assert "```" in blocks[1]["text"]["text"]

    def test_empty_cells_replaced_with_space(self) -> None:
        ast = _converter().to_ast(
            "| Kind | Label |\n|------|-------|\n| FORM | Form Submission |\n| and more... | |"
        )
        blocks = _converter().to_blocks_with_table(ast)
        assert blocks is not None
        table_block = blocks[0]
        assert table_block["type"] == "table"
        for row in table_block["rows"]:
            for cell in row:
                assert len(cell["text"]) > 0
        # The empty cell in row 3, column 2 should be a space.
        assert table_block["rows"][2][1]["text"] == " "

    def test_empty_header_cell_with_parse_markdown(self) -> None:
        markdown = "Here is a table:\n\n|  | Header2 |\n|---------|----------|\n| Data1 | Data2 |"
        ast = parse_markdown(markdown)
        blocks = _converter().to_blocks_with_table(ast)
        assert blocks is not None
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "table"
        table_block = blocks[1]
        # Empty header cell should be a space.
        assert table_block["rows"][0][0]["text"] == " "
        for row in table_block["rows"]:
            for cell in row:
                assert len(cell["text"]) > 0


# ---------------------------------------------------------------------------
# Nested lists
# ---------------------------------------------------------------------------


class TestNestedLists:
    def test_nested_unordered_lists(self) -> None:
        result = _converter().from_markdown("- parent\n  - child 1\n  - child 2")
        assert result == "• parent\n  • child 1\n  • child 2"

    def test_nested_ordered_lists(self) -> None:
        result = _converter().from_markdown(
            "1. first\n   1. sub-first\n   2. sub-second\n2. second"
        )
        assert "1. first" in result
        assert "  1. sub-first" in result
        assert "  2. sub-second" in result
        assert "2. second" in result

    def test_deeply_nested_lists(self) -> None:
        result = _converter().from_markdown("- level 1\n  - level 2\n    - level 3")
        assert "• level 1" in result
        assert "  • level 2" in result
        assert "    • level 3" in result

    def test_sibling_items_same_indent(self) -> None:
        result = _converter().from_markdown("- item 1\n- item 2\n- item 3")
        assert result == "• item 1\n• item 2\n• item 3"

    def test_mixed_ordered_and_unordered(self) -> None:
        result = _converter().from_markdown("1. first\n   - sub a\n   - sub b\n2. second")
        assert "1. first" in result
        assert "  • sub a" in result
        assert "  • sub b" in result
        assert "2. second" in result


# ---------------------------------------------------------------------------
# Backwards-compat alias
# ---------------------------------------------------------------------------


def test_slack_markdown_converter_alias() -> None:
    assert SlackMarkdownConverter is SlackFormatConverter
