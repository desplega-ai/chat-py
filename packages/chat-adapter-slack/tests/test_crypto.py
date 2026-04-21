"""Tests for :mod:`chat_adapter_slack.crypto`."""

from __future__ import annotations

import base64
import os

import pytest
from chat_adapter_slack.crypto import (
    decode_key,
    decrypt_token,
    encrypt_token,
    is_encrypted_token_data,
)
from cryptography.exceptions import InvalidTag

TEST_KEY = os.urandom(32)
TEST_KEY_BASE64 = base64.b64encode(TEST_KEY).decode("ascii")
TEST_KEY_HEX = TEST_KEY.hex()


class TestEncryptDecrypt:
    def test_round_trips_a_token_correctly(self) -> None:
        token = "xoxb-test-bot-token-12345"
        encrypted = encrypt_token(token, TEST_KEY)
        assert decrypt_token(encrypted, TEST_KEY) == token

    def test_produces_different_ciphertexts_for_same_input_random_iv(self) -> None:
        token = "xoxb-same-token"
        a = encrypt_token(token, TEST_KEY)
        b = encrypt_token(token, TEST_KEY)
        assert a["data"] != b["data"]
        assert a["iv"] != b["iv"]

    def test_decryption_with_wrong_key_throws(self) -> None:
        encrypted = encrypt_token("xoxb-secret", TEST_KEY)
        wrong_key = os.urandom(32)
        with pytest.raises(InvalidTag):
            decrypt_token(encrypted, wrong_key)

    def test_decryption_with_tampered_ciphertext_throws(self) -> None:
        encrypted = encrypt_token("xoxb-secret", TEST_KEY)
        encrypted["data"] = base64.b64encode(b"tampered").decode("ascii")
        with pytest.raises(InvalidTag):
            decrypt_token(encrypted, TEST_KEY)


class TestDecodeKey:
    def test_decodes_a_valid_32_byte_base64_key(self) -> None:
        key = decode_key(TEST_KEY_BASE64)
        assert len(key) == 32
        assert key == TEST_KEY

    def test_decodes_a_valid_64_char_hex_key(self) -> None:
        key = decode_key(TEST_KEY_HEX)
        assert len(key) == 32
        assert key == TEST_KEY

    def test_trims_whitespace(self) -> None:
        key = decode_key(f"  {TEST_KEY_BASE64}  ")
        assert len(key) == 32

    def test_throws_for_non_32_byte_key(self) -> None:
        short_key = base64.b64encode(os.urandom(16)).decode("ascii")
        with pytest.raises(ValueError, match="Encryption key must decode to exactly 32 bytes"):
            decode_key(short_key)

    def test_throws_for_empty_string(self) -> None:
        with pytest.raises(ValueError):
            decode_key("")


class TestIsEncryptedTokenData:
    def test_returns_true_for_valid_encrypted_data(self) -> None:
        encrypted = encrypt_token("test", TEST_KEY)
        assert is_encrypted_token_data(encrypted) is True

    def test_returns_false_for_plain_string(self) -> None:
        assert is_encrypted_token_data("xoxb-token") is False

    def test_returns_false_for_none(self) -> None:
        assert is_encrypted_token_data(None) is False

    def test_returns_false_for_object_missing_fields(self) -> None:
        assert is_encrypted_token_data({"iv": "a", "data": "b"}) is False
        assert is_encrypted_token_data({"iv": "a", "tag": "c"}) is False

    def test_returns_false_for_object_with_non_string_fields(self) -> None:
        assert is_encrypted_token_data({"iv": 1, "data": 2, "tag": 3}) is False
