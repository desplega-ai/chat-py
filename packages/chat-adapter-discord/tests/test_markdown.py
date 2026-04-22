"""Tests for the Discord markdown converter.

Mirrors upstream ``packages/adapter-discord/src/markdown.test.ts``.
"""

from __future__ import annotations

from chat_adapter_discord.markdown import DiscordFormatConverter

converter = DiscordFormatConverter()


# ---------------------------------------------------------------------------
# fromAst (AST -> Discord markdown)
# ---------------------------------------------------------------------------


class TestFromAst:
    def test_converts_bold(self) -> None:
        ast = converter.to_ast("**bold text**")
        assert "**bold text**" in converter.from_ast(ast)

    def test_converts_italic(self) -> None:
        ast = converter.to_ast("*italic text*")
        assert "*italic text*" in converter.from_ast(ast)

    def test_converts_strikethrough(self) -> None:
        ast = converter.to_ast("~~strikethrough~~")
        assert "~~strikethrough~~" in converter.from_ast(ast)

    def test_converts_links(self) -> None:
        ast = converter.to_ast("[link text](https://example.com)")
        assert "[link text](https://example.com)" in converter.from_ast(ast)

    def test_preserves_inline_code(self) -> None:
        ast = converter.to_ast("Use `const x = 1`")
        assert "`const x = 1`" in converter.from_ast(ast)

    def test_handles_code_blocks(self) -> None:
        ast = converter.to_ast("```js\nconst x = 1;\n```")
        output = converter.from_ast(ast)
        assert "```" in output
        assert "const x = 1;" in output

    def test_handles_mixed_formatting(self) -> None:
        ast = converter.to_ast("**Bold** and *italic* and [link](https://x.com)")
        output = converter.from_ast(ast)
        assert "**Bold**" in output
        assert "*italic*" in output
        assert "[link](https://x.com)" in output

    def test_converts_at_mentions_to_discord(self) -> None:
        ast = converter.to_ast("Hello @someone")
        assert "<@someone>" in converter.from_ast(ast)


# ---------------------------------------------------------------------------
# toAst / extract_plain_text (Discord markdown -> AST)
# ---------------------------------------------------------------------------


class TestToAst:
    def test_returns_a_root_node(self) -> None:
        ast = converter.to_ast("Hello **world**!")
        assert ast is not None
        assert ast.get("type") == "root"

    def test_converts_user_mentions(self) -> None:
        text = converter.extract_plain_text("Hello <@123456789>")
        assert text == "Hello @123456789"

    def test_converts_user_mentions_with_nickname_marker(self) -> None:
        text = converter.extract_plain_text("Hello <@!123456789>")
        assert text == "Hello @123456789"

    def test_converts_channel_mentions(self) -> None:
        text = converter.extract_plain_text("Check <#987654321>")
        assert text == "Check #987654321"

    def test_converts_role_mentions(self) -> None:
        text = converter.extract_plain_text("Hey <@&111222333>")
        assert text == "Hey @&111222333"

    def test_converts_custom_emoji(self) -> None:
        text = converter.extract_plain_text("Nice <:thumbsup:123>")
        assert text == "Nice :thumbsup:"

    def test_converts_animated_custom_emoji(self) -> None:
        text = converter.extract_plain_text("Cool <a:wave:456>")
        assert text == "Cool :wave:"

    def test_handles_spoiler_tags(self) -> None:
        text = converter.extract_plain_text("Secret ||hidden text||")
        assert "hidden text" in text


# ---------------------------------------------------------------------------
# extract_plain_text
# ---------------------------------------------------------------------------


class TestExtractPlainText:
    def test_removes_bold_markers(self) -> None:
        assert converter.extract_plain_text("Hello **world**!") == "Hello world!"

    def test_removes_italic_markers(self) -> None:
        assert converter.extract_plain_text("Hello *world*!") == "Hello world!"

    def test_removes_strikethrough_markers(self) -> None:
        assert converter.extract_plain_text("Hello ~~world~~!") == "Hello world!"

    def test_extracts_link_text(self) -> None:
        assert converter.extract_plain_text("Check [this](https://example.com)") == "Check this"

    def test_formats_user_mentions(self) -> None:
        result = converter.extract_plain_text("Hey <@U123>!")
        assert "@U123" in result

    def test_handles_complex_messages(self) -> None:
        result = converter.extract_plain_text(
            "**Bold** and *italic* with [link](https://x.com) and <@U123>"
        )
        assert "Bold" in result
        assert "italic" in result
        assert "link" in result
        assert "@U123" in result
        assert "**" not in result
        assert "<@" not in result

    def test_handles_inline_code(self) -> None:
        result = converter.extract_plain_text("Use `const x = 1`")
        assert "const x = 1" in result

    def test_handles_code_blocks(self) -> None:
        result = converter.extract_plain_text("```js\nconst x = 1;\n```")
        assert "const x = 1;" in result

    def test_handles_empty_string(self) -> None:
        assert converter.extract_plain_text("") == ""

    def test_handles_plain_text(self) -> None:
        assert converter.extract_plain_text("Hello world") == "Hello world"


# ---------------------------------------------------------------------------
# render_postable
# ---------------------------------------------------------------------------


class TestRenderPostable:
    def test_renders_plain_string_with_mention_conversion(self) -> None:
        assert converter.render_postable("Hello @user") == "Hello <@user>"

    def test_renders_raw_message_with_mention_conversion(self) -> None:
        assert converter.render_postable({"raw": "Hello @user"}) == "Hello <@user>"

    def test_renders_markdown_message(self) -> None:
        result = converter.render_postable({"markdown": "Hello **world** @user"})
        assert "**world**" in result
        assert "<@user>" in result

    def test_handles_empty_message(self) -> None:
        assert converter.render_postable("") == ""

    def test_renders_ast_message(self) -> None:
        ast = converter.to_ast("Hello **world**")
        result = converter.render_postable({"ast": ast})
        assert "**world**" in result


# ---------------------------------------------------------------------------
# blockquotes / lists / thematic break / tables
# ---------------------------------------------------------------------------


class TestBlockquotes:
    def test_handles_blockquotes(self) -> None:
        ast = converter.to_ast("> quoted text")
        assert "> quoted text" in converter.from_ast(ast)


class TestLists:
    def test_handles_unordered_lists(self) -> None:
        ast = converter.to_ast("- item 1\n- item 2")
        output = converter.from_ast(ast)
        assert "- item 1" in output
        assert "- item 2" in output

    def test_handles_ordered_lists(self) -> None:
        ast = converter.to_ast("1. item 1\n2. item 2")
        output = converter.from_ast(ast)
        assert "1." in output
        assert "2." in output


class TestNestedLists:
    def test_indents_nested_unordered_lists(self) -> None:
        result = converter.from_markdown("- parent\n  - child 1\n  - child 2")
        assert result == "- parent\n  - child 1\n  - child 2"

    def test_indents_nested_ordered_lists(self) -> None:
        result = converter.from_markdown("1. first\n   1. sub-first\n   2. sub-second\n2. second")
        assert "1. first" in result
        assert "  1. sub-first" in result
        assert "  2. sub-second" in result
        assert "2. second" in result

    def test_handles_deeply_nested_lists(self) -> None:
        result = converter.from_markdown("- level 1\n  - level 2\n    - level 3")
        assert "- level 1" in result
        assert "  - level 2" in result
        assert "    - level 3" in result

    def test_keeps_sibling_items_at_the_same_indent_level(self) -> None:
        result = converter.from_markdown("- item 1\n- item 2\n- item 3")
        assert result == "- item 1\n- item 2\n- item 3"

    def test_handles_mixed_ordered_and_unordered_nesting(self) -> None:
        result = converter.from_markdown("1. first\n   - sub a\n   - sub b\n2. second")
        assert "1. first" in result
        assert "  - sub a" in result
        assert "  - sub b" in result
        assert "2. second" in result


class TestThematicBreak:
    def test_handles_thematic_break(self) -> None:
        ast = converter.to_ast("text\n\n---\n\nmore text")
        assert "---" in converter.from_ast(ast)


class TestTableRendering:
    def test_renders_markdown_tables_as_code_blocks(self) -> None:
        result = converter.from_markdown("| Name | Age |\n|------|-----|\n| Alice | 30 |")
        assert "```" in result
        assert "Name" in result
        assert "Alice" in result
