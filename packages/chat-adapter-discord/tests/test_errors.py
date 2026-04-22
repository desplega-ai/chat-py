"""Tests for the Discord error translation helpers."""

from __future__ import annotations

import httpx
import pytest
from chat_adapter_discord.errors import handle_discord_error
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
)


def _response(
    status: int, *, text: str = "", headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        text=text,
        headers=headers or {},
        request=httpx.Request("POST", "https://discord.com/api/v10/fake"),
    )


class TestHandleDiscordError:
    def test_raises_authentication_error_on_401(self) -> None:
        response = _response(401, text='{"message": "401: Unauthorized"}')
        with pytest.raises(AuthenticationError) as exc_info:
            handle_discord_error(response, "postMessage")
        assert "postMessage" in str(exc_info.value)

    def test_raises_permission_error_on_403(self) -> None:
        response = _response(403, text='{"message": "Missing Permissions"}')
        with pytest.raises(PermissionError) as exc_info:
            handle_discord_error(response, "deleteMessage")
        assert "deleteMessage" in str(exc_info.value)

    def test_raises_resource_not_found_on_404(self) -> None:
        response = _response(404, text='{"message": "Unknown Message"}')
        with pytest.raises(ResourceNotFoundError):
            handle_discord_error(response, "fetchMessage")

    def test_raises_adapter_rate_limit_on_429_with_reset_after_header(self) -> None:
        response = _response(
            429,
            text='{"message": "You are being rate limited."}',
            headers={"X-RateLimit-Reset-After": "2.5"},
        )
        with pytest.raises(AdapterRateLimitError) as exc_info:
            handle_discord_error(response, "postMessage")
        # 2.5s → ceil(2.5) = 3
        assert exc_info.value.retry_after == 3

    def test_rate_limit_falls_back_to_standard_header(self) -> None:
        response = _response(429, text="", headers={"Retry-After": "7"})
        with pytest.raises(AdapterRateLimitError) as exc_info:
            handle_discord_error(response, "postMessage")
        assert exc_info.value.retry_after == 7

    def test_rate_limit_falls_back_to_body_retry_after(self) -> None:
        response = _response(429, text='{"retry_after": 4.1}')
        with pytest.raises(AdapterRateLimitError) as exc_info:
            handle_discord_error(response, "postMessage")
        assert exc_info.value.retry_after == 5

    def test_rate_limit_with_no_retry_after_info(self) -> None:
        response = _response(429, text="")
        with pytest.raises(AdapterRateLimitError) as exc_info:
            handle_discord_error(response, "postMessage")
        assert exc_info.value.retry_after is None

    def test_raises_network_error_on_500(self) -> None:
        response = _response(500, text='{"message": "Internal Server Error"}')
        with pytest.raises(NetworkError) as exc_info:
            handle_discord_error(response, "postMessage")
        assert "500" in str(exc_info.value)
        assert "postMessage" in str(exc_info.value)

    def test_raises_network_error_on_400_validation(self) -> None:
        response = _response(400, text='{"code": 50035, "message": "Invalid Form Body"}')
        with pytest.raises(NetworkError) as exc_info:
            handle_discord_error(response, "postMessage")
        assert "400" in str(exc_info.value)

    def test_handles_non_json_body(self) -> None:
        response = _response(503, text="service unavailable")
        with pytest.raises(NetworkError) as exc_info:
            handle_discord_error(response, "postMessage")
        assert "service unavailable" in str(exc_info.value)
