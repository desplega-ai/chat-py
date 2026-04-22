"""Tests for :func:`handle_teams_error`.

Mirrors upstream ``packages/adapter-teams/src/errors.test.ts``.
"""

from __future__ import annotations

import pytest
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
)
from chat_adapter_teams.errors import handle_teams_error


class TestHandleTeamsError:
    def test_401_raises_authentication_error(self) -> None:
        with pytest.raises(AuthenticationError):
            handle_teams_error({"statusCode": 401, "message": "Unauthorized"}, "postMessage")

    def test_403_raises_permission_error(self) -> None:
        with pytest.raises(PermissionError):
            handle_teams_error({"statusCode": 403, "message": "Forbidden"}, "postMessage")

    def test_404_raises_network_error(self) -> None:
        with pytest.raises(NetworkError):
            handle_teams_error({"statusCode": 404, "message": "Not found"}, "editMessage")

    def test_429_raises_rate_limit_error(self) -> None:
        with pytest.raises(AdapterRateLimitError):
            handle_teams_error({"statusCode": 429, "retryAfter": 30}, "postMessage")

    def test_inner_http_error_is_consulted(self) -> None:
        with pytest.raises(AuthenticationError):
            handle_teams_error(
                {
                    "innerHttpError": {"statusCode": 401},
                    "message": "Auth failed",
                },
                "postMessage",
            )

    def test_rate_limit_preserves_retry_after(self) -> None:
        with pytest.raises(AdapterRateLimitError) as excinfo:
            handle_teams_error({"statusCode": 429, "retryAfter": 60}, "postMessage")
        assert excinfo.value.retry_after == 60

    def test_permission_message_routes_to_permission_error(self) -> None:
        with pytest.raises(PermissionError):
            handle_teams_error(
                {"message": "Insufficient Permission to complete the operation"},
                "deleteMessage",
            )

    def test_generic_error_with_message_raises_network_error(self) -> None:
        with pytest.raises(NetworkError):
            handle_teams_error({"message": "Connection reset"}, "startTyping")

    def test_unknown_error_type_raises_network_error(self) -> None:
        with pytest.raises(NetworkError):
            handle_teams_error("some string error", "postMessage")

    def test_none_raises_network_error(self) -> None:
        with pytest.raises(NetworkError):
            handle_teams_error(None, "postMessage")

    def test_uses_status_field_when_statuscode_missing(self) -> None:
        with pytest.raises(AuthenticationError):
            handle_teams_error({"status": 401, "message": "Unauthorized"}, "postMessage")

    def test_uses_code_field_when_others_missing(self) -> None:
        with pytest.raises(AdapterRateLimitError):
            handle_teams_error({"code": 429}, "postMessage")
