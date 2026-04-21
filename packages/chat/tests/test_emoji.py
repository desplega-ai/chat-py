"""Tests for :mod:`chat.emoji`, ported from upstream ``emoji.test.ts``."""

from __future__ import annotations

from chat.emoji import (
    DEFAULT_EMOJI_MAP,
    EmojiResolver,
    convert_emoji_placeholders,
    create_emoji,
    default_emoji_resolver,
    emoji,
    get_emoji,
)


class TestFromSlack:
    def test_converts_slack_emoji_to_normalized(self) -> None:
        resolver = EmojiResolver()
        assert resolver.from_slack("+1").name == "thumbs_up"
        assert resolver.from_slack("thumbsup").name == "thumbs_up"
        assert resolver.from_slack("-1").name == "thumbs_down"
        assert resolver.from_slack("heart").name == "heart"
        assert resolver.from_slack("fire").name == "fire"

    def test_handles_colons(self) -> None:
        resolver = EmojiResolver()
        assert resolver.from_slack(":+1:").name == "thumbs_up"
        assert resolver.from_slack(":fire:").name == "fire"

    def test_case_insensitive(self) -> None:
        resolver = EmojiResolver()
        assert resolver.from_slack("FIRE").name == "fire"
        assert resolver.from_slack("Heart").name == "heart"

    def test_unknown_emoji_passes_through(self) -> None:
        resolver = EmojiResolver()
        result = resolver.from_slack("custom_emoji")
        assert result.name == "custom_emoji"
        assert str(result) == "{{emoji:custom_emoji}}"


class TestFromGChat:
    def test_converts_unicode_to_normalized(self) -> None:
        resolver = EmojiResolver()
        assert resolver.from_gchat("👍").name == "thumbs_up"
        assert resolver.from_gchat("👎").name == "thumbs_down"
        assert resolver.from_gchat("❤️").name == "heart"
        assert resolver.from_gchat("🔥").name == "fire"
        assert resolver.from_gchat("🚀").name == "rocket"

    def test_handles_multiple_unicode_variants(self) -> None:
        resolver = EmojiResolver()
        assert resolver.from_gchat("❤").name == "heart"
        assert resolver.from_gchat("❤️").name == "heart"
        assert resolver.from_gchat("✅").name == "check"
        assert resolver.from_gchat("✔️").name == "check"

    def test_unknown_emoji_passes_through(self) -> None:
        resolver = EmojiResolver()
        result = resolver.from_gchat("🦄")
        assert result.name == "🦄"
        assert str(result) == "{{emoji:🦄}}"


class TestFromTeams:
    def test_converts_teams_reactions(self) -> None:
        resolver = EmojiResolver()
        assert resolver.from_teams("like").name == "thumbs_up"
        assert resolver.from_teams("heart").name == "heart"
        assert resolver.from_teams("laugh").name == "laugh"
        assert resolver.from_teams("surprised").name == "surprised"
        assert resolver.from_teams("sad").name == "sad"
        assert resolver.from_teams("angry").name == "angry"

    def test_unknown_passes_through(self) -> None:
        resolver = EmojiResolver()
        result = resolver.from_teams("custom_reaction")
        assert result.name == "custom_reaction"


class TestToSlack:
    def test_converts_normalized_to_slack(self) -> None:
        resolver = EmojiResolver()
        assert resolver.to_slack("thumbs_up") == "+1"
        assert resolver.to_slack("fire") == "fire"
        assert resolver.to_slack("heart") == "heart"

    def test_returns_raw_if_unknown(self) -> None:
        resolver = EmojiResolver()
        assert resolver.to_slack("custom") == "custom"


class TestToGChat:
    def test_converts_normalized_to_gchat(self) -> None:
        resolver = EmojiResolver()
        assert resolver.to_gchat("thumbs_up") == "👍"
        assert resolver.to_gchat("fire") == "🔥"
        assert resolver.to_gchat("rocket") == "🚀"

    def test_returns_raw_if_unknown(self) -> None:
        resolver = EmojiResolver()
        assert resolver.to_gchat("custom") == "custom"


class TestMatches:
    def test_matches_slack_format(self) -> None:
        resolver = EmojiResolver()
        assert resolver.matches("+1", "thumbs_up") is True
        assert resolver.matches("thumbsup", "thumbs_up") is True
        assert resolver.matches(":+1:", "thumbs_up") is True
        assert resolver.matches("fire", "fire") is True

    def test_matches_gchat_format(self) -> None:
        resolver = EmojiResolver()
        assert resolver.matches("👍", "thumbs_up") is True
        assert resolver.matches("🔥", "fire") is True
        assert resolver.matches("❤️", "heart") is True

    def test_does_not_match_different_emoji(self) -> None:
        resolver = EmojiResolver()
        assert resolver.matches("+1", "thumbs_down") is False
        assert resolver.matches("👍", "fire") is False

    def test_matches_unmapped_by_equality(self) -> None:
        resolver = EmojiResolver()
        assert resolver.matches("custom", "custom") is True
        assert resolver.matches("custom", "other") is False


class TestExtend:
    def test_adds_new_mappings(self) -> None:
        resolver = EmojiResolver()
        resolver.extend({"unicorn": {"slack": "unicorn_face", "gchat": "🦄"}})

        assert resolver.from_slack("unicorn_face").name == "unicorn"
        assert resolver.from_gchat("🦄").name == "unicorn"
        assert resolver.to_slack("unicorn") == "unicorn_face"
        assert resolver.to_gchat("unicorn") == "🦄"

    def test_overrides_existing(self) -> None:
        resolver = EmojiResolver()
        resolver.extend({"fire": {"slack": "flames", "gchat": "🔥"}})

        assert resolver.from_slack("flames").name == "fire"
        assert resolver.to_slack("fire") == "flames"


class TestDefaultResolver:
    def test_is_a_resolver(self) -> None:
        assert isinstance(default_emoji_resolver, EmojiResolver)
        assert default_emoji_resolver.from_slack("+1").name == "thumbs_up"


class TestDefaultEmojiMap:
    def test_contains_all_well_known_emoji(self) -> None:
        expected = [
            "thumbs_up",
            "thumbs_down",
            "clap",
            "wave",
            "pray",
            "muscle",
            "ok_hand",
            "point_up",
            "point_down",
            "point_left",
            "point_right",
            "raised_hands",
            "shrug",
            "facepalm",
            "heart",
            "smile",
            "laugh",
            "thinking",
            "sad",
            "cry",
            "angry",
            "love_eyes",
            "cool",
            "wink",
            "surprised",
            "worried",
            "confused",
            "neutral",
            "sleeping",
            "sick",
            "mind_blown",
            "relieved",
            "grimace",
            "rolling_eyes",
            "hug",
            "zany",
            "check",
            "x",
            "question",
            "exclamation",
            "warning",
            "stop",
            "info",
            "100",
            "fire",
            "star",
            "sparkles",
            "lightning",
            "boom",
            "eyes",
            "green_circle",
            "yellow_circle",
            "red_circle",
            "blue_circle",
            "white_circle",
            "black_circle",
            "rocket",
            "party",
            "confetti",
            "balloon",
            "gift",
            "trophy",
            "medal",
            "lightbulb",
            "gear",
            "wrench",
            "hammer",
            "bug",
            "link",
            "lock",
            "unlock",
            "key",
            "pin",
            "memo",
            "clipboard",
            "calendar",
            "clock",
            "hourglass",
            "bell",
            "megaphone",
            "speech_bubble",
            "email",
            "inbox",
            "outbox",
            "package",
            "folder",
            "file",
            "chart_up",
            "chart_down",
            "coffee",
            "pizza",
            "beer",
            "arrow_up",
            "arrow_down",
            "arrow_left",
            "arrow_right",
            "refresh",
            "sun",
            "cloud",
            "rain",
            "snow",
            "rainbow",
        ]

        for name in expected:
            assert name in DEFAULT_EMOJI_MAP
            assert "slack" in DEFAULT_EMOJI_MAP[name]
            assert "gchat" in DEFAULT_EMOJI_MAP[name]


class TestEmojiHelper:
    def test_provides_well_known_values(self) -> None:
        assert emoji.thumbs_up.name == "thumbs_up"
        assert emoji.fire.name == "fire"
        assert emoji.rocket.name == "rocket"
        assert emoji["100"].name == "100"

    def test_str_returns_placeholder(self) -> None:
        assert str(emoji.thumbs_up) == "{{emoji:thumbs_up}}"
        assert str(emoji.fire) == "{{emoji:fire}}"
        assert f"{emoji.rocket}" == "{{emoji:rocket}}"

    def test_object_identity(self) -> None:
        assert emoji.thumbs_up is emoji.thumbs_up
        assert emoji.fire is emoji.fire
        assert get_emoji("thumbs_up") is emoji.thumbs_up

    def test_custom_returns_emoji_value(self) -> None:
        unicorn = emoji.custom("unicorn")
        assert unicorn.name == "unicorn"
        assert str(unicorn) == "{{emoji:unicorn}}"

        custom = emoji.custom("custom_team_emoji")
        assert custom.name == "custom_team_emoji"
        assert f"{custom}" == "{{emoji:custom_team_emoji}}"

    def test_custom_is_idempotent(self) -> None:
        assert emoji.custom("test_emoji") is emoji.custom("test_emoji")


class TestConvertEmojiPlaceholders:
    def test_slack(self) -> None:
        text = f"Thanks! {emoji.thumbs_up} Great work! {emoji.fire}"
        result = convert_emoji_placeholders(text, "slack")
        assert result == "Thanks! :+1: Great work! :fire:"

    def test_gchat(self) -> None:
        text = f"Thanks! {emoji.thumbs_up} Great work! {emoji.fire}"
        result = convert_emoji_placeholders(text, "gchat")
        assert result == "Thanks! 👍 Great work! 🔥"

    def test_teams_unicode(self) -> None:
        text = f"Thanks! {emoji.thumbs_up} Great work! {emoji.fire}"
        result = convert_emoji_placeholders(text, "teams")
        assert result == "Thanks! 👍 Great work! 🔥"

    def test_unknown_placeholder_passes_through(self) -> None:
        text = "Check this {{emoji:unknown_emoji}}!"
        result = convert_emoji_placeholders(text, "slack")
        assert result == "Check this :unknown_emoji:!"

    def test_multiple_emoji(self) -> None:
        text = f"{emoji.wave} Hello! {emoji.smile} How are you? {emoji.thumbs_up}"
        result = convert_emoji_placeholders(text, "gchat")
        assert result == "👋 Hello! 😊 How are you? 👍"

    def test_no_emoji(self) -> None:
        text = "Just a regular message"
        result = convert_emoji_placeholders(text, "slack")
        assert result == "Just a regular message"


class TestCreateEmoji:
    def test_default_well_known(self) -> None:
        e = create_emoji()
        assert e.thumbs_up.name == "thumbs_up"
        assert e.fire.name == "fire"
        assert e.rocket.name == "rocket"
        assert f"{e.thumbs_up}" == "{{emoji:thumbs_up}}"

    def test_custom_method(self) -> None:
        e = create_emoji()
        unicorn = e.custom("unicorn")
        assert unicorn.name == "unicorn"
        assert str(unicorn) == "{{emoji:unicorn}}"

    def test_custom_emoji_as_attributes(self) -> None:
        e = create_emoji(
            {
                "unicorn": {"slack": "unicorn_face", "gchat": "🦄"},
                "company_logo": {"slack": "company", "gchat": "🏢"},
            }
        )
        assert e.unicorn.name == "unicorn"
        assert e.company_logo.name == "company_logo"
        assert f"{e.unicorn}" == "{{emoji:unicorn}}"
        assert f"{e.company_logo}" == "{{emoji:company_logo}}"

        assert e.thumbs_up.name == "thumbs_up"

    def test_custom_emoji_auto_registers_with_resolver(self) -> None:
        e = create_emoji({"custom_test_abc": {"slack": "custom_slack_abc", "gchat": "🎯"}})

        text = f"{e.custom_test_abc} Magic!"
        assert convert_emoji_placeholders(text, "slack") == ":custom_slack_abc: Magic!"
        assert convert_emoji_placeholders(text, "gchat") == "🎯 Magic!"

    def test_returns_shared_singletons(self) -> None:
        e = create_emoji()
        assert e.thumbs_up is emoji.thumbs_up
        assert e.fire is emoji.fire
