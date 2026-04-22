"""Unit tests for :mod:`chat_adapter_linear.errors`."""

from __future__ import annotations

import httpx
import pytest
from chat_adapter_linear.errors import (
    handle_linear_error,
    handle_linear_graphql_body,
)
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
)


def _response(status: int, headers: dict[str, str] | None = None, body: str = "") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers=headers or {},
        content=body.encode("utf-8"),
        request=httpx.Request("POST", "https://api.linear.app/graphql"),
    )


class TestHandleLinearError:
    def test_401_raises_authentication_error(self) -> None:
        response = _response(401, body='{"error": "invalid_token"}')
        with pytest.raises(AuthenticationError) as excinfo:
            handle_linear_error(response, "post_message")
        assert "invalid_token" in str(excinfo.value)

    def test_401_without_body(self) -> None:
        response = _response(401)
        with pytest.raises(AuthenticationError):
            handle_linear_error(response, "post_message")

    def test_403_raises_permission_error(self) -> None:
        response = _response(403, body='{"error": "forbidden"}')
        with pytest.raises(PermissionError) as excinfo:
            handle_linear_error(response, "post_message")
        assert "post_message" in str(excinfo.value)

    def test_404_raises_resource_not_found(self) -> None:
        response = _response(404, body='{"error": "Not Found"}')
        with pytest.raises(ResourceNotFoundError) as excinfo:
            handle_linear_error(response, "fetch_messages")
        assert "fetch_messages" in str(excinfo.value)

    def test_429_raises_rate_limit_error(self) -> None:
        response = _response(429, headers={"retry-after": "30"})
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_linear_error(response, "post_message")
        assert excinfo.value.retry_after == 30

    def test_429_rounds_up_fractional_retry_after(self) -> None:
        response = _response(429, headers={"retry-after": "1.4"})
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_linear_error(response, "post_message")
        assert excinfo.value.retry_after == 2

    def test_429_ignores_malformed_retry_after(self) -> None:
        response = _response(429, headers={"retry-after": "nope"})
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_linear_error(response, "post_message")
        assert excinfo.value.retry_after is None

    def test_400_raises_validation_error(self) -> None:
        response = _response(400, body='{"error": "bad input"}')
        with pytest.raises(ValidationError) as excinfo:
            handle_linear_error(response, "post_message")
        assert "post_message" in str(excinfo.value)

    def test_500_raises_network_error(self) -> None:
        response = _response(500, body='{"error": "Server error"}')
        with pytest.raises(NetworkError) as excinfo:
            handle_linear_error(response, "post_message")
        assert "500" in str(excinfo.value)

    def test_plain_text_body_is_preserved(self) -> None:
        response = _response(500, body="Gateway Timeout")
        with pytest.raises(NetworkError) as excinfo:
            handle_linear_error(response, "post_message")
        assert "Gateway Timeout" in str(excinfo.value)

    def test_empty_body_is_tolerated(self) -> None:
        response = _response(500)
        with pytest.raises(NetworkError):
            handle_linear_error(response, "post_message")

    def test_graphql_authentication_code(self) -> None:
        body = '{"errors":[{"message":"auth","extensions":{"code":"AUTHENTICATION_ERROR"}}]}'
        response = _response(200, body=body)
        with pytest.raises(AuthenticationError):
            handle_linear_error(response, "op")

    def test_graphql_forbidden_code(self) -> None:
        body = '{"errors":[{"message":"nope","extensions":{"code":"FORBIDDEN"}}]}'
        response = _response(200, body=body)
        with pytest.raises(PermissionError):
            handle_linear_error(response, "op")

    def test_graphql_not_found_code(self) -> None:
        body = '{"errors":[{"message":"nope","extensions":{"code":"NOT_FOUND"}}]}'
        response = _response(200, body=body)
        with pytest.raises(ResourceNotFoundError):
            handle_linear_error(response, "op")

    def test_graphql_rate_limited_code(self) -> None:
        body = '{"errors":[{"message":"slow","extensions":{"code":"RATELIMITED"}}]}'
        response = _response(200, body=body)
        with pytest.raises(AdapterRateLimitError):
            handle_linear_error(response, "op")


class TestHandleLinearGraphqlBody:
    def test_no_errors_returns_none(self) -> None:
        assert handle_linear_graphql_body({"data": {"viewer": {"id": "x"}}}, "op") is None

    def test_unknown_code_raises_network_error(self) -> None:
        body = {"errors": [{"message": "oops", "extensions": {"code": "UNKNOWN"}}]}
        with pytest.raises(NetworkError) as excinfo:
            handle_linear_graphql_body(body, "op")
        assert "oops" in str(excinfo.value)

    def test_authentication_code(self) -> None:
        body = {"errors": [{"message": "no auth", "extensions": {"code": "AUTHENTICATION_ERROR"}}]}
        with pytest.raises(AuthenticationError):
            handle_linear_graphql_body(body, "op")

    def test_forbidden_code(self) -> None:
        body = {"errors": [{"message": "nope", "extensions": {"code": "FORBIDDEN"}}]}
        with pytest.raises(PermissionError):
            handle_linear_graphql_body(body, "op")

    def test_not_found_code(self) -> None:
        body = {"errors": [{"message": "nope", "extensions": {"code": "NOT_FOUND"}}]}
        with pytest.raises(ResourceNotFoundError):
            handle_linear_graphql_body(body, "op")

    def test_rate_limited_code(self) -> None:
        body = {"errors": [{"message": "slow", "extensions": {"code": "RATELIMITED"}}]}
        with pytest.raises(AdapterRateLimitError):
            handle_linear_graphql_body(body, "op")

    def test_validation_code(self) -> None:
        body = {"errors": [{"message": "bad input", "extensions": {"code": "BAD_USER_INPUT"}}]}
        with pytest.raises(ValidationError):
            handle_linear_graphql_body(body, "op")

    def test_non_dict_body_returns_none(self) -> None:
        assert handle_linear_graphql_body("not json", "op") is None
