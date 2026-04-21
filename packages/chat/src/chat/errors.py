"""Error types for chat-sdk.

Python port of upstream ``packages/chat/src/errors.ts``. The class hierarchy
mirrors upstream exactly so that error ``name``/``code`` values match across
language boundaries.
"""

from __future__ import annotations


class ChatError(Exception):
    """Base error raised by chat-sdk.

    Carries a stable ``code`` string and an optional ``cause``. Subclasses set
    ``code`` in their constructor and override ``name``.
    """

    code: str
    cause: object | None

    def __init__(self, message: str, code: str, cause: object | None = None) -> None:
        super().__init__(message)
        self.name = "ChatError"
        self.code = code
        self.cause = cause

    @property
    def message(self) -> str:
        return self.args[0] if self.args else ""


class RateLimitError(ChatError):
    """Raised when the upstream platform is rate limiting us."""

    retry_after_ms: int | None

    def __init__(
        self,
        message: str,
        retry_after_ms: int | None = None,
        cause: object | None = None,
    ) -> None:
        super().__init__(message, "RATE_LIMITED", cause)
        self.name = "RateLimitError"
        self.retry_after_ms = retry_after_ms


class LockError(ChatError):
    """Raised when a distributed-lock acquire/release fails."""

    def __init__(self, message: str, cause: object | None = None) -> None:
        super().__init__(message, "LOCK_FAILED", cause)
        self.name = "LockError"


class NotImplementedError(ChatError):
    """Raised when an adapter does not implement a requested feature."""

    feature: str | None

    def __init__(
        self,
        message: str,
        feature: str | None = None,
        cause: object | None = None,
    ) -> None:
        super().__init__(message, "NOT_IMPLEMENTED", cause)
        self.name = "NotImplementedError"
        self.feature = feature
