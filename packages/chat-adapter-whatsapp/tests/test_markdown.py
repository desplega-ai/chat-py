"""Tests for the WhatsApp markdown converter."""

from __future__ import annotations

from chat_adapter_whatsapp import (
    WhatsAppFormatConverter,
    from_whatsapp_format,
    to_whatsapp_format,
)

# ---------------------------------------------------------------------------
# to_whatsapp_format / from_whatsapp_format
# ---------------------------------------------------------------------------


def test_to_whatsapp_format_bold() -> None:
    assert to_whatsapp_format("**bold**") == "*bold*"


def test_to_whatsapp_format_strikethrough() -> None:
    assert to_whatsapp_format("~~strike~~") == "~strike~"


def test_to_whatsapp_format_italic_unchanged() -> None:
    assert to_whatsapp_format("_italic_") == "_italic_"


def test_to_whatsapp_format_mixed() -> None:
    assert to_whatsapp_format("**a** and ~~b~~") == "*a* and ~b~"


def test_from_whatsapp_format_bold() -> None:
    assert from_whatsapp_format("*bold*") == "**bold**"


def test_from_whatsapp_format_strikethrough() -> None:
    assert from_whatsapp_format("~strike~") == "~~strike~~"


def test_from_whatsapp_format_does_not_double_convert() -> None:
    # Already-double markers should not get any extra characters.
    assert from_whatsapp_format("**bold**") == "**bold**"
    assert from_whatsapp_format("~~strike~~") == "~~strike~~"


# ---------------------------------------------------------------------------
# WhatsAppFormatConverter.to_ast
# ---------------------------------------------------------------------------


def test_to_ast_plain_text() -> None:
    ast = WhatsAppFormatConverter().to_ast("Hello world")
    assert ast["type"] == "root"
    assert len(ast["children"]) > 0


def test_to_ast_whatsapp_bold() -> None:
    ast = WhatsAppFormatConverter().to_ast("*bold text*")
    assert ast["type"] == "root"


def test_to_ast_italic() -> None:
    ast = WhatsAppFormatConverter().to_ast("_italic text_")
    assert ast["type"] == "root"


def test_to_ast_whatsapp_strike() -> None:
    ast = WhatsAppFormatConverter().to_ast("~strikethrough~")
    assert ast["type"] == "root"


def test_to_ast_does_not_merge_bold_across_newlines() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("*bold1*\nsome text\n*bold2*")
    rendered = converter.from_ast(ast)
    assert "*bold1*" in rendered
    assert "*bold2*" in rendered


def test_to_ast_code_block() -> None:
    ast = WhatsAppFormatConverter().to_ast("```\ncode\n```")
    assert ast["type"] == "root"


def test_to_ast_lists() -> None:
    ast = WhatsAppFormatConverter().to_ast("- item 1\n- item 2\n- item 3")
    assert ast["type"] == "root"


# ---------------------------------------------------------------------------
# WhatsAppFormatConverter.from_ast
# ---------------------------------------------------------------------------


def test_from_ast_simple_text() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("Hello world")
    assert "Hello world" in converter.from_ast(ast)


def test_from_ast_standard_bold_to_whatsapp_bold() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("**bold text**")
    result = converter.from_ast(ast)
    assert "*bold text*" in result
    assert "**bold text**" not in result


def test_from_ast_standard_strike_to_whatsapp_strike() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("~~strikethrough~~")
    result = converter.from_ast(ast)
    assert "~strikethrough~" in result
    assert "~~strikethrough~~" not in result


def test_render_postable_italic() -> None:
    result = WhatsAppFormatConverter().render_postable({"markdown": "_italic text_"})
    assert "_italic text_" in result
    assert "*italic text*" not in result


def test_render_postable_bold_and_italic() -> None:
    result = WhatsAppFormatConverter().render_postable(
        {"markdown": "**bold** and _italic_"},
    )
    assert "*bold*" in result
    assert "_italic_" in result


def test_from_ast_heading_becomes_bold() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("# Main heading")
    result = converter.from_ast(ast)
    assert "*Main heading*" in result
    assert "#" not in result


def test_from_ast_flattens_bold_inside_heading() -> None:
    result = WhatsAppFormatConverter().render_postable(
        {"markdown": "## **Choose React if:**"},
    )
    assert "*Choose React if:*" in result
    assert "***" not in result


def test_from_ast_thematic_break_becomes_separator() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("above\n\n---\n\nbelow")
    result = converter.from_ast(ast)
    assert "\u2501\u2501\u2501" in result
    assert "above" in result
    assert "below" in result


def test_from_ast_table_becomes_code_block() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("| A | B |\n| --- | --- |\n| 1 | 2 |")
    result = converter.from_ast(ast)
    assert "```" in result


# ---------------------------------------------------------------------------
# render_postable
# ---------------------------------------------------------------------------


def test_render_postable_plain_string() -> None:
    assert WhatsAppFormatConverter().render_postable("Hello world") == "Hello world"


def test_render_postable_raw() -> None:
    assert WhatsAppFormatConverter().render_postable({"raw": "raw content"}) == ("raw content")


def test_render_postable_markdown() -> None:
    result = WhatsAppFormatConverter().render_postable({"markdown": "**bold** text"})
    assert "*bold*" in result


def test_render_postable_ast() -> None:
    converter = WhatsAppFormatConverter()
    ast = converter.to_ast("Hello from AST")
    result = converter.render_postable({"ast": ast})
    assert "Hello from AST" in result


def test_render_postable_complex_ai_response() -> None:
    markdown = "\n".join(
        [
            "# The Answer: **It Depends!**",
            "",
            "There's no universal *better* choice.",
            "",
            "## **Choose React if:**",
            "- Building **large-scale** apps",
            "- Need the biggest *ecosystem*",
            "- **Examples:** Facebook, Netflix",
            "",
            "## **Choose Vue if:**",
            "- Want *faster* learning curve",
            "- Prefer ~~complex~~ cleaner templates",
            "",
            "---",
            "",
            "## Real Talk:",
            "**All three are excellent.** Learn *React* first!",
        ],
    )
    result = WhatsAppFormatConverter().render_postable({"markdown": markdown})
    expected = (
        "*The Answer: It Depends!*\n"
        "\n"
        "There's no universal _better_ choice.\n"
        "\n"
        "*Choose React if:*\n"
        "\n"
        "- Building *large-scale* apps\n"
        "- Need the biggest _ecosystem_\n"
        "- *Examples:* Facebook, Netflix\n"
        "\n"
        "*Choose Vue if:*\n"
        "\n"
        "- Want _faster_ learning curve\n"
        "- Prefer ~complex~ cleaner templates\n"
        "\n"
        "\u2501\u2501\u2501\n"
        "\n"
        "*Real Talk:*\n"
        "\n"
        "*All three are excellent.* Learn _React_ first!"
    )
    assert result == expected
