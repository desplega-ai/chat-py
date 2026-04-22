"""Tests for Telegram thread-id encoding / decoding."""

from __future__ import annotations

import pytest
from chat_adapter_shared import ValidationError
from chat_adapter_telegram import (
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
)


def test_encode_chat_only() -> None:
    assert encode_thread_id({"chatId": "12345"}) == "telegram:12345"


def test_encode_with_topic() -> None:
    result = encode_thread_id({"chatId": "-100500", "messageThreadId": 7})
    assert result == "telegram:-100500:7"


def test_decode_chat_only() -> None:
    assert decode_thread_id("telegram:12345") == {"chatId": "12345"}


def test_decode_with_topic() -> None:
    decoded = decode_thread_id("telegram:-100500:7")
    assert decoded["chatId"] == "-100500"
    assert decoded.get("messageThreadId") == 7


def test_decode_empty_topic_treated_as_chat_only() -> None:
    # Trailing colon produces an empty topic string — upstream tolerates this.
    decoded = decode_thread_id("telegram:42:")
    assert decoded == {"chatId": "42"}


def test_decode_rejects_wrong_prefix() -> None:
    with pytest.raises(ValidationError) as exc_info:
        decode_thread_id("slack:C123")
    assert "Invalid Telegram thread ID" in str(exc_info.value)


def test_decode_rejects_empty_chat_id() -> None:
    with pytest.raises(ValidationError):
        decode_thread_id("telegram:")


def test_decode_rejects_too_many_segments() -> None:
    with pytest.raises(ValidationError):
        decode_thread_id("telegram:a:b:c")


def test_decode_rejects_non_integer_topic() -> None:
    with pytest.raises(ValidationError) as exc_info:
        decode_thread_id("telegram:42:abc")
    assert "topic ID" in str(exc_info.value)


def test_roundtrip_with_topic() -> None:
    original = {"chatId": "99", "messageThreadId": 1234}
    decoded = decode_thread_id(encode_thread_id(original))
    assert decoded["chatId"] == "99"
    assert decoded.get("messageThreadId") == 1234


def test_channel_id_from_thread_id_chat_only() -> None:
    assert channel_id_from_thread_id("telegram:77") == "telegram:77"


def test_channel_id_from_thread_id_drops_topic() -> None:
    assert channel_id_from_thread_id("telegram:-100:42") == "telegram:-100"


def test_channel_id_from_thread_id_invalid() -> None:
    with pytest.raises(ValidationError):
        channel_id_from_thread_id("not-a-telegram-thread")
