"""Tests for WhatsApp / Meta Graph API error translation."""

from __future__ import annotations

import pytest
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
)
from chat_adapter_whatsapp import throw_whatsapp_api_error


def test_429_maps_to_rate_limit() -> None:
    with pytest.raises(AdapterRateLimitError):
        throw_whatsapp_api_error(
            "postMessage",
            429,
            {"error": {"message": "Rate limit hit", "code": 80007}},
        )


def test_429_via_error_code_maps_to_rate_limit() -> None:
    with pytest.raises(AdapterRateLimitError):
        throw_whatsapp_api_error(
            "postMessage",
            400,
            {"error": {"message": "Rate limit", "code": 429}},
        )


def test_401_maps_to_authentication_error() -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        throw_whatsapp_api_error(
            "postMessage",
            401,
            {"error": {"message": "Invalid OAuth access token", "code": 190}},
        )
    assert "Invalid OAuth" in str(exc_info.value)


def test_oauth_code_190_maps_to_authentication_error() -> None:
    with pytest.raises(AuthenticationError):
        throw_whatsapp_api_error(
            "postMessage",
            400,
            {"error": {"message": "OAuth token expired", "code": 190}},
        )


def test_session_code_102_maps_to_authentication_error() -> None:
    with pytest.raises(AuthenticationError):
        throw_whatsapp_api_error(
            "postMessage",
            400,
            {"error": {"message": "Session has expired", "code": 102}},
        )


def test_403_maps_to_permission_error() -> None:
    with pytest.raises(PermissionError):
        throw_whatsapp_api_error(
            "postMessage",
            403,
            {"error": {"message": "Forbidden", "code": 200}},
        )


def test_application_code_10_maps_to_permission_error() -> None:
    with pytest.raises(PermissionError):
        throw_whatsapp_api_error(
            "postMessage",
            400,
            {"error": {"message": "Application does not have permission", "code": 10}},
        )


def test_404_maps_to_resource_not_found() -> None:
    with pytest.raises(ResourceNotFoundError):
        throw_whatsapp_api_error(
            "downloadMedia",
            404,
            {"error": {"message": "Object not found", "code": 100}},
        )


def test_other_4xx_maps_to_validation() -> None:
    with pytest.raises(ValidationError) as exc_info:
        throw_whatsapp_api_error(
            "postMessage",
            400,
            {"error": {"message": "Recipient phone number not in allowed list", "code": 131030}},
        )
    assert "Recipient phone number" in str(exc_info.value)


def test_5xx_maps_to_network_error() -> None:
    with pytest.raises(NetworkError) as exc_info:
        throw_whatsapp_api_error(
            "postMessage",
            503,
            {"error": {"message": "Service unavailable", "code": 1}},
        )
    msg = str(exc_info.value)
    assert "Service unavailable" in msg
    assert "status 503" in msg


def test_missing_error_envelope_uses_default_message() -> None:
    with pytest.raises(ValidationError) as exc_info:
        throw_whatsapp_api_error("postMessage", 400, {})
    assert "postMessage" in str(exc_info.value)


def test_error_user_msg_takes_precedence_when_no_message() -> None:
    with pytest.raises(ValidationError) as exc_info:
        throw_whatsapp_api_error(
            "postMessage",
            400,
            {"error": {"error_user_msg": "Friendlier message", "code": 131000}},
        )
    assert "Friendlier message" in str(exc_info.value)
