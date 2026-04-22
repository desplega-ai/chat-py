"""Tests for the Teams thread ID codec.

Mirrors upstream ``packages/adapter-teams/src/thread-id`` coverage and relevant
round-trip cases from ``index.test.ts``.
"""

from __future__ import annotations

import pytest
from chat_adapter_shared import ValidationError
from chat_adapter_teams.thread_id import (
    decode_thread_id,
    encode_thread_id,
    is_dm,
)


class TestEncodeThreadId:
    def test_encodes_channel_thread_id(self) -> None:
        tid = encode_thread_id(
            {
                "conversationId": "19:abc123@thread.tacv2",
                "serviceUrl": "https://smba.trafficmanager.net/amer/",
            }
        )
        assert tid.startswith("teams:")
        parts = tid.split(":")
        assert len(parts) == 3

    def test_encodes_dm_conversation_id(self) -> None:
        tid = encode_thread_id(
            {
                "conversationId": "a:1JCCDZHh8e-abcdef-ghijkl-mno",
                "serviceUrl": "https://smba.trafficmanager.net/amer/",
            }
        )
        assert tid.startswith("teams:")

    def test_encodes_thread_reply_conversation_id(self) -> None:
        tid = encode_thread_id(
            {
                "conversationId": "19:abc@thread.tacv2;messageid=1766459963017",
                "serviceUrl": "https://smba.trafficmanager.net/amer/",
            }
        )
        decoded = decode_thread_id(tid)
        assert decoded["conversationId"].endswith(";messageid=1766459963017")

    def test_encodes_service_url_with_special_characters(self) -> None:
        tid = encode_thread_id(
            {
                "conversationId": "19:test@thread.tacv2",
                "serviceUrl": "https://smba.trafficmanager.net/amer/abc-123/",
            }
        )
        decoded = decode_thread_id(tid)
        assert decoded["serviceUrl"] == "https://smba.trafficmanager.net/amer/abc-123/"


class TestDecodeThreadId:
    def test_decodes_channel_thread_id(self) -> None:
        tid = encode_thread_id(
            {
                "conversationId": "19:abc@thread.tacv2",
                "serviceUrl": "https://smba.trafficmanager.net/amer/",
            }
        )
        result = decode_thread_id(tid)
        assert result["conversationId"] == "19:abc@thread.tacv2"
        assert result["serviceUrl"] == "https://smba.trafficmanager.net/amer/"

    def test_raises_on_missing_prefix(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Teams thread ID"):
            decode_thread_id("slack:aGVsbG8:d29ybGQ")

    def test_raises_on_wrong_segment_count(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Teams thread ID"):
            decode_thread_id("teams:aGVsbG8")

    def test_raises_on_empty_string(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Teams thread ID"):
            decode_thread_id("")


class TestRoundTrip:
    def test_round_trips_channel_conversation(self) -> None:
        original = {
            "conversationId": "19:abc123@thread.tacv2",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
        }
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["conversationId"] == original["conversationId"]
        assert decoded["serviceUrl"] == original["serviceUrl"]

    def test_round_trips_dm(self) -> None:
        original = {
            "conversationId": "a:1JCCDZHh8e-xyz",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
        }
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["conversationId"] == original["conversationId"]

    def test_round_trips_reply_with_messageid(self) -> None:
        original = {
            "conversationId": "19:abc@thread.tacv2;messageid=1766459963017",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
        }
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["conversationId"] == original["conversationId"]


class TestIsDm:
    def test_returns_false_for_channel_thread(self) -> None:
        tid = encode_thread_id(
            {
                "conversationId": "19:abc@thread.tacv2",
                "serviceUrl": "https://smba.trafficmanager.net/amer/",
            }
        )
        assert is_dm(tid) is False

    def test_returns_true_for_legacy_dm_conversation(self) -> None:
        tid = encode_thread_id(
            {
                "conversationId": "a:1JCCDZHh8e",
                "serviceUrl": "https://smba.trafficmanager.net/amer/",
            }
        )
        assert is_dm(tid) is True

    def test_treats_graph_chat_id_as_channel_like(self) -> None:
        # Graph chat IDs use the 19: prefix, so is_dm returns False even for
        # resolved DMs. Matches upstream behavior.
        tid = encode_thread_id(
            {
                "conversationId": "19:guid_appid@unq.gbl.spaces",
                "serviceUrl": "https://smba.trafficmanager.net/amer/",
            }
        )
        assert is_dm(tid) is False
