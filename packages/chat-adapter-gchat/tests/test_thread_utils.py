"""Tests for the Google Chat thread ID codec.

Mirrors upstream ``packages/adapter-gchat/src/thread-utils.test.ts``.
"""

from __future__ import annotations

import pytest
from chat_adapter_gchat.thread_utils import (
    decode_thread_id,
    encode_thread_id,
    is_dm_thread,
)
from chat_adapter_shared import ValidationError


class TestEncodeThreadId:
    def test_encodes_space_name_only(self) -> None:
        assert encode_thread_id({"spaceName": "spaces/ABC123"}) == "gchat:spaces/ABC123"

    def test_encodes_space_name_with_thread_name(self) -> None:
        tid = encode_thread_id(
            {"spaceName": "spaces/ABC123", "threadName": "spaces/ABC123/threads/xyz"}
        )
        assert tid.startswith("gchat:spaces/ABC123:")
        parts = tid.split(":")
        assert len(parts) >= 3

    def test_adds_dm_suffix_for_dm_threads(self) -> None:
        assert (
            encode_thread_id({"spaceName": "spaces/DM123", "isDM": True}) == "gchat:spaces/DM123:dm"
        )

    def test_adds_dm_suffix_with_thread_name(self) -> None:
        tid = encode_thread_id(
            {
                "spaceName": "spaces/DM123",
                "threadName": "spaces/DM123/threads/t1",
                "isDM": True,
            }
        )
        assert tid.endswith(":dm")


class TestDecodeThreadId:
    def test_decodes_space_only_thread_id(self) -> None:
        result = decode_thread_id("gchat:spaces/ABC123")
        assert result["spaceName"] == "spaces/ABC123"
        assert "threadName" not in result
        assert result["isDM"] is False

    def test_decodes_dm_thread_id(self) -> None:
        result = decode_thread_id("gchat:spaces/DM123:dm")
        assert result["spaceName"] == "spaces/DM123"
        assert result["isDM"] is True

    def test_raises_on_invalid_format(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Google Chat thread ID"):
            decode_thread_id("invalid")

    def test_raises_on_wrong_prefix(self) -> None:
        with pytest.raises(ValidationError, match="Invalid Google Chat thread ID"):
            decode_thread_id("slack:C123:1234")


class TestRoundTrip:
    def test_round_trips_space_only(self) -> None:
        original = {"spaceName": "spaces/ABC"}
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["spaceName"] == original["spaceName"]

    def test_round_trips_with_thread_name(self) -> None:
        original = {
            "spaceName": "spaces/ABC",
            "threadName": "spaces/ABC/threads/xyz",
        }
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["spaceName"] == original["spaceName"]
        assert decoded["threadName"] == original["threadName"]

    def test_round_trips_dm(self) -> None:
        original = {"spaceName": "spaces/DM1", "isDM": True}
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["spaceName"] == original["spaceName"]
        assert decoded["isDM"] is True

    def test_round_trips_with_thread_name_preserves_slashes(self) -> None:
        original = {
            "spaceName": "spaces/XYZ",
            "threadName": "spaces/XYZ/threads/a/b/c",
        }
        decoded = decode_thread_id(encode_thread_id(original))
        assert decoded["threadName"] == original["threadName"]


class TestIsDMThread:
    def test_returns_true_for_dm_thread_ids(self) -> None:
        assert is_dm_thread("gchat:spaces/DM123:dm") is True

    def test_returns_false_for_non_dm_thread_ids(self) -> None:
        assert is_dm_thread("gchat:spaces/ABC123") is False

    def test_returns_false_for_thread_ids_with_dm_in_middle(self) -> None:
        assert is_dm_thread("gchat:dm:spaces/ABC") is False
