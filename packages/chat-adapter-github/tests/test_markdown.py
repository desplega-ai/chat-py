"""Unit tests for :mod:`chat_adapter_github.markdown`.

Mirrors upstream ``packages/adapter-github/src/markdown.test.ts``.
"""

from __future__ import annotations

from chat_adapter_github.markdown import GitHubFormatConverter


class TestToAst:
    def setup_method(self) -> None:
        self.converter = GitHubFormatConverter()

    def test_parses_plain_text(self) -> None:
        ast = self.converter.to_ast("Hello world")
        assert ast["type"] == "root"
        assert len(ast["children"]) == 1

    def test_parses_bold_text(self) -> None:
        ast = self.converter.to_ast("**bold text**")
        assert ast["type"] == "root"
        assert ast["children"][0]["type"] == "paragraph"

    def test_parses_mentions(self) -> None:
        _ast = self.converter.to_ast("Hey @username, check this out")
        text = self.converter.extract_plain_text("Hey @username, check this out")
        assert "@username" in text

    def test_parses_code_blocks(self) -> None:
        ast = self.converter.to_ast("```javascript\nconsole.log('hello');\n```")
        assert ast["type"] == "root"

    def test_parses_links(self) -> None:
        ast = self.converter.to_ast("[link text](https://example.com)")
        assert ast["type"] == "root"

    def test_parses_strikethrough(self) -> None:
        ast = self.converter.to_ast("~~deleted~~")
        assert ast["type"] == "root"


class TestFromAst:
    def setup_method(self) -> None:
        self.converter = GitHubFormatConverter()

    def test_renders_plain_text(self) -> None:
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "Hello world"}],
                },
            ],
        }
        assert self.converter.from_ast(ast) == "Hello world"

    def test_renders_bold_text(self) -> None:
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [
                        {
                            "type": "strong",
                            "children": [{"type": "text", "value": "bold"}],
                        },
                    ],
                },
            ],
        }
        assert self.converter.from_ast(ast) == "**bold**"

    def test_renders_italic_text(self) -> None:
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [
                        {
                            "type": "emphasis",
                            "children": [{"type": "text", "value": "italic"}],
                        },
                    ],
                },
            ],
        }
        assert self.converter.from_ast(ast) == "*italic*"


class TestExtractPlainText:
    def setup_method(self) -> None:
        self.converter = GitHubFormatConverter()

    def test_extracts_text_from_markdown(self) -> None:
        assert self.converter.extract_plain_text("**bold** and _italic_") == ("bold and italic")

    def test_preserves_mentions(self) -> None:
        result = self.converter.extract_plain_text("Hey @user, **thanks**!")
        assert "@user" in result
        assert "thanks" in result

    def test_extracts_text_from_code_blocks(self) -> None:
        result = self.converter.extract_plain_text("```\ncode\n```")
        assert "code" in result


class TestRenderPostable:
    def setup_method(self) -> None:
        self.converter = GitHubFormatConverter()

    def test_renders_string_directly(self) -> None:
        assert self.converter.render_postable("Hello world") == "Hello world"

    def test_renders_raw_message(self) -> None:
        assert self.converter.render_postable({"raw": "Raw content"}) == "Raw content"

    def test_renders_markdown_message(self) -> None:
        assert self.converter.render_postable({"markdown": "**bold**"}) == "**bold**"

    def test_renders_ast_message(self) -> None:
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "AST content"}],
                },
            ],
        }
        assert self.converter.render_postable({"ast": ast}) == "AST content"


class TestRoundtrip:
    def setup_method(self) -> None:
        self.converter = GitHubFormatConverter()

    def test_roundtrips_simple_text(self) -> None:
        original = "Hello world"
        ast = self.converter.to_ast(original)
        assert self.converter.from_ast(ast).strip() == original

    def test_roundtrips_markdown_with_formatting(self) -> None:
        original = "**bold** and *italic*"
        ast = self.converter.to_ast(original)
        result = self.converter.from_ast(ast)
        assert "bold" in result
        assert "italic" in result
