"""Tests for WhatsApp thread-id encoding / decoding."""

from __future__ import annotations

import pytest
from chat_adapter_shared import ValidationError
from chat_adapter_whatsapp import (
    channel_id_from_thread_id,
    decode_thread_id,
    encode_thread_id,
)


def test_encode_thread_id() -> None:
    assert encode_thread_id({"phoneNumberId": "1234", "userWaId": "9999"}) == ("whatsapp:1234:9999")


def test_decode_thread_id() -> None:
    assert decode_thread_id("whatsapp:1234:9999") == {
        "phoneNumberId": "1234",
        "userWaId": "9999",
    }


def test_decode_rejects_wrong_prefix() -> None:
    with pytest.raises(ValidationError) as exc_info:
        decode_thread_id("slack:C123:T456")
    assert "Invalid WhatsApp thread ID" in str(exc_info.value)


def test_decode_rejects_empty_payload() -> None:
    with pytest.raises(ValidationError):
        decode_thread_id("whatsapp:")


def test_decode_rejects_single_segment() -> None:
    with pytest.raises(ValidationError):
        decode_thread_id("whatsapp:1234")


def test_decode_rejects_too_many_segments() -> None:
    with pytest.raises(ValidationError):
        decode_thread_id("whatsapp:1234:5678:extra")


def test_decode_rejects_empty_phone_number_id() -> None:
    with pytest.raises(ValidationError):
        decode_thread_id("whatsapp::9999")


def test_decode_rejects_empty_user_wa_id() -> None:
    with pytest.raises(ValidationError):
        decode_thread_id("whatsapp:1234:")


def test_roundtrip() -> None:
    original = {"phoneNumberId": "abc", "userWaId": "def"}
    decoded = decode_thread_id(encode_thread_id(original))
    assert decoded == original


def test_channel_id_from_thread_id_returns_thread_id() -> None:
    assert channel_id_from_thread_id("whatsapp:1234:9999") == "whatsapp:1234:9999"


def test_channel_id_from_thread_id_validates() -> None:
    with pytest.raises(ValidationError):
        channel_id_from_thread_id("not-a-whatsapp-thread")
