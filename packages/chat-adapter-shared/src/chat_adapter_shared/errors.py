"""Standardized error types for chat adapters.

Python port of upstream ``packages/adapter-shared/src/errors.ts``. These error
classes provide a consistent error hierarchy across all platform adapter
implementations (Slack, Teams, Discord, Google Chat, Telegram, GitHub, Linear,
WhatsApp). The class names, ``code`` strings, and ``name`` attributes match
upstream so cross-language consumers see identical wire formats.
"""

from __future__ import annotations


class AdapterError(Exception):
    """Base error class for adapter operations.

    All adapter-specific errors should extend this class.
    """

    adapter: str
    code: str | None

    def __init__(self, message: str, adapter: str, code: str | None = None) -> None:
        super().__init__(message)
        self.name = "AdapterError"
        self.adapter = adapter
        self.code = code

    @property
    def message(self) -> str:
        return self.args[0] if self.args else ""


class AdapterRateLimitError(AdapterError):
    """Rate limit error - thrown when platform API rate limits are hit."""

    retry_after: int | None

    def __init__(self, adapter: str, retry_after: int | None = None) -> None:
        suffix = f", retry after {retry_after}s" if retry_after else ""
        super().__init__(
            f"Rate limited by {adapter}{suffix}",
            adapter,
            "RATE_LIMITED",
        )
        self.name = "AdapterRateLimitError"
        self.retry_after = retry_after


class AuthenticationError(AdapterError):
    """Authentication error - thrown when credentials are invalid or expired."""

    def __init__(self, adapter: str, message: str | None = None) -> None:
        super().__init__(
            message or f"Authentication failed for {adapter}",
            adapter,
            "AUTH_FAILED",
        )
        self.name = "AuthenticationError"


class ResourceNotFoundError(AdapterError):
    """Not found error - thrown when a requested resource doesn't exist."""

    resource_type: str
    resource_id: str | None

    def __init__(
        self,
        adapter: str,
        resource_type: str,
        resource_id: str | None = None,
    ) -> None:
        id_part = f" '{resource_id}'" if resource_id else ""
        super().__init__(
            f"{resource_type}{id_part} not found in {adapter}",
            adapter,
            "NOT_FOUND",
        )
        self.name = "ResourceNotFoundError"
        self.resource_type = resource_type
        self.resource_id = resource_id


class PermissionError(AdapterError):
    """Permission error - thrown when the bot lacks required permissions."""

    action: str
    required_scope: str | None

    def __init__(
        self,
        adapter: str,
        action: str,
        required_scope: str | None = None,
    ) -> None:
        scope_part = f" (requires: {required_scope})" if required_scope else ""
        super().__init__(
            f"Permission denied: cannot {action} in {adapter}{scope_part}",
            adapter,
            "PERMISSION_DENIED",
        )
        self.name = "PermissionError"
        self.action = action
        self.required_scope = required_scope


class ValidationError(AdapterError):
    """Validation error - thrown when input data is invalid."""

    def __init__(self, adapter: str, message: str) -> None:
        super().__init__(message, adapter, "VALIDATION_ERROR")
        self.name = "ValidationError"


class NetworkError(AdapterError):
    """Network error - thrown when there's a network/connectivity issue."""

    original_error: BaseException | None

    def __init__(
        self,
        adapter: str,
        message: str | None = None,
        original_error: BaseException | None = None,
    ) -> None:
        super().__init__(
            message or f"Network error communicating with {adapter}",
            adapter,
            "NETWORK_ERROR",
        )
        self.name = "NetworkError"
        self.original_error = original_error
