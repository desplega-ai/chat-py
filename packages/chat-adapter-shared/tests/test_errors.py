"""Tests for standardized error types.

Python port of upstream ``packages/adapter-shared/src/errors.test.ts``.
"""

from __future__ import annotations

import pytest
from chat_adapter_shared.errors import (
    AdapterError,
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# AdapterError
# ---------------------------------------------------------------------------


class TestAdapterError:
    def test_creates_error_with_message_adapter_and_code(self) -> None:
        error = AdapterError("Something failed", "slack", "CUSTOM_CODE")
        assert error.message == "Something failed"
        assert error.adapter == "slack"
        assert error.code == "CUSTOM_CODE"
        assert error.name == "AdapterError"

    def test_is_an_instance_of_exception(self) -> None:
        error = AdapterError("test", "slack")
        assert isinstance(error, Exception)

    def test_works_without_code(self) -> None:
        error = AdapterError("test", "teams")
        assert error.code is None


# ---------------------------------------------------------------------------
# AdapterRateLimitError
# ---------------------------------------------------------------------------


class TestAdapterRateLimitError:
    def test_creates_error_with_retry_after(self) -> None:
        error = AdapterRateLimitError("slack", 30)
        assert error.message == "Rate limited by slack, retry after 30s"
        assert error.adapter == "slack"
        assert error.code == "RATE_LIMITED"
        assert error.retry_after == 30
        assert error.name == "AdapterRateLimitError"

    def test_creates_error_without_retry_after(self) -> None:
        error = AdapterRateLimitError("teams")
        assert error.message == "Rate limited by teams"
        assert error.retry_after is None

    def test_is_an_instance_of_adapter_error(self) -> None:
        error = AdapterRateLimitError("slack")
        assert isinstance(error, AdapterError)


# ---------------------------------------------------------------------------
# AuthenticationError
# ---------------------------------------------------------------------------


class TestAuthenticationError:
    def test_creates_error_with_custom_message(self) -> None:
        error = AuthenticationError("slack", "Token expired")
        assert error.message == "Token expired"
        assert error.adapter == "slack"
        assert error.code == "AUTH_FAILED"
        assert error.name == "AuthenticationError"

    def test_creates_error_with_default_message(self) -> None:
        error = AuthenticationError("teams")
        assert error.message == "Authentication failed for teams"

    def test_is_an_instance_of_adapter_error(self) -> None:
        error = AuthenticationError("slack")
        assert isinstance(error, AdapterError)


# ---------------------------------------------------------------------------
# ResourceNotFoundError
# ---------------------------------------------------------------------------


class TestResourceNotFoundError:
    def test_creates_error_with_resource_type_and_id(self) -> None:
        error = ResourceNotFoundError("slack", "channel", "C123456")
        assert error.message == "channel 'C123456' not found in slack"
        assert error.adapter == "slack"
        assert error.code == "NOT_FOUND"
        assert error.resource_type == "channel"
        assert error.resource_id == "C123456"
        assert error.name == "ResourceNotFoundError"

    def test_creates_error_without_resource_id(self) -> None:
        error = ResourceNotFoundError("teams", "user")
        assert error.message == "user not found in teams"
        assert error.resource_id is None

    def test_is_an_instance_of_adapter_error(self) -> None:
        error = ResourceNotFoundError("slack", "thread")
        assert isinstance(error, AdapterError)


# ---------------------------------------------------------------------------
# PermissionError
# ---------------------------------------------------------------------------


class TestPermissionError:
    def test_creates_error_with_action_and_scope(self) -> None:
        error = PermissionError("slack", "send messages", "chat:write")
        assert error.message == (
            "Permission denied: cannot send messages in slack (requires: chat:write)"
        )
        assert error.adapter == "slack"
        assert error.code == "PERMISSION_DENIED"
        assert error.action == "send messages"
        assert error.required_scope == "chat:write"
        assert error.name == "PermissionError"

    def test_creates_error_without_scope(self) -> None:
        error = PermissionError("teams", "delete messages")
        assert error.message == "Permission denied: cannot delete messages in teams"
        assert error.required_scope is None

    def test_is_an_instance_of_adapter_error(self) -> None:
        error = PermissionError("gchat", "test")
        assert isinstance(error, AdapterError)


# ---------------------------------------------------------------------------
# ValidationError
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_creates_error_with_message(self) -> None:
        error = ValidationError("slack", "Message text exceeds 40000 characters")
        assert error.message == "Message text exceeds 40000 characters"
        assert error.adapter == "slack"
        assert error.code == "VALIDATION_ERROR"
        assert error.name == "ValidationError"

    def test_is_an_instance_of_adapter_error(self) -> None:
        error = ValidationError("teams", "Invalid")
        assert isinstance(error, AdapterError)


# ---------------------------------------------------------------------------
# NetworkError
# ---------------------------------------------------------------------------


class TestNetworkError:
    def test_creates_error_with_custom_message(self) -> None:
        error = NetworkError("slack", "Connection timeout after 30s")
        assert error.message == "Connection timeout after 30s"
        assert error.adapter == "slack"
        assert error.code == "NETWORK_ERROR"
        assert error.name == "NetworkError"

    def test_creates_error_with_default_message(self) -> None:
        error = NetworkError("gchat")
        assert error.message == "Network error communicating with gchat"

    def test_can_wrap_original_error(self) -> None:
        original = Exception("ECONNREFUSED")
        error = NetworkError("teams", "Connection refused", original)
        assert error.original_error is original

    def test_is_an_instance_of_adapter_error(self) -> None:
        error = NetworkError("slack")
        assert isinstance(error, AdapterError)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_all_errors_extend_adapter_error(self) -> None:
        errors: list[AdapterError] = [
            AdapterRateLimitError("slack"),
            AuthenticationError("slack"),
            ResourceNotFoundError("slack", "test"),
            PermissionError("slack", "test"),
            ValidationError("slack", "test"),
            NetworkError("slack"),
        ]
        for error in errors:
            assert isinstance(error, AdapterError)
            assert isinstance(error, Exception)

    def test_can_be_caught_by_adapter_name(self) -> None:
        slack_errors: list[AdapterError] = []
        with pytest.raises(AdapterRateLimitError) as exc_info:
            raise AdapterRateLimitError("slack", 30)
        e = exc_info.value
        if isinstance(e, AdapterError) and e.adapter == "slack":
            slack_errors.append(e)
        assert len(slack_errors) == 1
        assert slack_errors[0].adapter == "slack"

    def test_can_be_caught_by_error_code(self) -> None:
        rate_limit_errors: list[AdapterError] = []
        errors: list[AdapterError] = [
            AdapterRateLimitError("slack"),
            AuthenticationError("teams"),
            AdapterRateLimitError("gchat"),
        ]
        for error in errors:
            if error.code == "RATE_LIMITED":
                rate_limit_errors.append(error)
        assert len(rate_limit_errors) == 2
