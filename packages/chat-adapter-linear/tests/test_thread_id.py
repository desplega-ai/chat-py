"""Unit tests for :mod:`chat_adapter_linear.thread_id`."""

from __future__ import annotations

import pytest
from chat_adapter_linear.thread_id import (
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
)
from chat_adapter_shared import ValidationError


class TestEncodeThreadId:
    def test_encodes_issue_level_thread(self) -> None:
        assert encode_thread_id({"issueId": "abc-123"}) == "linear:abc-123"

    def test_encodes_comment_thread(self) -> None:
        assert (
            encode_thread_id({"issueId": "abc-123", "commentId": "c-1"}) == "linear:abc-123:c:c-1"
        )

    def test_encodes_issue_agent_session_thread(self) -> None:
        assert (
            encode_thread_id({"issueId": "abc-123", "agentSessionId": "s-1"})
            == "linear:abc-123:s:s-1"
        )

    def test_encodes_comment_agent_session_thread(self) -> None:
        assert (
            encode_thread_id({"issueId": "abc-123", "commentId": "c-1", "agentSessionId": "s-1"})
            == "linear:abc-123:c:c-1:s:s-1"
        )

    def test_rejects_missing_issue_id(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            encode_thread_id({})
        assert "issueId is required" in str(excinfo.value)

    def test_rejects_empty_issue_id(self) -> None:
        with pytest.raises(ValidationError):
            encode_thread_id({"issueId": ""})


class TestDecodeThreadId:
    def test_decodes_issue_level(self) -> None:
        assert decode_thread_id("linear:abc-123") == {"issueId": "abc-123"}

    def test_decodes_comment_thread(self) -> None:
        assert decode_thread_id("linear:abc-123:c:c-1") == {
            "issueId": "abc-123",
            "commentId": "c-1",
        }

    def test_decodes_issue_session_thread(self) -> None:
        assert decode_thread_id("linear:abc-123:s:s-1") == {
            "issueId": "abc-123",
            "agentSessionId": "s-1",
        }

    def test_decodes_comment_session_thread(self) -> None:
        assert decode_thread_id("linear:abc-123:c:c-1:s:s-1") == {
            "issueId": "abc-123",
            "commentId": "c-1",
            "agentSessionId": "s-1",
        }

    def test_rejects_foreign_prefix(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            decode_thread_id("slack:C123:ts")
        assert "Invalid Linear thread ID" in str(excinfo.value)

    def test_rejects_empty_body(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            decode_thread_id("linear:")
        assert "Invalid Linear thread ID" in str(excinfo.value)

    def test_rejects_malformed_body(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            decode_thread_id("linear:abc:x:foo")
        assert "Invalid Linear thread ID format" in str(excinfo.value)


class TestRoundtrip:
    def test_roundtrips_issue_level(self) -> None:
        original = {"issueId": "ISSUE-42"}
        assert decode_thread_id(encode_thread_id(original)) == original

    def test_roundtrips_comment(self) -> None:
        original = {"issueId": "ISSUE-42", "commentId": "comment-1"}
        assert decode_thread_id(encode_thread_id(original)) == original

    def test_roundtrips_agent_session_on_issue(self) -> None:
        original = {"issueId": "ISSUE-42", "agentSessionId": "session-xyz"}
        assert decode_thread_id(encode_thread_id(original)) == original

    def test_roundtrips_agent_session_on_comment(self) -> None:
        original = {
            "issueId": "ISSUE-42",
            "commentId": "comment-1",
            "agentSessionId": "session-xyz",
        }
        assert decode_thread_id(encode_thread_id(original)) == original


class TestChannelIdFromThreadId:
    def test_issue_level(self) -> None:
        assert channel_id_from_thread_id("linear:abc-123") == "linear:abc-123"

    def test_comment_thread(self) -> None:
        assert channel_id_from_thread_id("linear:abc-123:c:c-1") == "linear:abc-123"

    def test_issue_session_thread(self) -> None:
        assert channel_id_from_thread_id("linear:abc-123:s:s-1") == "linear:abc-123"

    def test_comment_session_thread(self) -> None:
        assert channel_id_from_thread_id("linear:abc-123:c:c-1:s:s-1") == "linear:abc-123"

    def test_rejects_foreign_prefix(self) -> None:
        with pytest.raises(ValidationError):
            channel_id_from_thread_id("slack:C123:ts")
