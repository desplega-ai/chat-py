"""Tests for :mod:`chat.markdown` — ported from upstream ``markdown.test.ts``.

Card-dependent tests (``renderPostable({card})``, ``cardToFallbackText``) are
deferred to part B when ``cards.py`` lands. They're ported here using dict
literals matching the expected card shape so they exercise the same logic the
upstream tests do against their ``Card()`` builder output.
"""

from __future__ import annotations

from typing import Any

import pytest
from chat.markdown import (
    BaseFormatConverter,
    MdastRoot,
    blockquote,
    code_block,
    emphasis,
    get_node_children,
    get_node_value,
    inline_code,
    is_blockquote_node,
    is_code_node,
    is_delete_node,
    is_emphasis_node,
    is_inline_code_node,
    is_link_node,
    is_list_item_node,
    is_list_node,
    is_paragraph_node,
    is_strong_node,
    is_table_cell_node,
    is_table_node,
    is_table_row_node,
    is_text_node,
    link,
    markdown_to_plain_text,
    paragraph,
    parse_markdown,
    root,
    strikethrough,
    stringify_markdown,
    strong,
    table_element_to_ascii,
    table_to_ascii,
    text,
    to_plain_text,
    walk_ast,
)

# ============================================================================
# parse_markdown
# ============================================================================


class TestParseMarkdown:
    def test_parses_plain_text(self) -> None:
        ast = parse_markdown("Hello, world!")
        assert ast["type"] == "root"
        assert len(ast["children"]) == 1
        assert ast["children"][0]["type"] == "paragraph"

    def test_parses_bold_text(self) -> None:
        ast = parse_markdown("**bold**")
        para = ast["children"][0]
        assert para["children"][0]["type"] == "strong"

    def test_parses_italic_text(self) -> None:
        ast = parse_markdown("_italic_")
        para = ast["children"][0]
        assert para["children"][0]["type"] == "emphasis"

    def test_parses_strikethrough(self) -> None:
        ast = parse_markdown("~~deleted~~")
        para = ast["children"][0]
        assert para["children"][0]["type"] == "delete"

    def test_parses_inline_code(self) -> None:
        ast = parse_markdown("`code`")
        para = ast["children"][0]
        assert para["children"][0]["type"] == "inlineCode"

    def test_parses_code_blocks(self) -> None:
        ast = parse_markdown("```javascript\nconst x = 1;\n```")
        code = ast["children"][0]
        assert code["type"] == "code"
        assert code["lang"] == "javascript"
        assert code["value"] == "const x = 1;"

    def test_parses_links(self) -> None:
        ast = parse_markdown("[text](https://example.com)")
        para = ast["children"][0]
        assert para["children"][0]["type"] == "link"
        assert para["children"][0]["url"] == "https://example.com"

    def test_parses_blockquotes(self) -> None:
        ast = parse_markdown("> quoted text")
        assert ast["children"][0]["type"] == "blockquote"

    def test_parses_unordered_lists(self) -> None:
        ast = parse_markdown("- item 1\n- item 2")
        node = ast["children"][0]
        assert node["type"] == "list"
        assert node["ordered"] is False

    def test_parses_ordered_lists(self) -> None:
        ast = parse_markdown("1. first\n2. second")
        node = ast["children"][0]
        assert node["type"] == "list"
        assert node["ordered"] is True

    def test_handles_nested_formatting(self) -> None:
        ast = parse_markdown("**_bold italic_**")
        para = ast["children"][0]
        assert para["children"][0]["type"] == "strong"
        assert para["children"][0]["children"][0]["type"] == "emphasis"

    def test_handles_empty_string(self) -> None:
        ast = parse_markdown("")
        assert ast["type"] == "root"
        assert ast["children"] == []

    def test_handles_multiple_paragraphs(self) -> None:
        ast = parse_markdown("First paragraph.\n\nSecond paragraph.")
        assert len(ast["children"]) == 2
        assert ast["children"][0]["type"] == "paragraph"
        assert ast["children"][1]["type"] == "paragraph"


# ============================================================================
# stringify_markdown
# ============================================================================


class TestStringifyMarkdown:
    def test_stringifies_simple_ast(self) -> None:
        ast = root([paragraph([text("Hello")])])
        assert stringify_markdown(ast).strip() == "Hello"

    def test_stringifies_bold(self) -> None:
        ast = root([paragraph([strong([text("bold")])])])
        assert stringify_markdown(ast).strip() == "**bold**"

    def test_stringifies_italic(self) -> None:
        ast = root([paragraph([emphasis([text("italic")])])])
        assert stringify_markdown(ast).strip() == "*italic*"

    def test_stringifies_inline_code(self) -> None:
        ast = root([paragraph([inline_code("code")])])
        assert stringify_markdown(ast).strip() == "`code`"

    def test_stringifies_links(self) -> None:
        ast = root([paragraph([link("https://example.com", [text("link")])])])
        assert stringify_markdown(ast).strip() == "[link](https://example.com)"

    def test_round_trips_markdown(self) -> None:
        original = "**bold** and _italic_ and `code`"
        ast = parse_markdown(original)
        result = stringify_markdown(ast)
        reparsed = parse_markdown(result)
        assert len(reparsed["children"]) == len(ast["children"])


# ============================================================================
# to_plain_text
# ============================================================================


class TestToPlainText:
    def test_extracts_plain_text(self) -> None:
        ast = parse_markdown("**bold** and _italic_")
        assert to_plain_text(ast) == "bold and italic"

    def test_extracts_from_code_blocks(self) -> None:
        ast = parse_markdown("```\ncode block\n```")
        assert to_plain_text(ast) == "code block"

    def test_extracts_from_links(self) -> None:
        ast = parse_markdown("[link text](https://example.com)")
        assert to_plain_text(ast) == "link text"

    def test_empty_ast(self) -> None:
        assert to_plain_text(root([])) == ""


# ============================================================================
# markdown_to_plain_text
# ============================================================================


class TestMarkdownToPlainText:
    def test_converts_directly(self) -> None:
        assert markdown_to_plain_text("**bold** and _italic_") == "bold and italic"

    def test_handles_complex(self) -> None:
        result = markdown_to_plain_text("# Heading\n\nParagraph with `code`.")
        assert "Heading" in result
        assert "Paragraph with code" in result


# ============================================================================
# walk_ast
# ============================================================================


class TestWalkAst:
    def test_visits_all_nodes(self) -> None:
        ast = parse_markdown("**bold** and _italic_")
        visited: list[str] = []

        def visitor(node: dict[str, Any]) -> dict[str, Any] | None:
            visited.append(node["type"])
            return node

        walk_ast(ast, visitor)
        for t in ("paragraph", "strong", "emphasis", "text"):
            assert t in visited

    def test_filters_nodes(self) -> None:
        ast = parse_markdown("**bold** and _italic_")

        def visitor(node: dict[str, Any]) -> dict[str, Any] | None:
            if node["type"] == "strong":
                return None
            return node

        filtered = walk_ast(ast, visitor)
        plain = to_plain_text(filtered)
        assert "bold" not in plain
        assert "italic" in plain

    def test_transforms_nodes(self) -> None:
        ast = root([paragraph([text("hello")])])

        def visitor(node: dict[str, Any]) -> dict[str, Any] | None:
            if node["type"] == "text":
                return {**node, "value": node["value"].upper()}
            return node

        transformed = walk_ast(ast, visitor)
        assert to_plain_text(transformed) == "HELLO"

    def test_deeply_nested(self) -> None:
        ast = parse_markdown("> **_nested_ text**")
        types: list[str] = []

        def visitor(node: dict[str, Any]) -> dict[str, Any] | None:
            types.append(node["type"])
            return node

        walk_ast(ast, visitor)
        for t in ("blockquote", "strong", "emphasis"):
            assert t in types

    def test_empty_ast(self) -> None:
        ast = root([])
        visited: list[str] = []

        def visitor(node: dict[str, Any]) -> dict[str, Any] | None:
            visited.append(node["type"])
            return node

        walk_ast(ast, visitor)
        assert visited == []


# ============================================================================
# AST builders
# ============================================================================


class TestBuilders:
    def test_text_node(self) -> None:
        node = text("hello")
        assert node["type"] == "text"
        assert node["value"] == "hello"

    def test_text_empty(self) -> None:
        assert text("")["value"] == ""

    def test_text_special_chars(self) -> None:
        node = text('hello & world < > "')
        assert node["value"] == 'hello & world < > "'

    def test_strong_node(self) -> None:
        node = strong([text("bold")])
        assert node["type"] == "strong"
        assert len(node["children"]) == 1

    def test_strong_nested(self) -> None:
        node = strong([emphasis([text("bold italic")])])
        assert node["children"][0]["type"] == "emphasis"

    def test_emphasis_node(self) -> None:
        node = emphasis([text("italic")])
        assert node["type"] == "emphasis"
        assert len(node["children"]) == 1

    def test_strikethrough_node(self) -> None:
        node = strikethrough([text("deleted")])
        assert node["type"] == "delete"
        assert len(node["children"]) == 1

    def test_inline_code_node(self) -> None:
        node = inline_code("const x = 1")
        assert node["type"] == "inlineCode"
        assert node["value"] == "const x = 1"

    def test_code_block_with_lang(self) -> None:
        node = code_block("function() {}", "javascript")
        assert node["type"] == "code"
        assert node["value"] == "function() {}"
        assert node["lang"] == "javascript"

    def test_code_block_missing_lang(self) -> None:
        node = code_block("plain code")
        assert node["lang"] is None

    def test_link_node(self) -> None:
        node = link("https://example.com", [text("Example")])
        assert node["type"] == "link"
        assert node["url"] == "https://example.com"
        assert len(node["children"]) == 1

    def test_link_with_title(self) -> None:
        node = link("https://example.com", [text("Example")], "Title")
        assert node["title"] == "Title"

    def test_blockquote_node(self) -> None:
        node = blockquote([paragraph([text("quoted")])])
        assert node["type"] == "blockquote"
        assert len(node["children"]) == 1

    def test_paragraph_node(self) -> None:
        node = paragraph([text("content")])
        assert node["type"] == "paragraph"
        assert len(node["children"]) == 1

    def test_root_node(self) -> None:
        node = root([paragraph([text("content")])])
        assert node["type"] == "root"
        assert len(node["children"]) == 1

    def test_root_empty(self) -> None:
        assert root([])["children"] == []


# ============================================================================
# BaseFormatConverter
# ============================================================================


class _TestConverter(BaseFormatConverter):
    def from_ast(self, ast: MdastRoot) -> str:
        return to_plain_text(ast)

    def to_ast(self, platform_text: str) -> MdastRoot:
        return parse_markdown(platform_text)


class TestBaseFormatConverter:
    def setup_method(self) -> None:
        self.converter = _TestConverter()

    def test_extract_plain_text(self) -> None:
        assert self.converter.extract_plain_text("**bold** text") == "bold text"

    def test_from_markdown(self) -> None:
        assert self.converter.from_markdown("**bold**") == "bold"

    def test_to_markdown(self) -> None:
        assert self.converter.to_markdown("plain text").strip() == "plain text"

    def test_render_postable_string(self) -> None:
        assert self.converter.render_postable("plain string") == "plain string"

    def test_render_postable_raw(self) -> None:
        assert self.converter.render_postable({"raw": "raw text"}) == "raw text"

    def test_render_postable_markdown(self) -> None:
        assert self.converter.render_postable({"markdown": "**bold**"}) == "bold"

    def test_render_postable_ast(self) -> None:
        ast = root([paragraph([text("from ast")])])
        assert self.converter.render_postable({"ast": ast}) == "from ast"

    def test_render_postable_invalid(self) -> None:
        with pytest.raises(ValueError):
            self.converter.render_postable({"invalid": True})

    def test_deprecated_to_plain_text(self) -> None:
        assert self.converter.to_plain_text("**bold** text") == "bold text"


# ============================================================================
# BaseFormatConverter — card-shape rendering
#
# Upstream tests construct cards with ``Card()``/``Text()``/etc. builders from
# ``cards.ts``. Part B will port ``cards.py``; until then we use the same dict
# shape builders produce so the card → fallback-text logic is still exercised.
# ============================================================================


def _card(**kwargs: Any) -> dict[str, Any]:
    return {"type": "card", **kwargs}


def _card_text(content: str) -> dict[str, Any]:
    return {"type": "text", "content": content}


def _card_fields(fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "fields", "children": fields}


def _card_field(label: str, value: str) -> dict[str, Any]:
    return {"label": label, "value": value}


def _card_actions(buttons: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "actions", "children": buttons}


def _card_button(button_id: str, label: str) -> dict[str, Any]:
    return {"type": "button", "id": button_id, "label": label}


def _card_section(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "section", "children": children}


def _card_divider() -> dict[str, Any]:
    return {"type": "divider"}


def _card_table(headers: list[str], rows: list[list[str]]) -> dict[str, Any]:
    return {"type": "table", "headers": headers, "rows": rows}


class TestRenderPostableCards:
    def setup_method(self) -> None:
        self.converter = _TestConverter()

    def test_card_with_fallback_text(self) -> None:
        card = _card(title="Title", children=[_card_text("Content")])
        result = self.converter.render_postable({"card": card, "fallbackText": "Custom fallback"})
        assert result == "Custom fallback"

    def test_generates_fallback_from_card(self) -> None:
        card = _card(
            title="Order Status",
            subtitle="Your order details",
            children=[_card_text("Processing your order...")],
        )
        result = self.converter.render_postable({"card": card})
        assert "Order Status" in result
        assert "Your order details" in result
        assert "Processing your order..." in result

    def test_card_with_actions_excluded(self) -> None:
        card = _card(
            title="Confirm",
            children=[_card_actions([_card_button("yes", "Yes"), _card_button("no", "No")])],
        )
        result = self.converter.render_postable({"card": card})
        assert "Confirm" in result
        assert "[Yes]" not in result
        assert "[No]" not in result

    def test_card_with_fields(self) -> None:
        card = _card(
            children=[
                _card_fields(
                    [
                        _card_field("Name", "John"),
                        _card_field("Email", "john@example.com"),
                    ]
                )
            ]
        )
        result = self.converter.render_postable({"card": card})
        assert "**Name**: John" in result
        assert "**Email**: john@example.com" in result

    def test_direct_card_element(self) -> None:
        card = _card(title="Direct Card")
        result = self.converter.render_postable(card)
        assert "Direct Card" in result

    def test_card_with_table(self) -> None:
        card = _card(children=[_card_table(["Name", "Age"], [["Alice", "30"], ["Bob", "25"]])])
        result = self.converter.render_postable({"card": card})
        for expected in ("Name", "Age", "Alice", "30"):
            assert expected in result

    def test_card_with_section(self) -> None:
        card = _card(
            children=[_card_section([_card_text("Section content"), _card_text("More content")])]
        )
        result = self.converter.render_postable({"card": card})
        assert "Section content" in result
        assert "More content" in result

    def test_card_title_only(self) -> None:
        card = _card(title="Title Only")
        assert self.converter.render_postable({"card": card}) == "**Title Only**"

    def test_card_with_divider_only(self) -> None:
        card = _card(title="With Divider", children=[_card_divider()])
        assert self.converter.render_postable({"card": card}) == "**With Divider**"

    def test_card_mixed_children(self) -> None:
        card = _card(
            title="Mixed",
            children=[
                _card_text("Visible text"),
                _card_actions([_card_button("ok", "OK")]),
                _card_fields([_card_field("Key", "Val")]),
            ],
        )
        result = self.converter.render_postable({"card": card})
        assert "Visible text" in result
        assert "OK" not in result
        assert "**Key**: Val" in result


# ============================================================================
# _from_ast_with_node_converter
# ============================================================================


class _NodeConverterTest(BaseFormatConverter):
    def from_ast(self, ast: MdastRoot) -> str:
        def convert(node: dict[str, Any]) -> str:
            if node["type"] == "paragraph":
                return f"[para:{to_plain_text({'type': 'root', 'children': [node]})}]"
            return to_plain_text({"type": "root", "children": [node]})

        return self._from_ast_with_node_converter(ast, convert)

    def to_ast(self, platform_text: str) -> MdastRoot:
        return parse_markdown(platform_text)


class TestFromAstWithNodeConverter:
    def test_joins_paragraphs_with_double_newlines(self) -> None:
        c = _NodeConverterTest()
        ast = root([paragraph([text("First")]), paragraph([text("Second")])])
        assert c.from_ast(ast) == "[para:First]\n\n[para:Second]"

    def test_single_paragraph(self) -> None:
        c = _NodeConverterTest()
        ast = root([paragraph([text("Only")])])
        assert c.from_ast(ast) == "[para:Only]"

    def test_empty_ast(self) -> None:
        c = _NodeConverterTest()
        assert c.from_ast(root([])) == ""


# ============================================================================
# Tables
# ============================================================================


class TestTableParsing:
    def test_parses_gfm_table(self) -> None:
        ast = parse_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        assert ast["children"][0]["type"] == "table"

    def test_multiple_rows(self) -> None:
        ast = parse_markdown("| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |")
        table = ast["children"][0]
        assert table["type"] == "table"
        assert len(table["children"]) == 3


class TestTableTypeGuards:
    def test_is_table_node(self) -> None:
        ast = parse_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        assert is_table_node(ast["children"][0]) is True
        assert is_table_node({"type": "paragraph", "children": []}) is False

    def test_is_table_row_node(self) -> None:
        ast = parse_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        table = ast["children"][0]
        assert is_table_row_node(table["children"][0]) is True

    def test_is_table_cell_node(self) -> None:
        ast = parse_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        row = ast["children"][0]["children"][0]
        assert is_table_cell_node(row["children"][0]) is True


class TestTableToAscii:
    def test_simple_table(self) -> None:
        ast = parse_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
        table = ast["children"][0]
        result = table_to_ascii(table)
        assert "A" in result and "B" in result and "1" in result and "2" in result
        assert "-|" in result

    def test_column_padding(self) -> None:
        ast = parse_markdown("| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |")
        table = ast["children"][0]
        lines = table_to_ascii(table).split("\n")
        assert lines[0] == "Name  | Age"
        assert lines[1] == "------|----"
        assert lines[2] == "Alice | 30"
        assert lines[3] == "Bob   | 25"

    def test_empty_table(self) -> None:
        assert table_to_ascii({"type": "table", "children": []}) == ""


class TestTableElementToAscii:
    def test_renders_headers_and_rows(self) -> None:
        result = table_element_to_ascii(["Name", "Age"], [["Alice", "30"], ["Bob", "25"]])
        lines = result.split("\n")
        assert len(lines) == 4
        assert "Name" in lines[0] and "Age" in lines[0]
        assert "---" in lines[1]
        assert "Alice" in lines[2]
        assert "Bob" in lines[3]

    def test_column_padding(self) -> None:
        result = table_element_to_ascii(
            ["Name", "Age", "Role"],
            [["Alice", "30", "Engineer"], ["Bob", "25", "Designer"]],
        )
        lines = result.split("\n")
        assert lines[0] == "Name  | Age | Role"
        assert lines[2] == "Alice | 30  | Engineer"
        assert lines[3] == "Bob   | 25  | Designer"


# ============================================================================
# Type guards
# ============================================================================


class TestTypeGuards:
    def test_is_text_node(self) -> None:
        assert is_text_node({"type": "text", "value": "hello"}) is True
        assert is_text_node({"type": "paragraph", "children": []}) is False

    def test_is_paragraph_node(self) -> None:
        assert is_paragraph_node({"type": "paragraph", "children": []}) is True
        assert is_paragraph_node({"type": "text", "value": "hello"}) is False

    def test_is_strong_node(self) -> None:
        assert is_strong_node({"type": "strong", "children": []}) is True
        assert is_strong_node({"type": "emphasis", "children": []}) is False

    def test_is_emphasis_node(self) -> None:
        assert is_emphasis_node({"type": "emphasis", "children": []}) is True
        assert is_emphasis_node({"type": "text", "value": "hi"}) is False

    def test_is_delete_node(self) -> None:
        assert is_delete_node({"type": "delete", "children": []}) is True
        assert is_delete_node({"type": "text", "value": "hi"}) is False

    def test_is_inline_code_node(self) -> None:
        assert is_inline_code_node({"type": "inlineCode", "value": "code"}) is True
        assert is_inline_code_node({"type": "code", "value": "block"}) is False

    def test_is_code_node(self) -> None:
        assert is_code_node({"type": "code", "value": "block"}) is True
        assert is_code_node({"type": "inlineCode", "value": "code"}) is False

    def test_is_link_node(self) -> None:
        assert is_link_node({"type": "link", "url": "https://x", "children": []}) is True
        assert is_link_node({"type": "text", "value": "hi"}) is False

    def test_is_blockquote_node(self) -> None:
        assert is_blockquote_node({"type": "blockquote", "children": []}) is True
        assert is_blockquote_node({"type": "text", "value": "hi"}) is False

    def test_is_list_node(self) -> None:
        ast = parse_markdown("- item 1\n- item 2")
        assert is_list_node(ast["children"][0]) is True
        assert is_list_node({"type": "text", "value": "hi"}) is False

    def test_is_list_item_node(self) -> None:
        ast = parse_markdown("- item 1")
        list_node = ast["children"][0]
        assert is_list_item_node(list_node["children"][0]) is True
        assert is_list_item_node({"type": "text", "value": "hi"}) is False


# ============================================================================
# get_node_children / get_node_value
# ============================================================================


class TestGetNodeChildren:
    def test_paragraph(self) -> None:
        node = paragraph([text("hello"), text(" world")])
        assert len(get_node_children(node)) == 2
        assert get_node_children(node)[0]["value"] == "hello"

    def test_strong(self) -> None:
        assert len(get_node_children(strong([text("bold")]))) == 1

    def test_text_no_children(self) -> None:
        assert get_node_children(text("hello")) == []

    def test_inline_code_no_children(self) -> None:
        assert get_node_children(inline_code("code")) == []

    def test_code_block_no_children(self) -> None:
        assert get_node_children(code_block("code", "js")) == []

    def test_blockquote(self) -> None:
        node = blockquote([paragraph([text("quoted")])])
        children = get_node_children(node)
        assert len(children) == 1
        assert children[0]["type"] == "paragraph"

    def test_emphasis(self) -> None:
        assert len(get_node_children(emphasis([text("italic")]))) == 1

    def test_link(self) -> None:
        node = link("https://example.com", [text("link")])
        assert len(get_node_children(node)) == 1


class TestGetNodeValue:
    def test_text(self) -> None:
        assert get_node_value(text("hello")) == "hello"

    def test_inline_code(self) -> None:
        assert get_node_value(inline_code("const x = 1")) == "const x = 1"

    def test_code_block(self) -> None:
        assert get_node_value(code_block("function() {}")) == "function() {}"

    def test_paragraph_no_value(self) -> None:
        assert get_node_value(paragraph([text("hello")])) == ""

    def test_strong_no_value(self) -> None:
        assert get_node_value(strong([text("bold")])) == ""

    def test_emphasis_no_value(self) -> None:
        assert get_node_value(emphasis([text("italic")])) == ""

    def test_blockquote_no_value(self) -> None:
        assert get_node_value(blockquote([paragraph([text("quoted")])])) == ""

    def test_text_empty_string(self) -> None:
        assert get_node_value(text("")) == ""


# ============================================================================
# Edge cases
# ============================================================================


class TestParseEdgeCases:
    def test_whitespace_only(self) -> None:
        ast = parse_markdown("   ")
        assert ast["type"] == "root"
        assert len(ast["children"]) >= 0

    def test_special_chars(self) -> None:
        ast = parse_markdown('Hello <world> & "quotes"')
        assert ast["type"] == "root"
        plain = to_plain_text(ast)
        assert "Hello" in plain

    def test_long_input(self) -> None:
        ast = parse_markdown("word " * 1000)
        assert ast["type"] == "root"
        assert len(ast["children"]) > 0

    def test_mixed_heading_levels(self) -> None:
        ast = parse_markdown("# H1\n## H2\n### H3")
        assert len(ast["children"]) == 3
        for child in ast["children"]:
            assert child["type"] == "heading"

    def test_thematic_break(self) -> None:
        ast = parse_markdown("before\n\n---\n\nafter")
        types = [c["type"] for c in ast["children"]]
        assert "thematicBreak" in types
