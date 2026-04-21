"""AES-256-GCM token encryption for stored Slack OAuth tokens.

Python port of upstream ``packages/adapter-slack/src/crypto.ts``.

Uses the standard ``cryptography`` library's ``AESGCM`` which matches Node's
``aes-256-gcm`` with a 12-byte IV and a 16-byte auth tag.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, TypedDict, TypeGuard

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_IV_LENGTH = 12
_AUTH_TAG_LENGTH = 16
_HEX_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class EncryptedTokenData(TypedDict):
    """Serialized envelope for a token encrypted with AES-256-GCM."""

    data: str
    iv: str
    tag: str


def encrypt_token(plaintext: str, key: bytes) -> EncryptedTokenData:
    """Encrypt ``plaintext`` using AES-256-GCM with a random 12-byte IV.

    Matches the Node implementation byte-for-byte — ciphertext and auth tag
    are stored separately in base64.
    """

    iv = os.urandom(_IV_LENGTH)
    aesgcm = AESGCM(key)
    # cryptography's AESGCM appends the 16-byte auth tag to the ciphertext.
    combined = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    ciphertext = combined[:-_AUTH_TAG_LENGTH]
    tag = combined[-_AUTH_TAG_LENGTH:]
    return {
        "iv": base64.b64encode(iv).decode("ascii"),
        "data": base64.b64encode(ciphertext).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
    }


def decrypt_token(encrypted: EncryptedTokenData, key: bytes) -> str:
    """Decrypt data produced by :func:`encrypt_token`.

    Raises ``cryptography.exceptions.InvalidTag`` if the auth tag does not
    verify (wrong key, tampered ciphertext, etc.).
    """

    iv = base64.b64decode(encrypted["iv"])
    ciphertext = base64.b64decode(encrypted["data"])
    tag = base64.b64decode(encrypted["tag"])
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext + tag, None)
    return plaintext.decode("utf-8")


def is_encrypted_token_data(value: Any) -> TypeGuard[EncryptedTokenData]:
    """Return True if ``value`` is a dict shaped like :class:`EncryptedTokenData`."""

    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("iv"), str)
        and isinstance(value.get("data"), str)
        and isinstance(value.get("tag"), str)
    )


def decode_key(raw_key: str) -> bytes:
    """Decode a 32-byte encryption key from a hex or base64 string.

    Accepts 64-char hex or 44-char base64 (the Node-style ``key.toString('base64')``).
    Whitespace around the string is trimmed. Raises :class:`ValueError` if the
    decoded key is not exactly 32 bytes.
    """

    trimmed = raw_key.strip()
    is_hex = bool(_HEX_KEY_PATTERN.match(trimmed))
    if is_hex:
        key = bytes.fromhex(trimmed)
    else:
        # base64.b64decode tolerates missing padding in some Python versions,
        # but Node outputs padded base64 for 32-byte keys (44 chars). Accept
        # both strictly and leniently — invalid input raises ValueError below.
        try:
            key = base64.b64decode(trimmed, validate=False)
        except Exception as exc:
            raise ValueError(
                f"Encryption key must decode to exactly 32 bytes (decode failed: {exc})."
            ) from exc
    if len(key) != 32:
        raise ValueError(
            f"Encryption key must decode to exactly 32 bytes "
            f"(received {len(key)}). Use a 64-char hex string or "
            f"44-char base64 string."
        )
    return key


__all__ = [
    "EncryptedTokenData",
    "decode_key",
    "decrypt_token",
    "encrypt_token",
    "is_encrypted_token_data",
]
