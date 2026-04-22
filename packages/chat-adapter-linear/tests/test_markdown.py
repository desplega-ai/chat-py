"""Unit tests for :mod:`chat_adapter_linear.markdown`."""

from __future__ import annotations

from chat_adapter_linear.markdown import LinearFormatConverter


class TestToAst:
    def setup_method(self) -> None:
        self.converter = LinearFormatConverter()

    def test_parses_plain_text(self) -> None:
        ast = self.converter.to_ast("Hello world")
        assert ast["type"] == "root"
        assert len(ast["children"]) == 1

    def test_parses_bold_text(self) -> None:
        ast = self.converter.to_ast("**bold**")
        assert ast["type"] == "root"

    def test_parses_code_blocks(self) -> None:
        ast = self.converter.to_ast("```python\nprint(1)\n```")
        assert ast["type"] == "root"


class TestFromAst:
    def setup_method(self) -> None:
        self.converter = LinearFormatConverter()

    def test_renders_plain_text(self) -> None:
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "Hello"}],
                },
            ],
        }
        assert self.converter.from_ast(ast) == "Hello"

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


class TestRenderPostable:
    def setup_method(self) -> None:
        self.converter = LinearFormatConverter()

    def test_renders_string_directly(self) -> None:
        assert self.converter.render_postable("hello") == "hello"

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
                    "children": [{"type": "text", "value": "AST"}],
                },
            ],
        }
        assert self.converter.render_postable({"ast": ast}) == "AST"


class TestRoundtrip:
    def setup_method(self) -> None:
        self.converter = LinearFormatConverter()

    def test_roundtrips_simple_text(self) -> None:
        original = "Hello"
        assert self.converter.from_ast(self.converter.to_ast(original)).strip() == original

    def test_roundtrips_bold(self) -> None:
        original = "**bold**"
        result = self.converter.from_ast(self.converter.to_ast(original))
        assert "bold" in result
