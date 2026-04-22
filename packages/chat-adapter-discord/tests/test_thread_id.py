"""Tests for the Discord thread ID codec.

Mirrors upstream ``packages/adapter-discord/src/index.test.ts`` coverage for
``encodeThreadId`` / ``decodeThreadId`` / ``channelIdFromThreadId`` / ``isDM``.
"""

from __future__ import annotations

import pytest
from chat_adapter_discord.thread_id import (
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
    is_dm,
)
from chat_adapter_shared import ValidationError


class TestEncodeThreadId:
    def test_encodes_guild_channel_thread_id(self) -> None:
        tid = encode_thread_id({"guildId": "111", "channelId": "222"})
        assert tid == "discord:111:222"

    def test_encodes_nested_thread_id(self) -> None:
        tid = encode_thread_id({"guildId": "111", "channelId": "222", "threadId": "333"})
        assert tid == "discord:111:222:333"

    def test_encodes_dm_thread_id(self) -> None:
        tid = encode_thread_id({"guildId": "@me", "channelId": "999"})
        assert tid == "discord:@me:999"


class TestDecodeThreadId:
    def test_decodes_channel_thread_id(self) -> None:
        decoded = decode_thread_id("discord:111:222")
        assert decoded["guildId"] == "111"
        assert decoded["channelId"] == "222"
        assert "threadId" not in decoded

    def test_decodes_nested_thread(self) -> None:
        decoded = decode_thread_id("discord:111:222:333")
        assert decoded["guildId"] == "111"
        assert decoded["channelId"] == "222"
        assert decoded["threadId"] == "333"

    def test_raises_on_missing_prefix(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Discord thread ID"):
            decode_thread_id("slack:111:222")

    def test_raises_on_wrong_segment_count(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Discord thread ID"):
            decode_thread_id("discord:111")

    def test_raises_on_empty_string(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Discord thread ID"):
            decode_thread_id("")


class TestRoundTrip:
    def test_round_trips_channel(self) -> None:
        original = {"guildId": "111", "channelId": "222"}
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["guildId"] == "111"
        assert decoded["channelId"] == "222"

    def test_round_trips_thread(self) -> None:
        original = {
            "guildId": "111",
            "channelId": "222",
            "threadId": "333",
        }
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded == original

    def test_round_trips_dm(self) -> None:
        original = {"guildId": "@me", "channelId": "999"}
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["guildId"] == "@me"
        assert decoded["channelId"] == "999"


class TestIsDm:
    def test_returns_true_for_dm(self) -> None:
        assert is_dm("discord:@me:999") is True

    def test_returns_false_for_guild_channel(self) -> None:
        assert is_dm("discord:111:222") is False

    def test_returns_false_for_guild_thread(self) -> None:
        assert is_dm("discord:111:222:333") is False


class TestChannelIdFromThreadId:
    def test_strips_thread_segment(self) -> None:
        assert channel_id_from_thread_id("discord:111:222:333") == "discord:111:222"

    def test_keeps_channel_id_unchanged(self) -> None:
        assert channel_id_from_thread_id("discord:111:222") == "discord:111:222"

    def test_handles_dm(self) -> None:
        assert channel_id_from_thread_id("discord:@me:999") == "discord:@me:999"
