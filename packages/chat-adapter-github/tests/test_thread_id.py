"""Unit tests for :mod:`chat_adapter_github.thread_id`."""

from __future__ import annotations

import pytest
from chat_adapter_github.thread_id import (
    channel_id_from_thread_id,
    decode_channel_id,
    decode_thread_id,
    encode_thread_id,
)
from chat_adapter_shared import ValidationError


class TestEncodeThreadId:
    def test_encodes_pr_level_thread_id(self) -> None:
        assert (
            encode_thread_id({"owner": "acme", "repo": "app", "prNumber": 123})
            == "github:acme/app:123"
        )

    def test_encodes_review_comment_thread_id(self) -> None:
        assert (
            encode_thread_id(
                {
                    "owner": "acme",
                    "repo": "app",
                    "prNumber": 123,
                    "reviewCommentId": 456789,
                }
            )
            == "github:acme/app:123:rc:456789"
        )

    def test_handles_special_characters_in_repo_names(self) -> None:
        assert (
            encode_thread_id({"owner": "my-org", "repo": "my-cool-app", "prNumber": 42})
            == "github:my-org/my-cool-app:42"
        )

    def test_encodes_issue_thread_id(self) -> None:
        assert (
            encode_thread_id({"owner": "acme", "repo": "app", "prNumber": 10, "type": "issue"})
            == "github:acme/app:issue:10"
        )

    def test_rejects_issue_with_review_comment_id(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            encode_thread_id(
                {
                    "owner": "acme",
                    "repo": "app",
                    "prNumber": 10,
                    "type": "issue",
                    "reviewCommentId": 999,
                }
            )
        assert "Review comments are not supported on issue threads" in str(excinfo.value)


class TestDecodeThreadId:
    def test_decodes_pr_level_thread_id(self) -> None:
        assert decode_thread_id("github:acme/app:123") == {
            "owner": "acme",
            "repo": "app",
            "prNumber": 123,
            "type": "pr",
        }

    def test_decodes_review_comment_thread_id(self) -> None:
        assert decode_thread_id("github:acme/app:123:rc:456789") == {
            "owner": "acme",
            "repo": "app",
            "prNumber": 123,
            "type": "pr",
            "reviewCommentId": 456789,
        }

    def test_decodes_issue_thread_id(self) -> None:
        assert decode_thread_id("github:acme/app:issue:10") == {
            "owner": "acme",
            "repo": "app",
            "prNumber": 10,
            "type": "issue",
        }

    def test_rejects_foreign_prefix(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            decode_thread_id("slack:C123:ts")
        assert "Invalid GitHub thread ID" in str(excinfo.value)

    def test_rejects_malformed_body(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            decode_thread_id("github:invalid")
        assert "Invalid GitHub thread ID format" in str(excinfo.value)

    def test_decodes_repo_names_with_hyphens(self) -> None:
        assert decode_thread_id("github:my-org/my-cool-app:42") == {
            "owner": "my-org",
            "repo": "my-cool-app",
            "prNumber": 42,
            "type": "pr",
        }


class TestThreadIdRoundtrip:
    def test_roundtrips_pr_thread_id(self) -> None:
        original = {
            "owner": "vercel",
            "repo": "next.js",
            "prNumber": 99999,
            "type": "pr",
        }
        assert decode_thread_id(encode_thread_id(original)) == original

    def test_roundtrips_review_comment_thread_id(self) -> None:
        original = {
            "owner": "vercel",
            "repo": "next.js",
            "prNumber": 99999,
            "type": "pr",
            "reviewCommentId": 123456789,
        }
        assert decode_thread_id(encode_thread_id(original)) == original

    def test_roundtrips_issue_thread_id(self) -> None:
        original = {
            "owner": "vercel",
            "repo": "next.js",
            "prNumber": 42,
            "type": "issue",
        }
        assert decode_thread_id(encode_thread_id(original)) == original


class TestChannelIdFromThreadId:
    def test_pr_level_thread(self) -> None:
        assert channel_id_from_thread_id("github:acme/app:42") == "github:acme/app"

    def test_review_comment_thread(self) -> None:
        assert channel_id_from_thread_id("github:acme/app:42:rc:200") == "github:acme/app"

    def test_issue_thread(self) -> None:
        assert channel_id_from_thread_id("github:acme/app:issue:10") == "github:acme/app"


class TestDecodeChannelId:
    def test_decodes_simple_channel(self) -> None:
        assert decode_channel_id("github:acme/app") == ("acme", "app")

    def test_decodes_channel_with_hyphens(self) -> None:
        assert decode_channel_id("github:my-org/my-cool-app") == (
            "my-org",
            "my-cool-app",
        )

    def test_rejects_foreign_prefix(self) -> None:
        with pytest.raises(ValidationError):
            decode_channel_id("slack:C123")

    def test_rejects_missing_slash(self) -> None:
        with pytest.raises(ValidationError):
            decode_channel_id("github:acme")
