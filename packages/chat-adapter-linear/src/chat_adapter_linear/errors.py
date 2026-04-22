"""Linear error translation helpers.

Upstream ``packages/adapter-linear/src/index.ts`` relies on ``@linear/sdk``'s
own error classes. This module translates :mod:`httpx` responses (both REST
and GraphQL) into the typed adapter errors from :mod:`chat_adapter_shared`.
"""

from __future__ import annotations

import json
import math
from typing import Any, NoReturn

import httpx
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
)


def _extract_retry_after(response: httpx.Response) -> int | None:
    """Pull a retry-after seconds hint from Linear rate-limit headers."""

    header = response.headers.get("retry-after")
    if header is not None:
        try:
            return max(1, math.ceil(float(header)))
        except (TypeError, ValueError):
            pass
    return None


def _extract_body(response: httpx.Response) -> Any:
    """Best-effort JSON decode of the response body."""

    text = response.text or ""
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text


def _summarize_body(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, str) and message:
                    return message
        for key in ("error_description", "error", "message"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    try:
        return json.dumps(body, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(body)


def _extract_graphql_error_code(body: Any) -> str | None:
    """Pull the first ``errors[].extensions.code`` value from a GraphQL body."""

    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if not isinstance(errors, list):
        return None
    for err in errors:
        if not isinstance(err, dict):
            continue
        extensions = err.get("extensions")
        if isinstance(extensions, dict):
            code = extensions.get("code")
            if isinstance(code, str) and code:
                return code
    return None


def handle_linear_error(response: httpx.Response, operation: str) -> NoReturn:
    """Translate a failed Linear HTTP/GraphQL response into a typed adapter error.

    * ``401`` / ``403`` → :class:`AuthenticationError` or :class:`PermissionError`
    * ``404`` → :class:`ResourceNotFoundError`
    * ``429`` → :class:`AdapterRateLimitError`
    * GraphQL-level ``AUTHENTICATION_ERROR`` / ``FORBIDDEN`` / ``RATELIMITED`` codes
      are also mapped to the equivalent adapter errors.
    * Otherwise → :class:`NetworkError`
    """

    status = response.status_code
    body = _extract_body(response)
    summary = _summarize_body(body)
    gql_code = _extract_graphql_error_code(body)

    if status == 401 or gql_code in {"AUTHENTICATION_ERROR", "UNAUTHENTICATED"}:
        detail = summary or "unauthorized"
        raise AuthenticationError(
            "linear",
            f"Authentication failed for {operation}: {detail}",
        )

    if status == 403 or gql_code == "FORBIDDEN":
        raise PermissionError("linear", operation)

    if status == 404 or gql_code == "NOT_FOUND":
        raise ResourceNotFoundError("linear", operation)

    if status == 429 or gql_code in {"RATELIMITED", "RATE_LIMITED"}:
        retry_after = _extract_retry_after(response)
        raise AdapterRateLimitError("linear", retry_after)

    if status == 400 or gql_code in {"BAD_USER_INPUT", "INVALID_INPUT"}:
        raise ValidationError(
            "linear",
            f"Linear API validation error during {operation}: {summary or status}",
        )

    message = f"Linear API error during {operation}: {status} {summary}".rstrip()
    raise NetworkError("linear", message)


def handle_linear_graphql_body(body: Any, operation: str) -> None:
    """Inspect a 200-response GraphQL body and raise if it carries errors.

    Linear (like most GraphQL APIs) returns HTTP 200 with ``{"errors": [...]}``
    for logical errors. This helper translates those into adapter-typed
    exceptions without needing a non-200 status.
    """

    if not isinstance(body, dict):
        return
    errors = body.get("errors")
    if not errors:
        return

    summary = _summarize_body(body)
    code = _extract_graphql_error_code(body)

    if code in {"AUTHENTICATION_ERROR", "UNAUTHENTICATED"}:
        raise AuthenticationError("linear", f"Authentication failed for {operation}: {summary}")
    if code == "FORBIDDEN":
        raise PermissionError("linear", operation)
    if code == "NOT_FOUND":
        raise ResourceNotFoundError("linear", operation)
    if code in {"RATELIMITED", "RATE_LIMITED"}:
        raise AdapterRateLimitError("linear", None)
    if code in {"BAD_USER_INPUT", "INVALID_INPUT"}:
        raise ValidationError(
            "linear", f"Linear API validation error during {operation}: {summary}"
        )

    raise NetworkError(
        "linear",
        f"Linear GraphQL error during {operation}: {summary}",
    )


__all__ = ["handle_linear_error", "handle_linear_graphql_body"]
