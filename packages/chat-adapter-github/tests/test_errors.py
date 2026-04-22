"""Unit tests for :mod:`chat_adapter_github.errors`.

Translates failed :mod:`httpx` responses into typed adapter errors. Upstream
defers to Octokit for this, so these tests are specific to the Python port.
"""

from __future__ import annotations

import time

import httpx
import pytest
from chat_adapter_github.errors import handle_github_error
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
)


def _response(status: int, headers: dict[str, str] | None = None, body: str = "") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers=headers or {},
        content=body.encode("utf-8"),
        request=httpx.Request("GET", "https://api.github.com/foo"),
    )


class TestHandleGitHubError:
    def test_401_raises_authentication_error(self) -> None:
        response = _response(401, body='{"message": "Bad credentials"}')
        with pytest.raises(AuthenticationError) as excinfo:
            handle_github_error(response, "post_message")
        assert "Bad credentials" in str(excinfo.value)

    def test_401_without_body_still_raises(self) -> None:
        response = _response(401)
        with pytest.raises(AuthenticationError):
            handle_github_error(response, "post_message")

    def test_403_with_rate_limit_remaining_zero_raises_rate_limit(self) -> None:
        response = _response(
            403,
            headers={"x-ratelimit-remaining": "0", "retry-after": "60"},
            body='{"message": "API rate limit exceeded"}',
        )
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_github_error(response, "post_message")
        assert excinfo.value.retry_after == 60

    def test_403_without_rate_limit_raises_permission_error(self) -> None:
        response = _response(403, body='{"message": "Forbidden"}')
        with pytest.raises(PermissionError) as excinfo:
            handle_github_error(response, "post_message")
        assert "post_message" in str(excinfo.value)

    def test_404_raises_resource_not_found_error(self) -> None:
        response = _response(404, body='{"message": "Not Found"}')
        with pytest.raises(ResourceNotFoundError) as excinfo:
            handle_github_error(response, "fetch_messages")
        assert "fetch_messages" in str(excinfo.value)

    def test_429_raises_rate_limit_error(self) -> None:
        response = _response(
            429,
            headers={"retry-after": "30"},
            body='{"message": "Too Many Requests"}',
        )
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_github_error(response, "post_message")
        assert excinfo.value.retry_after == 30

    def test_429_rounds_up_fractional_retry_after(self) -> None:
        response = _response(429, headers={"retry-after": "1.4"})
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_github_error(response, "post_message")
        assert excinfo.value.retry_after == 2

    def test_429_ignores_malformed_retry_after(self) -> None:
        response = _response(429, headers={"retry-after": "nope"})
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_github_error(response, "post_message")
        assert excinfo.value.retry_after is None

    def test_429_uses_x_ratelimit_reset_when_retry_after_missing(self) -> None:
        future = int(time.time()) + 42
        response = _response(429, headers={"x-ratelimit-reset": str(future)})
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_github_error(response, "post_message")
        assert excinfo.value.retry_after is not None
        assert excinfo.value.retry_after > 0
        assert excinfo.value.retry_after <= 42

    def test_500_raises_network_error(self) -> None:
        response = _response(500, body='{"message": "Server error"}')
        with pytest.raises(NetworkError) as excinfo:
            handle_github_error(response, "post_message")
        assert "500" in str(excinfo.value)
        assert "Server error" in str(excinfo.value)

    def test_plain_text_error_body_is_preserved(self) -> None:
        response = _response(500, body="Gateway Timeout")
        with pytest.raises(NetworkError) as excinfo:
            handle_github_error(response, "post_message")
        assert "Gateway Timeout" in str(excinfo.value)

    def test_empty_body_is_tolerated(self) -> None:
        response = _response(500)
        with pytest.raises(NetworkError):
            handle_github_error(response, "post_message")
