"""Tests for the Google Chat format converter.

Mirrors upstream ``packages/adapter-gchat/src/markdown.test.ts``.
"""

from __future__ import annotations

from chat_adapter_gchat.markdown import GoogleChatFormatConverter


class TestFromAst:
    def setup_method(self) -> None:
        self.converter = GoogleChatFormatConverter()

    def test_converts_bold(self) -> None:
        ast = self.converter.to_ast("**bold text**")
        assert "*bold text*" in self.converter.from_ast(ast)

    def test_converts_italic(self) -> None:
        ast = self.converter.to_ast("_italic text_")
        assert "_italic text_" in self.converter.from_ast(ast)

    def test_converts_strikethrough(self) -> None:
        ast = self.converter.to_ast("~~strikethrough~~")
        assert "~strikethrough~" in self.converter.from_ast(ast)

    def test_preserves_inline_code(self) -> None:
        ast = self.converter.to_ast("Use `const x = 1`")
        assert "`const x = 1`" in self.converter.from_ast(ast)

    def test_handles_code_blocks(self) -> None:
        ast = self.converter.to_ast("```\nconst x = 1;\n```")
        output = self.converter.from_ast(ast)
        assert "```" in output
        assert "const x = 1;" in output

    def test_outputs_bare_url_when_link_text_matches(self) -> None:
        ast = self.converter.to_ast("[https://example.com](https://example.com)")
        assert "https://example.com" in self.converter.from_ast(ast)

    def test_outputs_gchat_link_syntax_when_label_differs(self) -> None:
        ast = self.converter.to_ast("[click here](https://example.com)")
        assert "<https://example.com|click here>" in self.converter.from_ast(ast)

    def test_handles_blockquotes(self) -> None:
        ast = self.converter.to_ast("> quoted text")
        assert "> quoted text" in self.converter.from_ast(ast)

    def test_handles_unordered_lists(self) -> None:
        ast = self.converter.to_ast("- item 1\n- item 2")
        result = self.converter.from_ast(ast)
        assert "item 1" in result
        assert "item 2" in result

    def test_handles_ordered_lists(self) -> None:
        ast = self.converter.to_ast("1. first\n2. second")
        result = self.converter.from_ast(ast)
        assert "1." in result
        assert "2." in result

    def test_indents_nested_unordered_lists(self) -> None:
        result = self.converter.from_markdown("- parent\n  - child 1\n  - child 2")
        assert result == "• parent\n  • child 1\n  • child 2"

    def test_indents_nested_ordered_lists(self) -> None:
        result = self.converter.from_markdown(
            "1. first\n   1. sub-first\n   2. sub-second\n2. second"
        )
        assert "1. first" in result
        assert "  1. sub-first" in result
        assert "  2. sub-second" in result
        assert "2. second" in result

    def test_handles_deeply_nested_lists(self) -> None:
        result = self.converter.from_markdown("- level 1\n  - level 2\n    - level 3")
        assert "• level 1" in result
        assert "  • level 2" in result
        assert "    • level 3" in result

    def test_keeps_siblings_at_same_indent(self) -> None:
        result = self.converter.from_markdown("- item 1\n- item 2\n- item 3")
        assert result == "• item 1\n• item 2\n• item 3"

    def test_mixed_ordered_and_unordered_nesting(self) -> None:
        result = self.converter.from_markdown("1. first\n   - sub a\n   - sub b\n2. second")
        assert "1. first" in result
        assert "  • sub a" in result
        assert "  • sub b" in result
        assert "2. second" in result

    def test_handles_line_breaks(self) -> None:
        ast = self.converter.to_ast("line1  \nline2")
        result = self.converter.from_ast(ast)
        assert "line1" in result
        assert "line2" in result

    def test_handles_thematic_breaks(self) -> None:
        ast = self.converter.to_ast("text\n\n---\n\nmore")
        assert "---" in self.converter.from_ast(ast)


class TestToAst:
    def setup_method(self) -> None:
        self.converter = GoogleChatFormatConverter()

    def test_parses_gchat_bold(self) -> None:
        ast = self.converter.to_ast("*bold*")
        assert ast["type"] == "root"

    def test_parses_gchat_strikethrough(self) -> None:
        ast = self.converter.to_ast("~struck~")
        assert ast["type"] == "root"

    def test_parses_code_blocks(self) -> None:
        ast = self.converter.to_ast("```\ncode\n```")
        assert ast["type"] == "root"


class TestExtractPlainText:
    def setup_method(self) -> None:
        self.converter = GoogleChatFormatConverter()

    def test_removes_formatting_markers(self) -> None:
        result = self.converter.extract_plain_text("*bold* _italic_ ~struck~")
        assert "bold" in result
        assert "italic" in result
        assert "struck" in result

    def test_handles_empty_string(self) -> None:
        assert self.converter.extract_plain_text("") == ""

    def test_handles_plain_text(self) -> None:
        assert self.converter.extract_plain_text("Hello world") == "Hello world"

    def test_handles_inline_code(self) -> None:
        result = self.converter.extract_plain_text("Use `const x = 1`")
        assert "const x = 1" in result


class TestRenderPostable:
    def setup_method(self) -> None:
        self.converter = GoogleChatFormatConverter()

    def test_renders_plain_string(self) -> None:
        assert self.converter.render_postable("Hello world") == "Hello world"

    def test_renders_raw_message(self) -> None:
        assert self.converter.render_postable({"raw": "raw text"}) == "raw text"

    def test_renders_markdown_message(self) -> None:
        result = self.converter.render_postable({"markdown": "**bold** text"})
        assert "bold" in result

    def test_renders_ast_message(self) -> None:
        ast = self.converter.to_ast("**bold**")
        result = self.converter.render_postable({"ast": ast})
        assert "bold" in result


class TestTableRendering:
    def setup_method(self) -> None:
        self.converter = GoogleChatFormatConverter()

    def test_renders_tables_as_code_blocks(self) -> None:
        result = self.converter.from_markdown("| Name | Age |\n|------|-----|\n| Alice | 30 |")
        assert "```" in result
        assert "Name" in result
        assert "Alice" in result
