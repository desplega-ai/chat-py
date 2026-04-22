"""Tests for Telegram error translation."""

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
from chat_adapter_telegram import throw_telegram_api_error


def test_429_maps_to_rate_limit_with_retry_after() -> None:
    with pytest.raises(AdapterRateLimitError) as exc_info:
        throw_telegram_api_error(
            "sendMessage",
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 13},
            },
        )
    assert getattr(exc_info.value, "retry_after", None) == 13


def test_429_without_retry_after() -> None:
    with pytest.raises(AdapterRateLimitError) as exc_info:
        throw_telegram_api_error(
            "sendMessage",
            429,
            {"ok": False, "error_code": 429},
        )
    assert getattr(exc_info.value, "retry_after", None) is None


def test_401_maps_to_authentication_error() -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        throw_telegram_api_error(
            "getMe",
            401,
            {"ok": False, "error_code": 401, "description": "Unauthorized"},
        )
    assert "Unauthorized" in str(exc_info.value)


def test_403_maps_to_permission_error() -> None:
    with pytest.raises(PermissionError):
        throw_telegram_api_error(
            "sendMessage",
            403,
            {"ok": False, "error_code": 403, "description": "Forbidden"},
        )


def test_404_maps_to_resource_not_found() -> None:
    with pytest.raises(ResourceNotFoundError):
        throw_telegram_api_error(
            "getFile",
            404,
            {"ok": False, "error_code": 404, "description": "Not Found"},
        )


def test_other_4xx_maps_to_validation() -> None:
    with pytest.raises(ValidationError) as exc_info:
        throw_telegram_api_error(
            "sendMessage",
            400,
            {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
        )
    assert "chat not found" in str(exc_info.value)


def test_5xx_maps_to_network_error() -> None:
    with pytest.raises(NetworkError) as exc_info:
        throw_telegram_api_error(
            "sendMessage",
            503,
            {"ok": False, "error_code": 503, "description": "Service Unavailable"},
        )
    msg = str(exc_info.value)
    assert "Service Unavailable" in msg
    assert "status 503" in msg
    assert "error 503" in msg


def test_missing_error_code_falls_back_to_status() -> None:
    with pytest.raises(NetworkError):
        throw_telegram_api_error("sendMessage", 500, {"ok": False})


def test_missing_description_uses_default() -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        throw_telegram_api_error("getMe", 401, {"ok": False, "error_code": 401})
    assert "getMe" in str(exc_info.value)
