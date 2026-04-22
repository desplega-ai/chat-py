"""Tests for :class:`TeamsFormatConverter`.

Mirrors upstream ``packages/adapter-teams/src/markdown.test.ts``.
"""

from __future__ import annotations

import pytest
from chat_adapter_teams.markdown import TeamsFormatConverter


@pytest.fixture
def converter() -> TeamsFormatConverter:
    return TeamsFormatConverter()


class TestFromAst:
    def test_converts_bold(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("**bold text**")
        assert "**bold text**" in converter.from_ast(ast)

    def test_converts_italic(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("_italic text_")
        assert "_italic text_" in converter.from_ast(ast)

    def test_converts_strikethrough(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("~~strikethrough~~")
        assert "~~strikethrough~~" in converter.from_ast(ast)

    def test_preserves_inline_code(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("Use `const x = 1`")
        assert "`const x = 1`" in converter.from_ast(ast)

    def test_handles_code_blocks(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("```js\nconst x = 1;\n```")
        out = converter.from_ast(ast)
        assert "```" in out
        assert "const x = 1;" in out

    def test_converts_links(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("[link text](https://example.com)")
        assert "[link text](https://example.com)" in converter.from_ast(ast)

    def test_handles_blockquotes(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("> quoted text")
        assert "> quoted text" in converter.from_ast(ast)

    def test_handles_unordered_lists(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("- item 1\n- item 2")
        out = converter.from_ast(ast)
        assert "- item 1" in out
        assert "- item 2" in out

    def test_handles_ordered_lists(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("1. first\n2. second")
        out = converter.from_ast(ast)
        assert "1." in out
        assert "2." in out

    def test_converts_at_mentions(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("Hello @someone")
        assert "<at>someone</at>" in converter.from_ast(ast)

    def test_handles_thematic_breaks(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("text\n\n---\n\nmore")
        assert "---" in converter.from_ast(ast)


class TestToAst:
    def test_strips_at_tag_to_plain_mention(self, converter: TeamsFormatConverter) -> None:
        assert "@John" in converter.extract_plain_text("<at>John</at> said hi")

    def test_converts_b_tags_to_bold(self, converter: TeamsFormatConverter) -> None:
        assert "**bold**" in converter.from_ast(converter.to_ast("<b>bold</b>"))

    def test_converts_strong_tags_to_bold(self, converter: TeamsFormatConverter) -> None:
        assert "**bold**" in converter.from_ast(converter.to_ast("<strong>bold</strong>"))

    def test_converts_i_tags_to_italic(self, converter: TeamsFormatConverter) -> None:
        assert "_italic_" in converter.from_ast(converter.to_ast("<i>italic</i>"))

    def test_converts_em_tags_to_italic(self, converter: TeamsFormatConverter) -> None:
        assert "_italic_" in converter.from_ast(converter.to_ast("<em>italic</em>"))

    def test_converts_s_tags_to_strikethrough(self, converter: TeamsFormatConverter) -> None:
        assert "~~struck~~" in converter.from_ast(converter.to_ast("<s>struck</s>"))

    def test_converts_a_tags_to_links(self, converter: TeamsFormatConverter) -> None:
        assert "[link](https://example.com)" in converter.from_ast(
            converter.to_ast('<a href="https://example.com">link</a>')
        )

    def test_converts_code_tags_to_inline_code(self, converter: TeamsFormatConverter) -> None:
        assert "`const x`" in converter.from_ast(converter.to_ast("<code>const x</code>"))

    def test_converts_pre_tags_to_code_blocks(self, converter: TeamsFormatConverter) -> None:
        out = converter.from_ast(converter.to_ast("<pre>const x = 1;</pre>"))
        assert "```" in out
        assert "const x = 1;" in out

    def test_strips_remaining_html_tags(self, converter: TeamsFormatConverter) -> None:
        assert converter.extract_plain_text("<div><span>hello</span></div>") == "hello"

    def test_decodes_html_entities(self, converter: TeamsFormatConverter) -> None:
        text = converter.extract_plain_text("&lt;b&gt;not bold&lt;/b&gt; &amp; &quot;quoted&quot;")
        assert "<b>" in text
        assert "&" in text
        assert '"quoted"' in text


class TestRenderPostable:
    def test_converts_mentions_in_plain_strings(self, converter: TeamsFormatConverter) -> None:
        assert converter.render_postable("Hello @user") == "Hello <at>user</at>"

    def test_converts_mentions_in_raw_messages(self, converter: TeamsFormatConverter) -> None:
        assert converter.render_postable({"raw": "Hello @user"}) == "Hello <at>user</at>"

    def test_renders_markdown_messages(self, converter: TeamsFormatConverter) -> None:
        out = converter.render_postable({"markdown": "Hello **world**"})
        assert "**world**" in out

    def test_renders_ast_messages(self, converter: TeamsFormatConverter) -> None:
        ast = converter.to_ast("Hello **world**")
        out = converter.render_postable({"ast": ast})
        assert "**world**" in out

    def test_handles_empty_message(self, converter: TeamsFormatConverter) -> None:
        assert converter.render_postable("") == ""


class TestExtractPlainText:
    def test_removes_bold_markers(self, converter: TeamsFormatConverter) -> None:
        assert converter.extract_plain_text("Hello **world**!") == "Hello world!"

    def test_removes_italic_markers(self, converter: TeamsFormatConverter) -> None:
        assert converter.extract_plain_text("Hello _world_!") == "Hello world!"

    def test_handles_empty_string(self, converter: TeamsFormatConverter) -> None:
        assert converter.extract_plain_text("") == ""

    def test_handles_plain_text(self, converter: TeamsFormatConverter) -> None:
        assert converter.extract_plain_text("Hello world") == "Hello world"

    def test_preserves_inline_code_content(self, converter: TeamsFormatConverter) -> None:
        assert "const x = 1" in converter.extract_plain_text("Use `const x = 1`")


class TestTableRendering:
    def test_renders_markdown_tables_as_gfm(self, converter: TeamsFormatConverter) -> None:
        out = converter.from_markdown("| Name | Age |\n|------|-----|\n| Alice | 30 |")
        assert "| Name | Age |" in out
        assert "| --- | --- |" in out
        assert "| Alice | 30 |" in out

    def test_renders_tables_with_pipe_syntax(self, converter: TeamsFormatConverter) -> None:
        out = converter.from_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        assert "|" in out
        assert "```" not in out
