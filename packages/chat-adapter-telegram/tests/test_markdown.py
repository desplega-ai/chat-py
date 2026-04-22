"""Tests for Telegram MarkdownV2 conversion."""

from __future__ import annotations

from chat_adapter_telegram import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    TelegramFormatConverter,
    ends_with_orphan_backslash,
    escape_markdown_v2,
    find_unescaped_positions,
    to_bot_api_parse_mode,
    truncate_for_telegram,
)

# --------------------------------------------------------------------------
# Constants + parse mode translation
# --------------------------------------------------------------------------


def test_message_limit_constant() -> None:
    assert TELEGRAM_MESSAGE_LIMIT == 4096


def test_caption_limit_constant() -> None:
    assert TELEGRAM_CAPTION_LIMIT == 1024


def test_to_bot_api_parse_mode_markdown_v2() -> None:
    assert to_bot_api_parse_mode("MarkdownV2") == "MarkdownV2"


def test_to_bot_api_parse_mode_plain_is_none() -> None:
    assert to_bot_api_parse_mode("plain") is None


# --------------------------------------------------------------------------
# escape_markdown_v2
# --------------------------------------------------------------------------


def test_escape_markdown_v2_special_chars() -> None:
    result = escape_markdown_v2("a.b!c(d)")
    assert result == r"a\.b\!c\(d\)"


def test_escape_markdown_v2_no_specials() -> None:
    assert escape_markdown_v2("plain text") == "plain text"


def test_escape_markdown_v2_all_reserved() -> None:
    text = "_*[]()~`>#+-=|{}.!\\"
    escaped = escape_markdown_v2(text)
    # Every char should be prefixed with a backslash.
    assert len(escaped) == 2 * len(text)


# --------------------------------------------------------------------------
# find_unescaped_positions / ends_with_orphan_backslash
# --------------------------------------------------------------------------


def test_find_unescaped_positions_marker_present() -> None:
    assert find_unescaped_positions("abc*def*ghi", "*") == [3, 7]


def test_find_unescaped_positions_escaped_marker() -> None:
    # Even number of backslashes preceding marker ⇒ marker is real.
    assert find_unescaped_positions("a\\\\*", "*") == [3]
    # Odd number ⇒ marker is escaped ⇒ skipped.
    assert find_unescaped_positions("a\\*", "*") == []


def test_ends_with_orphan_backslash_yes() -> None:
    assert ends_with_orphan_backslash("hello\\")


def test_ends_with_orphan_backslash_doubled_is_safe() -> None:
    assert not ends_with_orphan_backslash("hello\\\\")


def test_ends_with_orphan_backslash_no_trailing() -> None:
    assert not ends_with_orphan_backslash("hello")


# --------------------------------------------------------------------------
# truncate_for_telegram
# --------------------------------------------------------------------------


def test_truncate_short_text_unchanged() -> None:
    assert truncate_for_telegram("hi", 100, "plain") == "hi"


def test_truncate_plain_adds_ellipsis() -> None:
    text = "x" * 20
    result = truncate_for_telegram(text, 10, "plain")
    assert result.endswith("...")
    assert len(result) == 10


def test_truncate_markdown_v2_adds_escaped_ellipsis() -> None:
    text = "x" * 200
    result = truncate_for_telegram(text, 20, "MarkdownV2")
    assert result.endswith(r"\.\.\.")
    assert len(result) <= 20


def test_truncate_markdown_v2_drops_orphan_backslash() -> None:
    # A trailing backslash gets stripped, then ellipsis appended.
    text = "a" * 40 + "\\" + "b" * 40
    result = truncate_for_telegram(text, 45, "MarkdownV2")
    # No orphan backslash before the ellipsis.
    assert not ends_with_orphan_backslash(result.removesuffix(r"\.\.\."))


def test_truncate_markdown_v2_balances_entity_delimiters() -> None:
    text = "*bold only opened" + "x" * 200
    result = truncate_for_telegram(text, 30, "MarkdownV2")
    # Unpaired * would be invalid MarkdownV2 — the truncation trims back.
    body = result.removesuffix(r"\.\.\.")
    stars = find_unescaped_positions(body, "*")
    assert len(stars) % 2 == 0


# --------------------------------------------------------------------------
# TelegramFormatConverter
# --------------------------------------------------------------------------


def test_from_markdown_bold() -> None:
    result = TelegramFormatConverter().from_markdown("**bold**")
    assert "*bold*" in result


def test_from_markdown_italic() -> None:
    result = TelegramFormatConverter().from_markdown("*italic*")
    assert "_italic_" in result


def test_from_markdown_strikethrough() -> None:
    result = TelegramFormatConverter().from_markdown("~~strike~~")
    assert "~strike~" in result


def test_from_markdown_inline_code() -> None:
    result = TelegramFormatConverter().from_markdown("`code`")
    assert "`code`" in result


def test_from_markdown_heading_becomes_bold() -> None:
    result = TelegramFormatConverter().from_markdown("# Hello")
    assert result == "*Hello*"


def test_from_markdown_link() -> None:
    result = TelegramFormatConverter().from_markdown("[docs](https://x.com)")
    assert result == "[docs](https://x.com)"


def test_from_markdown_escapes_plain_specials() -> None:
    result = TelegramFormatConverter().from_markdown("a.b!")
    assert r"\." in result
    assert r"\!" in result


def test_from_markdown_code_block() -> None:
    result = TelegramFormatConverter().from_markdown("```\nhello\n```")
    assert result.startswith("```")
    assert result.endswith("```")
    assert "hello" in result


def test_from_markdown_unordered_list() -> None:
    result = TelegramFormatConverter().from_markdown("- one\n- two")
    assert r"\- one" in result
    assert r"\- two" in result


def test_from_markdown_ordered_list() -> None:
    result = TelegramFormatConverter().from_markdown("1. first\n2. second")
    assert r"1\. first" in result
    assert r"2\. second" in result


def test_from_ast_table_folds_to_code_block() -> None:
    ast = {
        "type": "root",
        "children": [
            {
                "type": "table",
                "children": [
                    {
                        "type": "tableRow",
                        "children": [
                            {"type": "tableCell", "children": [{"type": "text", "value": "a"}]},
                            {"type": "tableCell", "children": [{"type": "text", "value": "b"}]},
                        ],
                    },
                    {
                        "type": "tableRow",
                        "children": [
                            {"type": "tableCell", "children": [{"type": "text", "value": "1"}]},
                            {"type": "tableCell", "children": [{"type": "text", "value": "2"}]},
                        ],
                    },
                ],
            },
        ],
    }
    result = TelegramFormatConverter().from_ast(ast)
    assert result.startswith("```")
    assert result.endswith("```")


def test_render_postable_plain_string_passthrough() -> None:
    assert TelegramFormatConverter().render_postable("hello.world") == "hello.world"


def test_render_postable_raw_passthrough() -> None:
    assert TelegramFormatConverter().render_postable({"raw": "raw!text"}) == "raw!text"


def test_render_postable_markdown() -> None:
    result = TelegramFormatConverter().render_postable({"markdown": "**hi**"})
    assert "*hi*" in result


def test_from_ast_blockquote() -> None:
    result = TelegramFormatConverter().from_markdown("> quoted line")
    assert ">" in result
    assert "quoted" in result
